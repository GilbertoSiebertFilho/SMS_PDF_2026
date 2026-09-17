"""App session state.

Since AgroSuite runs locally and for one person, state lives in memory: the
loaded datasets, the cleaning and analysis results, and the files generated
for export. Nothing is written to disk unless the user asks.

A dataset is never overwritten. Cleaning produces two new datasets — clean
and removed — that live alongside the original. That is what makes it
possible to compare before and after, and to undo a bad cleaning without
re-importing the file.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..core import schema as sch
from ..core.dataset import Dataset

#: Ceiling on points sent to the map in one response. Above it the sampling is
#: systematic, which preserves the spatial pattern without stalling the browser.
MAP_POINT_LIMIT = 60_000


@dataclass
class Entry:
    """One dataset in the session, with its history."""

    id: str
    dataset: Dataset
    label: str
    origin: str = "import"
    parent_id: str | None = None
    reports: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        data = self.dataset.summary()
        data.update({
            "id": self.id,
            "label": self.label,
            "origin": self.origin,
            "parent_id": self.parent_id,
            "has_clean_report": "clean" in self.reports,
            "has_difm_report": "difm" in self.reports,
        })
        return data


class Session:
    """In-memory repository for the session's data."""

    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}
        self._files: dict[str, Path] = {}
        self._lock = threading.Lock()
        self.workdir = Path(tempfile.mkdtemp(prefix="agrosuite_"))
        self.uploads = self.workdir / "uploads"
        self.exports = self.workdir / "exports"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.exports.mkdir(parents=True, exist_ok=True)

    # -- datasets --------------------------------------------------------
    def add(
        self,
        dataset: Dataset,
        label: str | None = None,
        origin: str = "import",
        parent_id: str | None = None,
    ) -> Entry:
        entry = Entry(
            id=uuid.uuid4().hex[:12],
            dataset=dataset,
            label=label or dataset.meta.name,
            origin=origin,
            parent_id=parent_id,
        )
        with self._lock:
            self._entries[entry.id] = entry
        return entry

    def get(self, dataset_id: str) -> Entry:
        entry = self._entries.get(dataset_id)
        if entry is None:
            raise KeyError(f"Dataset '{dataset_id}' is not loaded in this session.")
        return entry

    def remove(self, dataset_id: str) -> None:
        with self._lock:
            self._entries.pop(dataset_id, None)

    def list(self) -> list[dict[str, Any]]:
        return [entry.summary() for entry in self._entries.values()]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # -- files -----------------------------------------------------------
    def register_file(self, path: Path) -> str:
        """Register a generated file and return its download token."""
        token = uuid.uuid4().hex[:16]
        with self._lock:
            self._files[token] = Path(path)
        return token

    def file_for(self, token: str) -> Path:
        path = self._files.get(token)
        if path is None or not path.exists():
            raise KeyError("File not found, or already removed.")
        return path

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Serialization for the map and the charts
# --------------------------------------------------------------------------

def map_payload(
    dataset: Dataset,
    column: str = sch.VALUE,
    limit: int = MAP_POINT_LIMIT,
) -> dict[str, Any]:
    """Prepare the points for drawing on the map.

    It returns parallel arrays rather than GeoJSON: for tens of thousands of
    points, GeoJSON multiplies the response size and the browser's parse time
    several times over for no gain — the drawing happens on a canvas, which
    only needs the coordinates and the value.
    """
    df = dataset.df
    if sch.LON not in df.columns or sch.LAT not in df.columns:
        return {"count": 0, "lon": [], "lat": [], "values": [], "column": column}

    columns = [sch.LON, sch.LAT] + ([column] if column in df.columns else [])
    frame = df[columns].dropna(subset=[sch.LON, sch.LAT])

    total = len(frame)
    if total > limit:
        # Systematic sampling keeps the spatial coverage of the pass, unlike
        # simply taking the first N rows.
        step = int(np.ceil(total / limit))
        frame = frame.iloc[::step]

    values: list[float | None] = []
    if column in frame.columns:
        series = pd.to_numeric(frame[column], errors="coerce")
        values = [None if pd.isna(v) else round(float(v), 4) for v in series]

    payload: dict[str, Any] = {
        "count": int(len(frame)),
        "total": int(total),
        "sampled": bool(total > limit),
        "column": column,
        "lon": [round(float(v), 7) for v in frame[sch.LON]],
        "lat": [round(float(v), 7) for v in frame[sch.LAT]],
        "values": values,
        "bounds": dataset.bounds(),
    }

    if values:
        finite = [v for v in values if v is not None]
        if finite:
            array = np.asarray(finite, dtype="float64")
            payload["scale"] = {
                "min": float(np.min(array)),
                "max": float(np.max(array)),
                # The colour scale uses percentiles: one extreme point should
                # not flatten the contrast of the whole map.
                "low": float(np.percentile(array, 2)),
                "high": float(np.percentile(array, 98)),
            }

    if dataset.geometry is not None:
        payload["polygons"] = _polygon_payload(dataset)

    return payload


def _polygon_payload(dataset: Dataset, limit: int = 4000) -> list[list[list[float]]]:
    """Outer rings of the polygons, for drawing boundaries and grids."""
    rings: list[list[list[float]]] = []
    for geometry in (dataset.geometry or [])[:limit]:
        if geometry is None or geometry.is_empty:
            continue
        parts = geometry.geoms if geometry.geom_type.startswith("Multi") else [geometry]
        for part in parts:
            if part.geom_type == "Polygon":
                rings.append([[round(x, 7), round(y, 7)] for x, y in part.exterior.coords])
            elif part.geom_type == "LineString":
                rings.append([[round(x, 7), round(y, 7)] for x, y in part.coords])
    return rings


def preview_table(dataset: Dataset, rows: int = 25) -> dict[str, Any]:
    """A sample of the table for visual inspection in the interface."""
    frame = dataset.df.head(rows).copy()
    for column in frame.columns:
        if pd.api.types.is_datetime64_any_dtype(frame[column]):
            frame[column] = frame[column].dt.strftime("%Y-%m-%d %H:%M:%S")
    frame = frame.replace({np.nan: None, np.inf: None, -np.inf: None})

    records = []
    for record in frame.to_dict("records"):
        clean: dict[str, Any] = {}
        for key, value in record.items():
            if isinstance(value, (np.integer,)):
                clean[key] = int(value)
            elif isinstance(value, (np.floating,)):
                clean[key] = None if not np.isfinite(value) else round(float(value), 4)
            elif isinstance(value, (np.bool_,)):
                clean[key] = bool(value)
            else:
                clean[key] = value
        records.append(clean)

    return {"columns": list(frame.columns), "rows": records}
