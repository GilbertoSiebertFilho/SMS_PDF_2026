"""Project file endpoints: save the session, reopen it, start afresh.

The library that writes and reads the file is :mod:`agrosuite.app.persist`;
this module only decides *where* a save goes, *whether* an existing file may
be replaced, and what to tell the user when either goes wrong. It also
remembers which file the session came from or last went to, so that
re-saving it asks nothing and reloading the page does not forget it, and it
looks at a file before the session is given up for it.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from .. import persist as persist_mod
from .. import session as session_mod

router = APIRouter(prefix="/api/session", tags=["session"])


class SaveRequest(BaseModel):
    """Where to save, and what to call the project.

    ``name`` becomes the project's name as part of the save rather than
    through a separate request: a save that is refused must leave the
    project as it was, or the panel, the report title and the file would
    disagree about what the project is called.
    """

    name: str | None = None
    path: str | None = None
    #: Replacing an existing file is a decision, not a default: the interface
    #: asks first. The one exception is the file the session was opened from
    #: or last saved to, which is re-saved without asking.
    overwrite: bool = False


class OpenRequest(BaseModel):
    path: str


def _strip_quotes(raw: str) -> str:
    # Paths pasted from a file manager often arrive quoted.
    return raw.strip().strip('"').strip("'")


def _clean_path(raw: str) -> Path:
    return Path(_strip_quotes(raw)).expanduser()


def _save_target(chosen: Path, slug: str) -> Path:
    """The file a typed 'Save to' means, with its folder in place.

    One rule, the same in the dialog's hint: a path ending in ``.agrosuite``
    is the file; anything else is a folder, and the file inside it is named
    after the project. Whether the folder exists yet does not enter into it
    — the earlier rule looked, and a folder typed before it was created
    quietly became a file of that name in its parent. A missing folder is
    created instead: nothing can be overwritten by making one.

    Relative paths are refused. The server resolves them against wherever
    the app was started from, a place the person cannot see and did not
    choose; the file would land somewhere only a search would find.
    """
    from agrosuite.app import server as server_mod

    if not chosen.is_absolute():
        raise server_mod._fail(
            f"'{chosen}' is a relative path, and the app cannot tell where it is "
            "meant from. Give the full path of a folder, from the drive or the root "
            "(e.g. C:\\Data\\Projects or /home/you/projects)."
        )
    if chosen.suffix.lower() == persist_mod.EXTENSION:
        target, folder = chosen, chosen.parent
    else:
        target, folder = chosen / f"{slug}{persist_mod.EXTENSION}", chosen

    if folder.exists() and not folder.is_dir():
        raise server_mod._fail(
            f"'{folder}' is a file, not a folder. To save as a file, end the path in "
            f"{persist_mod.EXTENSION}; otherwise give a folder."
        )
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise server_mod._fail(
            f"Could not create the folder '{folder}': {exc.strerror or exc}. "
            "Check the path, and that its parent folder is writable."
        )
    return target


def _is_current(state: session_mod.Session, target: Path) -> bool:
    current = state.project_file
    return current is not None and current["path"] == target


def _add_to_recent(path: Path, saved_at: str | None) -> str | None:
    """Put ``path`` on the recent list; the warning to show if that failed.

    The list is a convenience kept under the home folder. A home folder that
    cannot be written — a read-only profile, a full disk, AGROSUITE_HOME
    pointing at a file — must not turn a save that succeeded, or an open
    that has already replaced the session, into an error the interface
    treats as a failure of the whole action.
    """
    try:
        persist_mod.remember_recent(path, saved_at)
    except OSError as exc:
        return (
            f"The file is fine, but the recent list could not be updated: {exc}. "
            "Point AGROSUITE_HOME at a writable folder to keep the list."
        )
    return None


def _file_payload(state: session_mod.Session) -> dict[str, Any] | None:
    """The open file as the interface shows it, in one shape for every reply."""
    current = state.project_file
    if current is None:
        return None
    path: Path = current["path"]
    token = current.get("token")
    return {
        "path": str(path),
        "name": path.stem,
        "folder": str(path.parent),
        "saved_at": current.get("saved_at"),
        # A file in the session's own folder goes when the app closes; the
        # save dialog must not propose that folder as a place to keep work.
        "on_disk": not path.is_relative_to(state.workdir.resolve()),
        "download_url": f"/api/download/{token}" if token else None,
        # Decides what the next save proposes: a file named after the project
        # keeps following the name (the folder), one saved under a name of
        # its own is re-saved as it is (the full path).
        "named_after_project": path.stem == persist_mod.slugify(state.project["name"]),
    }


@router.post("/save")
def save_session(request: SaveRequest) -> dict[str, Any]:
    """Write the session to a ``.agrosuite`` file and remember it as recent."""
    from agrosuite.app import server as server_mod

    state = server_mod.state
    if not state.list():
        raise server_mod._fail(
            "Nothing to save yet: the session holds no datasets. Open a file first."
        )

    name = (request.name or "").strip() or state.project["name"]
    slug = persist_mod.slugify(name)
    chosen = _strip_quotes(request.path or "")
    if chosen:
        target = _save_target(Path(chosen).expanduser(), slug)
    else:
        target = state.exports / f"{slug}{persist_mod.EXTENSION}"
    # Resolved once, here: the status line, the recent list and the check
    # for "the file that is open" all compare paths, and '..', a trailing
    # separator or a symlinked folder would make one file look like two.
    target = target.resolve()

    if target.exists() and not request.overwrite and not _is_current(state, target):
        raise server_mod._fail(
            f"'{target}' already exists. Choose another name, or confirm replacing it.",
            409,
        )

    saved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    previous_name = state.project["name"]
    state.project["name"] = name
    try:
        result = persist_mod.save_session(state, target, saved_at)
    except (OSError, ValueError) as exc:
        # Nothing was saved, so nothing changes — including the name.
        state.project["name"] = previous_name
        if isinstance(exc, OSError):
            raise server_mod._fail(
                f"Could not write '{target}': {exc.strerror or exc}. "
                "Check that the folder is writable and the disk has space."
            )
        raise server_mod._fail(str(exc))

    token = state.register_file(target)
    state.project_file = {"path": target, "saved_at": saved_at, "token": token}
    return {
        "path": str(target),
        "name": target.stem,
        "download_url": f"/api/download/{token}",
        "datasets": result["datasets"],
        "saved_at": saved_at,
        "size_bytes": result["size_bytes"],
        "file": _file_payload(state),
        "warning": _add_to_recent(target, saved_at),
    }


def _peek(path: Path) -> dict[str, Any]:
    """What a project file holds, or why it cannot be opened — without
    loading it.

    Opening replaces the session, so the interface asks first; but a
    question about a path that will then fail ("closes the 3 datasets ...
    File not found") is worse than no question. The file browser puts the
    folder in the box when one is clicked, which made that the common case.
    The manifest is a small member of the ZIP, cheap to read on its own.
    """
    if path.is_dir():
        return {"ok": False, "reason": (
            f"That is a folder; choose the {persist_mod.EXTENSION} file inside it: {path}"
        )}
    if not path.exists():
        return {"ok": False, "reason": f"File not found: {path}"}
    try:
        manifest = persist_mod.read_manifest(path)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    except OSError as exc:
        return {"ok": False, "reason": (
            f"Could not read '{path.name}': {exc.strerror or exc}. Check that the file "
            "is readable and, on a removable drive, that the drive is still there."
        )}
    return {
        "ok": True,
        "path": str(path.resolve()),
        "name": path.stem,
        "project": (manifest.get("project") or {}).get("name"),
        "saved_at": manifest.get("saved_at"),
        "datasets": len(manifest["datasets"]),
    }


@router.get("/peek")
def peek_session(path: str) -> dict[str, Any]:
    """Describe a project file before opening it: ``{ok, name, project,
    saved_at, datasets}``, or ``{ok: false, reason}`` in words the person
    can act on. Never an error status: a refused path is an answer."""
    return _peek(_clean_path(path))


def _open(path: Path, remember: bool) -> dict[str, Any]:
    from agrosuite.app import server as server_mod

    state = server_mod.state
    if path.is_dir():
        raise server_mod._fail(
            f"That is a folder; choose the {persist_mod.EXTENSION} file inside it: {path}"
        )
    if not path.exists():
        raise server_mod._fail(f"File not found: {path}", 404)
    path = path.resolve()
    try:
        result = persist_mod.load_session(state, path)
    except ValueError as exc:
        raise server_mod._fail(str(exc))
    except Exception as exc:
        raise server_mod._fail(f"Could not open '{path.name}': {exc}")

    state.project_file = {"path": path, "saved_at": result["saved_at"], "token": None}
    return {
        "path": str(path),
        "name": path.stem,
        "saved_at": result["saved_at"],
        "project": result["project"],
        "datasets": result["datasets"],
        "design": result["design"],
        "file": _file_payload(state),
        "warning": _add_to_recent(path, result["saved_at"]) if remember else None,
    }


@router.post("/open")
def open_session(request: OpenRequest) -> dict[str, Any]:
    """Replace the session with a project file from this machine's disk."""
    return _open(_clean_path(request.path), remember=True)


@router.post("/upload")
async def upload_session(file: UploadFile = File(...)) -> dict[str, Any]:
    """Replace the session with a project file uploaded from the browser.

    The copy lands in the session's upload folder, which disappears with the
    session, so it is not added to the recent list: a recent entry that
    points nowhere after a restart would only mislead.
    """
    from agrosuite.app import server as server_mod

    if not file.filename:
        raise server_mod._fail("File has no name.")
    name = Path(file.filename).name
    if Path(name).suffix.lower() != persist_mod.EXTENSION:
        raise server_mod._fail(
            f"'{name}' is not a project file. Project files end in "
            f"{persist_mod.EXTENSION}; monitor data is opened from the Data tab."
        )

    state = server_mod.state
    target = state.uploads / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as handle:
        shutil.copyfileobj(file.file, handle)
    return _open(target, remember=False)


@router.get("/recent")
def recent_sessions() -> dict[str, Any]:
    """Recently saved or opened project files, most recent first, and the
    file the session is on now — the interface asks for both on every load,
    which is what lets a reloaded page know where the next save goes."""
    from agrosuite.app import server as server_mod

    return {"recent": persist_mod.recent_files(), "current": _file_payload(server_mod.state)}


@router.post("/new")
def new_session() -> dict[str, Any]:
    """Drop every dataset and put the project back to its defaults.

    Files already generated for download stay reachable: they are on disk
    and the tokens cost nothing, whereas a link that stops working the moment
    a new project starts would surprise anyone who had just exported.
    """
    from agrosuite.app import server as server_mod

    state = server_mod.state
    state.clear()
    state.project = session_mod.default_project()
    return {"ok": True}
