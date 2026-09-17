"""Importing a dropped folder, or several files at once.

A shapefile is four files and an ISOXML export is a whole folder, so the
single-file upload cannot carry either: the browser has to send the tree.
This module receives that tree, rebuilds it under the session's upload folder
exactly as it was dropped, and decides whether it is one source or several.

The rule for a folder is the one a person would apply. When the folder *is*
one source — a TASKDATA folder, a John Deere card, a folder holding one
shapefile set and nothing else — it becomes one dataset, because its files
only mean something together. Otherwise every top-level item is imported on
its own: each ``.shp`` with its sidecars, each table, each subfolder by the
same rule. A boundary shapefile lying beside a TASKDATA.XML is an item too,
not part of the ISOXML. Whatever could not be imported comes back with the
reason, so a drop never loses a file quietly.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile

from ...core.dataset import Dataset
from ...formats import isoxml as isoxml_mod
from ...formats import johndeere as jd_mod
from ...formats import registry

router = APIRouter(prefix="/api/import", tags=["import"])

#: A shapefile opens without its ``.prj`` (the projection is then assumed),
#: but not without the index and the attribute table.
REQUIRED_SIDECARS = (".shx", ".dbf")

#: Files the operating system drops into folders behind the user's back. They
#: are not the user's data, so they are neither imported nor reported — the
#: file browser (``/api/browse``) hides them for the same reason.
SYSTEM_NAMES = {"__macosx", "thumbs.db", "desktop.ini"}

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


# ==========================================================================
# Paths as the browser sends them
# ==========================================================================

def safe_relative_path(raw: str) -> PurePosixPath:
    """The path a dropped file may be written to, or ``ValueError``.

    The browser is trusted to describe a tree, not to choose a destination:
    a path that climbs out of the upload folder or names an absolute location
    is refused rather than "fixed", because a fix would write somewhere the
    user never pointed at. Backslashes are accepted as separators since a
    Windows browser may send them.
    """
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("A file arrived with an empty path; every file needs its relative path.")
    if "\x00" in text:
        raise ValueError(f"Path {raw!r} contains a NUL character and was refused.")
    if text.startswith("/") or _WINDOWS_DRIVE.match(text):
        raise ValueError(
            f"Path '{raw}' is absolute. Send paths relative to the dropped folder, "
            "such as 'TASKDATA/TASKDATA.XML'."
        )
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError(f"Path '{raw}' names no file.")
    if ".." in parts:
        raise ValueError(
            f"Path '{raw}' climbs out of the dropped folder ('..') and was refused."
        )
    return PurePosixPath(*parts)


def _plan_tree(files: list[UploadFile], paths: list[str]) -> list[PurePosixPath]:
    """Validate every path before a single byte is written.

    A bad path in the middle of a drop would otherwise leave half a tree on
    disk with nothing to say where it came from; refusing the whole request
    up front keeps the upload folder free of orphans.
    """
    from agrosuite.app import server as server_mod

    if not files:
        raise server_mod._fail("No files arrived. Drop a folder or a set of files.")
    if not paths:
        # A plain multi-file drop has no folder: the file names are the tree.
        paths = [Path(item.filename or "").name for item in files]
    if len(paths) != len(files):
        raise server_mod._fail(
            f"{len(files)} file(s) arrived with {len(paths)} path(s). Send one 'paths' "
            "field per file, in the same order as the files."
        )

    relatives: list[PurePosixPath] = []
    try:
        for raw in paths:
            relatives.append(safe_relative_path(raw))
    except ValueError as exc:
        raise server_mod._fail(str(exc))

    seen: set[PurePosixPath] = set()
    for relative in relatives:
        if relative in seen:
            raise server_mod._fail(
                f"'{relative}' appears twice in the drop. Two files cannot share one "
                "path; rename one of them."
            )
        seen.add(relative)

    # 'a' as a file and 'a/b' as another cannot both exist on disk.
    folders = {parent for relative in relatives for parent in relative.parents}
    clash = sorted(str(path) for path in seen & folders)
    if clash:
        raise server_mod._fail(
            f"'{clash[0]}' is sent both as a file and as a folder; the drop cannot be "
            "written as it is."
        )
    return relatives


def _write_tree(root: Path, files: list[UploadFile], relatives: list[PurePosixPath]) -> None:
    from agrosuite.app import server as server_mod

    try:
        for item, relative in zip(files, relatives):
            target = root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as handle:
                shutil.copyfileobj(item.file, handle)
    except OSError as exc:
        # Half a tree would only confuse the next drop; remove what was written.
        shutil.rmtree(root, ignore_errors=True)
        raise server_mod._fail(
            f"Could not store '{relative}': {exc.strerror or exc}. "
            "Check the disk has space and the file name is valid on this system."
        )


# ==========================================================================
# Walking the dropped tree
# ==========================================================================

def _visible(folder: Path) -> list[Path]:
    """The folder's entries, minus what the operating system planted there."""
    return sorted(
        (
            child for child in folder.iterdir()
            if not child.name.startswith(".") and child.name.lower() not in SYSTEM_NAMES
        ),
        key=lambda child: (not child.is_dir(), child.name.lower()),
    )


def _companion_stem(path: Path) -> str | None:
    """The shapefile this file travels with, by stem, or ``None`` for a file
    that stands on its own.

    ArcGIS and QGIS write ``<name>.shp.xml`` beside nearly every export. By
    suffix it is an XML, and read as one it fails as "not ISOXML" — for a
    metadata file that was never meant to be a dataset. It belongs to the
    shapefile, like the ``.dbf``.
    """
    if path.is_dir():
        return None
    if path.name.lower().endswith(".shp.xml"):
        return path.name[: -len(".shp.xml")]
    if path.suffix.lower() in registry.SHAPEFILE_SIDECARS - {".shp"}:
        return path.stem
    return None


def _sibling(folder: Path, stem: str, suffix: str) -> Path | None:
    """A file in ``folder`` with this stem and this suffix, any case."""
    for candidate in folder.iterdir():
        if candidate.is_file() and candidate.stem == stem and candidate.suffix.lower() == suffix:
            return candidate
    return None


def _items(children: list[Path]) -> list[Path]:
    """The entries that count as things to import: subfolders and files that
    are not a shapefile's companions (those travel with their ``.shp``)."""
    return [child for child in children if _companion_stem(child) is None]


def _isoxml_member(path: Path) -> bool:
    """Whether a file in a TASKDATA folder is part of the ISOXML.

    TASKDATA.XML, the TLG/GRD headers and their binaries, and the external
    XML files it references all carry these suffixes; the reader finds them
    from TASKDATA.XML, so none is walked on its own — a TLG header sent to
    the reader would import the same task a second time.
    """
    return path.is_file() and path.suffix.lower() in {".xml", ".bin", ".iso"} \
        and _companion_stem(path) is None


def _entry_point(root: Path) -> tuple[Path, str | None]:
    """Where the import starts, and the name the user knows the drop by.

    The browser sends ``Field 12/TASKDATA/TASKDATA.XML`` when the user drops
    the folder ``Field 12``. The wrapper folders carry the name, but the
    import has to start where the data is, so single-child folders are
    walked through. The walk stops at a folder that is a source in itself:
    a card holding only ``SETUP`` must be opened as the card, not as the
    loose shapefiles inside ``SETUP``. The upload root is a random id and
    names nothing.
    """
    label: str | None = None
    current = root
    while current.is_dir():
        children = _visible(current)
        if len(children) != 1 or _whole_source(current, _items(children)) is not None:
            break
        child = children[0]
        if label is None:
            label = child.name if child.is_dir() else child.stem
        current = child
    return current, label


def _whole_source(folder: Path, items: list[Path]) -> registry.DetectedSource | None:
    """The source this folder is as a whole, or ``None`` to go item by item.

    ``detect`` looks deep — it finds a TASKDATA.XML or a card root anywhere
    below the folder — which is right when the user opens a path, but here a
    deep hit would swallow whatever sits beside it. The folder counts as one
    source only when the anchor is the folder itself; anything deeper is
    reached by the item walk, one level at a time, with its siblings intact.
    """
    try:
        source = registry.detect(folder)
    except (ValueError, FileNotFoundError):
        return None

    if source.kind == "isoxml":
        direct = any(
            child.is_file() and child.name.upper() == "TASKDATA.XML" for child in folder.iterdir()
        )
        return source if direct else None
    if source.kind == "jd_card":
        card_root, _ = jd_mod.find_card_root(folder)
        return source if card_root == folder else None
    if source.kind == "shapefile":
        # A .shp without its index and table is not a source yet; left to the
        # item walk, it is named with what is missing instead of failing in
        # the reader with a path the user never saw.
        if (
            len(items) == 1 and items[0].is_file() and items[0].suffix.lower() == ".shp"
            and all(_sibling(folder, items[0].stem, ext) is not None for ext in REQUIRED_SIDECARS)
        ):
            return source
        return None
    return None


class _Walk:
    """Collects what a drop yields: datasets to register, and what was left."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.found: list[tuple[Dataset, str]] = []
        self.skipped: list[dict[str, str]] = []

    def _name(self, path: Path) -> str:
        # The root is the upload folder, a random id: loose files dropped
        # together have no folder name, and the user knows them as the drop.
        if path == self.root:
            return "the dropped files"
        return path.relative_to(self.root).as_posix()

    def skip(self, path: Path, reason: str) -> None:
        # A reader that fails names the file by the path it was given, which
        # is the upload folder — a temporary location the user never saw.
        # What they know is the path inside the drop, so that is what stays.
        for prefix in (f"{self.root}/", f"{self.root}\\", str(self.root)):
            reason = reason.replace(prefix, "")
        self.skipped.append({"name": self._name(path), "reason": reason})

    # -- one item ----------------------------------------------------------
    def file(self, path: Path) -> None:
        suffix = path.suffix.lower()
        stem = _companion_stem(path)
        if stem is not None:
            if _sibling(path.parent, stem, ".shp") is None:
                self.skip(
                    path,
                    f"'{path.name}' belongs to a shapefile whose .shp is not in the drop. "
                    "Drop the folder holding all of its files (.shp, .shx, .dbf, .prj).",
                )
            # Otherwise it is read together with its .shp; nothing to report.
            return
        if suffix == ".shp":
            missing = [ext for ext in REQUIRED_SIDECARS if _sibling(path.parent, path.stem, ext) is None]
            if missing:
                self.skip(
                    path,
                    f"'{path.name}' arrived without {' and '.join(missing)}. A shapefile is "
                    "several files; drop the folder holding all of them.",
                )
                return
        if suffix not in registry.ALL_IMPORT_EXT:
            self.skip(path, f"Extension '{suffix or path.name}' is not supported on import.")
            return
        try:
            dataset = registry.read_any(path)
        except Exception as exc:
            # QGIS and AgroSuite project files land here too, with the
            # registry's own advice on which button opens them.
            self.skip(path, str(exc))
            return
        self.found.append((dataset, path.stem))

    def folder(self, folder: Path, label: str | None) -> None:
        children = _visible(folder)
        items = _items(children)
        source = _whole_source(folder, items)
        if source is not None:
            self.source(folder, source, items, label)
            if source.kind != "isoxml":
                # A shapefile source is the .shp and its companions, nothing
                # else. A card is one thing down to its last file: the reader
                # inventories the tree and the card panel lists it.
                return
            # A TASKDATA.XML makes the folder one ISOXML source, not the
            # owner of everything lying beside it: the boundary shapefile or
            # the notes exported into the same folder are items of their own.
            children = [child for child in children if not _isoxml_member(child)]
        elif not children:
            where = "The drop" if folder == self.root else "The folder"
            self.skip(folder, f"{where} holds nothing that can be imported.")
            return
        # Every visible entry is walked, not only ``items``: a sidecar whose
        # .shp is missing is worth reporting, and ``file`` knows the difference.
        for child in children:
            if child.is_dir():
                self.folder(child, child.name)
            else:
                self.file(child)

    def source(
        self, folder: Path, source: registry.DetectedSource, items: list[Path], label: str | None
    ) -> None:
        """Read a folder that is one source, and name it by what the user
        dropped — the .shp or the TASKDATA.XML, never the upload folder."""
        anchor = folder
        if source.kind == "shapefile":
            anchor = items[0]
        elif source.kind == "isoxml":
            anchor = next(
                child for child in folder.iterdir()
                if child.is_file() and child.name.upper() == "TASKDATA.XML"
            )
        try:
            dataset = registry.read_any(folder)
        except Exception as exc:
            self.skip(anchor, f"{source.label}: {exc}")
            return
        if source.kind == "shapefile":
            label = items[0].stem
        elif not label or label.upper() == "TASKDATA":
            # 'TASKDATA' names the format, not the field; the reader knows
            # the field's name from the registry inside.
            label = dataset.meta.name
        self.found.append((dataset, label))


def import_tree(root: Path) -> tuple[list[tuple[Dataset, str]], list[dict[str, str]]]:
    """Read everything importable under ``root``: (dataset, label) pairs and skips."""
    start, label = _entry_point(root)
    walk = _Walk(root)
    if start.is_file():
        walk.file(start)
    else:
        walk.folder(start, label)
    return walk.found, walk.skipped


# ==========================================================================
# Endpoint
# ==========================================================================

@router.post("/files")
async def import_files(
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(default=[]),
) -> dict[str, Any]:
    """Import a dropped folder, or several files, in one request.

    ``paths`` runs parallel to ``files`` and carries each file's path inside
    the dropped folder, with forward slashes. Left out, the file names are
    used, which covers a handful of loose files dropped together.
    """
    from agrosuite.app import server as server_mod

    relatives = _plan_tree(files, paths)

    # Each drop gets its own folder: two drops of files with the same names
    # must not overwrite each other, and a shapefile's sidecars must land
    # beside the .shp they belong to and no other.
    state = server_mod.state
    root = state.uploads / uuid.uuid4().hex[:12]
    root.mkdir(parents=True, exist_ok=True)
    _write_tree(root, files, relatives)

    found, skipped = import_tree(root)

    imported: list[dict[str, Any]] = []
    for dataset, label in found:
        try:
            imported.append(server_mod._register(dataset, label, "upload"))
        except Exception as exc:
            skipped.append({"name": label, "reason": f"Read, but could not be registered: {exc}"})

    if not imported:
        reasons = " ".join(
            f"{item['name']}: {item['reason'].rstrip('.')}." for item in skipped[:5]
        )
        raise server_mod._fail(
            "Nothing in the drop could be imported. "
            + (f"{reasons} " if reasons else "")
            + "Drop a folder holding a shapefile (.shp with .shx, .dbf, .prj), a "
            "TASKDATA folder, a John Deere card, or CSV, GeoJSON, KML, Excel or ZIP files."
        )
    return {"imported": imported, "skipped": skipped}
