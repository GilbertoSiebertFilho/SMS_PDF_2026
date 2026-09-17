"""Project file endpoints: save the session, reopen it, start afresh.

The library that writes and reads the file is :mod:`agrosuite.app.persist`;
this module only decides *where* a save goes, *whether* an existing file may
be replaced, and what to tell the user when either goes wrong. It also
remembers which file the session came from or last went to, so that
re-saving it asks nothing and reloading the page does not forget it.
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
    if request.path:
        raw = _strip_quotes(request.path)
        target = Path(raw).expanduser()
        # A trailing separator means a folder even when the folder is not
        # there yet; taken as a file path it would produce a file named after
        # the folder, in the folder's parent, under a name nobody chose.
        if raw.endswith(("/", "\\")) or target.is_dir():
            if not target.is_dir():
                raise server_mod._fail(
                    f"Folder not found: {target}. Create it first, or save somewhere else."
                )
            target = target / f"{slug}{persist_mod.EXTENSION}"
        elif target.suffix.lower() != persist_mod.EXTENSION:
            target = target.with_name(target.name + persist_mod.EXTENSION)
        if not target.parent.exists():
            raise server_mod._fail(
                f"Folder not found: {target.parent}. Create it first, or save somewhere else."
            )
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


def _open(path: Path, remember: bool) -> dict[str, Any]:
    from agrosuite.app import server as server_mod

    state = server_mod.state
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
