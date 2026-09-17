"""AgroSuite's local server.

The app runs as a server on the machine itself and the interface opens in the
browser. That is deliberate: agricultural data is geospatial, and a real map —
with zoom, satellite imagery and thousands of coloured points — works far
better in a browser than in a widget window.

Nothing leaves the machine. The server listens only on ``127.0.0.1`` and the
files live in a temporary session folder.
"""

from __future__ import annotations

import shutil
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import demo as demo_mod
from ..clean import pipeline as clean_pipeline
from ..clean import steps as clean_steps
from ..core import schema as sch
from ..core import units as units_mod
from ..core.dataset import OPERATION_LABELS, apply_source_units
from ..difm import analysis as difm_analysis
from ..difm import design as difm_design
from ..difm import response as difm_response
from ..formats import augmenta as augmenta_mod
from ..formats import brands as brands_mod
from ..formats import isoxml as isoxml_mod
from ..core import guidance as guidance_mod
from ..formats import johndeere as jd_mod
from ..formats import packages as packages_mod
from ..formats import validate as validate_mod
from ..formats import registry, writers
from . import session as session_mod

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="AgroSuite", version="1.0.0")
state = session_mod.Session()


# ==========================================================================
# Request models
# ==========================================================================

class PathRequest(BaseModel):
    path: str
    brand: str | None = None
    #: Units the source file is in, keyed by canonical column.
    source_units: dict[str, str] = Field(default_factory=dict)
    crop: str | None = None


class DemoRequest(BaseModel):
    kind: str = "harvest"


class UnitsRequest(BaseModel):
    """Re-declaring the units of an already-loaded dataset."""

    source_units: dict[str, str] = Field(default_factory=dict)
    crop: str | None = None


class CleanRequest(BaseModel):
    preset: str | None = None
    value_column: str = sch.VALUE
    corrections: dict[str, Any] = Field(default_factory=dict)
    steps: dict[str, Any] = Field(default_factory=dict)


class DifmRequest(BaseModel):
    rate_column: str = sch.APPLIED_RATE
    value_column: str = sch.VALUE
    crop_price: float = 1.0
    input_cost: float = 0.0
    cell_m: float = 20.0
    edge_margin_m: float = 6.0
    zone_column: str | None = None
    models: list[str] | None = None
    rate_max: float | None = None


class DesignRequest(BaseModel):
    boundary: list[list[float]] | None = None
    boundary_dataset_id: str | None = None
    rates: list[float]
    implement_width_m: float = 12.0
    passes_per_strip: int = 2
    blocks: int = 4
    angle_deg: float | None = None
    buffer_m: float = 0.0
    seed: int = 0


class GuidanceRequest(BaseModel):
    """Building an AB line, from a direction or from two points."""

    boundary: list[list[float]] | None = None
    boundary_dataset_id: str | None = None
    angle_deg: float | None = None
    point_a: list[float] | None = None
    point_b: list[float] | None = None
    name: str = "AB"


class PackageRequest(BaseModel):
    """A complete package to take to the monitor."""

    monitor: str = "generic"
    features: dict[str, Any] | None = None
    boundary: list[list[float]] | None = None
    boundary_dataset_id: str | None = None
    guidance_lines: list[dict[str, Any]] = Field(default_factory=list)
    dataset_id: str | None = None
    rate_property: str = "rate"
    rate_kind: str = "mass"
    rate_unit: str = "kg/ha"
    crop: str | None = None
    cell_m: float = 10.0
    field_name: str = "Field"
    task_name: str = "Prescription"
    product_name: str = "Product"
    customer_name: str = "AgroSuite"
    farm_name: str = "Farm"


class ExportRequest(BaseModel):
    dataset_id: str | None = None
    features: dict[str, Any] | None = None
    formats: list[str] = Field(default_factory=lambda: ["shapefile"])
    brand: str = "generic"
    rate_property: str = "rate"
    rate_kind: str = "mass"
    #: Unit the rate will be **written** in, in the output file.
    rate_unit: str = "kg/ha"
    crop: str | None = None
    cell_m: float = 10.0
    task_name: str = "Prescription"
    field_name: str = "Field"
    product_name: str = "Product"
    columns: list[str] | None = None


# ==========================================================================
# Helpers
# ==========================================================================

def _fail(message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception):  # pragma: no cover - safety net
    """Turn unexpected failures into a readable message rather than a silent 500."""
    if isinstance(exc, HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return JSONResponse(
        {
            "detail": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=6),
        },
        status_code=500,
    )


def _register(
    dataset,
    label: str,
    origin: str,
    parent_id: str | None = None,
    source_units: dict[str, str] | None = None,
    crop: str | None = None,
) -> dict[str, Any]:
    # Units are applied before the derived fields: speed reconstructed from the
    # track already comes out in km/h, and converting it afterwards would break it.
    if source_units:
        apply_source_units(dataset, source_units, crop)
    dataset.ensure_derived()
    entry = state.add(dataset, label=label, origin=origin, parent_id=parent_id)
    return entry.summary()


# ==========================================================================
# Catalogue
# ==========================================================================

@app.get("/api/units")
def units_catalog() -> dict[str, Any]:
    """Catalogue of units and crops for the interface pickers."""
    return units_mod.unit_catalog()


@app.post("/api/datasets/{dataset_id}/units")
def redeclare_units(dataset_id: str, request: UnitsRequest) -> dict[str, Any]:
    """Re-apply source units to an already-loaded dataset.

    This covers the case where the unit only becomes obvious after seeing the
    numbers: an average yield of 55 on a canola map is bu/ac, not kg/ha. The
    conversion produces a new dataset, leaving the original untouched.
    """
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)

    converted = entry.dataset.copy()
    applied = apply_source_units(converted, request.source_units, request.crop)
    if not applied:
        raise _fail(
            "No conversion applies: the chosen units are already the internal ones, "
            "or those columns do not exist in this dataset."
        )
    summary = _register(converted, f"{entry.label} · converted", "units", entry.id)
    return {"dataset": summary, "conversions": applied}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    """Everything the interface needs to build its forms."""
    return {
        "brands": brands_mod.brand_catalog(),
        "operations": OPERATION_LABELS,
        "presets": {
            key: {
                "label": value["label"],
                "description": value["description"],
                "corrections": value["corrections"],
                "steps": value["steps"],
            }
            for key, value in clean_pipeline.PRESETS.items()
        },
        "steps": [
            {
                "key": cls.key,
                "label": cls.label,
                "description": cls.description,
                "defaults": cls.defaults,
            }
            for cls in clean_steps.STEP_CLASSES
        ],
        "response_models": difm_response.MODEL_LABELS,
        "rate_kinds": isoxml_mod.RX_DDI_LABELS,
        "import_extensions": sorted(registry.ALL_IMPORT_EXT),
        "columns": sch.LABELS,
        "monitors": packages_mod.profile_catalog(),
        "artifact_labels": packages_mod.ARTIFACT_LABELS,
        "units": units_mod.unit_catalog(),
        "unit_columns": {k: v for k, v in __import__(
            "agrosuite.core.dataset", fromlist=["SOURCE_UNIT_COLUMNS"]
        ).SOURCE_UNIT_COLUMNS.items()},
    }


# ==========================================================================
# Import
# ==========================================================================

@app.post("/api/import/upload")
async def import_upload(
    file: UploadFile = File(...),
    brand: str | None = None,
    rate_unit: str | None = None,
    speed_unit: str | None = None,
    length_unit: str | None = None,
    crop: str | None = None,
) -> dict[str, Any]:
    """Import a file uploaded from the browser."""
    if not file.filename:
        raise _fail("File has no name.")

    target = state.uploads / file.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as handle:
        shutil.copyfileobj(file.file, handle)

    if target.suffix.lower() == ".shp":
        raise _fail(
            "A shapefile is several files (.shp, .shx, .dbf, .prj), and uploading the "
            ".shp on its own will not open. Zip the folder and upload the zip, or "
            "import by local path instead."
        )

    try:
        dataset = registry.read_any(target, brand)
    except Exception as exc:
        raise _fail(f"Could not read '{file.filename}': {exc}")

    declared = {
        sch.VALUE: rate_unit, sch.TARGET_RATE: rate_unit, sch.APPLIED_RATE: rate_unit,
        sch.SPEED: speed_unit, sch.SWATH: length_unit,
    }
    declared = {k: v for k, v in declared.items() if v}
    return _register(dataset, Path(file.filename).stem, "upload", source_units=declared, crop=crop)


@app.post("/api/import/path")
def import_path(request: PathRequest) -> dict[str, Any]:
    """Import from a path on this machine's disk.

    This is the preferred route for shapefiles and ISOXML folders, which only
    make sense with all of their companion files present.
    """
    path = Path(request.path.strip().strip('"'))
    if not path.exists():
        raise _fail(f"Path not found: {path}")
    try:
        dataset = registry.read_any(path, request.brand)
    except Exception as exc:
        raise _fail(f"Could not read '{path.name}': {exc}")
    return _register(
        dataset, path.stem or path.name, "path",
        source_units=request.source_units, crop=request.crop,
    )


@app.post("/api/import/demo")
def import_demo(request: DemoRequest) -> dict[str, Any]:
    """Load a synthetic dataset for trying the app out."""
    if request.kind == "trial":
        dataset = demo_mod.synthetic_trial()
        label = "DIFM trial (demo)"
    else:
        dataset = demo_mod.synthetic_harvest()
        label = "Harvest with defects (demo)"
    return _register(dataset, label, "demo")


@app.get("/api/browse")
def browse(path: str = "") -> dict[str, Any]:
    """List a folder on disk, so files can be picked without typing a path."""
    target = Path(path).expanduser() if path else Path.home()
    if not target.exists():
        raise _fail(f"Folder not found: {target}")
    if target.is_file():
        target = target.parent

    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith("."):
                continue
            is_dir = child.is_dir()
            importable = is_dir or child.suffix.lower() in registry.ALL_IMPORT_EXT
            if not importable:
                continue
            entries.append({
                "name": child.name,
                "path": str(child),
                "is_dir": is_dir,
                "size": child.stat().st_size if not is_dir else None,
            })
    except PermissionError:
        raise _fail(f"No permission to list: {target}")

    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "entries": entries[:500],
    }


@app.get("/api/inspect")
def inspect(path: str) -> dict[str, Any]:
    """Quick inspection of a path, before importing."""
    try:
        return registry.inspect(path)
    except Exception as exc:
        raise _fail(str(exc))


# ==========================================================================
# Datasets
# ==========================================================================

@app.get("/api/datasets")
def list_datasets() -> dict[str, Any]:
    return {"datasets": state.list()}


@app.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id: str) -> dict[str, Any]:
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)
    data = entry.summary()
    data["preview"] = session_mod.preview_table(entry.dataset)
    data["reports"] = {k: True for k in entry.reports}
    return data


@app.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: str) -> dict[str, Any]:
    state.remove(dataset_id)
    return {"ok": True}


@app.get("/api/datasets/{dataset_id}/map")
def dataset_map(dataset_id: str, column: str = sch.VALUE) -> dict[str, Any]:
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)
    return session_mod.map_payload(entry.dataset, column)


@app.get("/api/datasets/{dataset_id}/stats")
def dataset_stats(dataset_id: str, column: str = sch.VALUE) -> dict[str, Any]:
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)
    return {"column": column, "stats": entry.dataset.stats(column)}


@app.get("/api/datasets/{dataset_id}/report/{kind}")
def dataset_report(dataset_id: str, kind: str) -> dict[str, Any]:
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)
    report = entry.reports.get(kind)
    if report is None:
        raise _fail(f"This dataset has no '{kind}' report.", 404)
    return report


# ==========================================================================
# Cleaning
# ==========================================================================

@app.post("/api/datasets/{dataset_id}/clean")
def clean_dataset(dataset_id: str, request: CleanRequest) -> dict[str, Any]:
    """Run the cleaning and return the report with the resulting datasets."""
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)

    if request.steps:
        config = {"corrections": request.corrections, "steps": request.steps}
    else:
        preset_key = request.preset or clean_pipeline.preset_for(entry.dataset.meta.operation)
        preset = clean_pipeline.PRESETS.get(preset_key)
        if preset is None:
            raise _fail(f"Preset '{preset_key}' does not exist.")
        config = {
            "corrections": {**preset["corrections"], **request.corrections},
            "steps": preset["steps"],
        }

    try:
        result = clean_pipeline.run(entry.dataset, config, request.value_column)
    except Exception as exc:
        raise _fail(f"Cleaning failed: {exc}")

    clean_summary = _register(result.clean, f"{entry.label} · clean", "clean", entry.id)
    removed_summary = None
    if len(result.removed):
        removed_summary = _register(
            result.removed, f"{entry.label} · removed", "clean_removed", entry.id
        )

    state.get(clean_summary["id"]).reports["clean"] = result.report
    entry.reports["clean"] = result.report

    return {
        "report": result.report,
        "clean": clean_summary,
        "removed": removed_summary,
        "config": config,
    }


# ==========================================================================
# DIFM analysis
# ==========================================================================

@app.post("/api/datasets/{dataset_id}/difm")
def difm(dataset_id: str, request: DifmRequest) -> dict[str, Any]:
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)
    try:
        report = difm_analysis.analyze(
            entry.dataset,
            rate_column=request.rate_column,
            value_column=request.value_column,
            crop_price=request.crop_price,
            input_cost=request.input_cost,
            cell_m=request.cell_m,
            edge_margin_m=request.edge_margin_m,
            zone_column=request.zone_column,
            models=request.models,
            rate_max=request.rate_max,
        )
    except Exception as exc:
        raise _fail(f"DIFM analysis failed: {exc}")
    entry.reports["difm"] = report
    return report


@app.post("/api/datasets/{dataset_id}/augmenta")
def augmenta_report(dataset_id: str) -> dict[str, Any]:
    """Vigour against rate, specific to Augmenta data."""
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)
    report = augmenta_mod.vigor_rate_summary(entry.dataset)
    entry.reports["augmenta"] = report
    return report


# ==========================================================================
# Trial layout
# ==========================================================================

@app.post("/api/design")
def design(request: DesignRequest) -> dict[str, Any]:
    """Generate the strip layout for a DIFM trial."""
    boundary: list[tuple[float, float]] | None = None

    if request.boundary_dataset_id:
        try:
            entry = state.get(request.boundary_dataset_id)
        except KeyError as exc:
            raise _fail(str(exc), 404)
        boundary = _boundary_from_dataset(entry.dataset)
        if boundary is None:
            raise _fail(
                "The chosen dataset has no usable field boundary. Import a boundary "
                "shapefile, or draw the field on the map."
            )
    elif request.boundary:
        boundary = [(float(p[0]), float(p[1])) for p in request.boundary]

    if not boundary:
        raise _fail("Give the field boundary to lay the trial out on.")

    try:
        return difm_design.design_strips(
            boundary_lonlat=boundary,
            rates=request.rates,
            implement_width_m=request.implement_width_m,
            passes_per_strip=request.passes_per_strip,
            blocks=request.blocks,
            angle_deg=request.angle_deg,
            buffer_m=request.buffer_m,
            seed=request.seed,
        )
    except Exception as exc:
        raise _fail(str(exc))


def _boundary_from_dataset(dataset) -> list[tuple[float, float]] | None:
    """Extract a boundary from the dataset.

    The order of preference matters: a boundary declared in the file is the
    field's real limit, while the convex hull of the points is only the
    envelope of where the machine drove — and it inflates headlands and detours.
    """
    from shapely.geometry import MultiPoint
    from shapely.ops import unary_union

    setup = dataset.meta.extra.get("field_setup")
    for fld in (setup or {}).get("fields", []):
        for rings in fld.get("boundaries", []):
            exterior = next((r for r in rings if r.get("type") == 1), None)
            if exterior and len(exterior["points"]) >= 3:
                return [(float(x), float(y)) for x, y in exterior["points"]]

    if dataset.geometry:
        polygons = [g for g in dataset.geometry if g is not None and not g.is_empty]
        if polygons:
            merged = unary_union(polygons)
            if merged.geom_type == "MultiPolygon":
                merged = max(merged.geoms, key=lambda g: g.area)
            if merged.geom_type == "Polygon":
                return [(float(x), float(y)) for x, y in merged.exterior.coords]

    if sch.LON in dataset.df.columns:
        frame = dataset.df[[sch.LON, sch.LAT]].dropna()
        if len(frame) >= 3:
            hull = MultiPoint(list(zip(frame[sch.LON], frame[sch.LAT]))).convex_hull
            if hull.geom_type == "Polygon":
                return [(float(x), float(y)) for x, y in hull.exterior.coords]
    return None


# ==========================================================================
# Export
# ==========================================================================


@app.get("/api/datasets/{dataset_id}/setup")
def dataset_setup(dataset_id: str) -> dict[str, Any]:
    """A dataset's boundary and guidance lines, as GeoJSON.

    This is what allows importing setup from one monitor and sending it to
    another: the boundary and the AB lines cross the app without any
    reconstruction.
    """
    try:
        entry = state.get(dataset_id)
    except KeyError as exc:
        raise _fail(str(exc), 404)

    setup = entry.dataset.meta.extra.get("field_setup")
    if not setup:
        boundary = _boundary_from_dataset(entry.dataset)
        if not boundary:
            return {"available": False, "reason": "This dataset carries no boundary and no AB lines."}
        setup = {"fields": [{
            "name": entry.dataset.meta.field_name or entry.label,
            "boundaries": [[{"type": 1, "points": boundary}]],
            "headlands": [], "obstacles": [], "guidance_lines": [],
        }]}
        derived = True
    else:
        derived = False

    fields = []
    for fld in setup.get("fields", []):
        outer = None
        for rings in fld.get("boundaries", []):
            exterior = next((r for r in rings if r.get("type") == 1), None)
            if exterior:
                outer = exterior["points"]
                break
        fields.append({
            "name": fld.get("name"),
            "area_m2": fld.get("area_m2"),
            "boundary": outer,
            "guidance_lines": fld.get("guidance_lines", []),
            "guidance_geojson": guidance_mod.ab_lines_to_geojson(
                fld.get("guidance_lines", [])
            ),
        })

    return {
        "available": bool(fields),
        "derived_from_points": derived,
        "fields": fields,
    }


@app.post("/api/guidance")
def make_guidance(request: GuidanceRequest) -> dict[str, Any]:
    """Create an AB line from a direction, or from two points."""
    if request.point_a and request.point_b:
        try:
            line = guidance_mod.ab_line_from_points(
                (request.point_a[0], request.point_a[1]),
                (request.point_b[0], request.point_b[1]),
                request.name,
            )
        except ValueError as exc:
            raise _fail(str(exc))
        return {"line": line, "geojson": guidance_mod.ab_lines_to_geojson([line])}

    boundary = _resolve_boundary(request.boundary, request.boundary_dataset_id)
    if request.angle_deg is None:
        raise _fail("Give the line's direction, or points A and B.")
    try:
        line = guidance_mod.ab_line_from_direction(boundary, request.angle_deg, request.name)
    except ValueError as exc:
        raise _fail(str(exc))
    return {"line": line, "geojson": guidance_mod.ab_lines_to_geojson([line])}


@app.post("/api/export/package")
def export_package(request: PackageRequest) -> dict[str, Any]:
    """Assemble the full package for the chosen monitor and return the ZIP."""
    profile = packages_mod.get_profile(request.monitor)

    boundary = None
    if request.boundary or request.boundary_dataset_id:
        boundary = _resolve_boundary(request.boundary, request.boundary_dataset_id)

    dataset = None
    if request.dataset_id:
        try:
            dataset = state.get(request.dataset_id).dataset
        except KeyError as exc:
            raise _fail(str(exc), 404)

    features = request.features
    notes: list[str] = []
    if features:
        features, note = _convert_features_rate(
            features, request.rate_property, request.rate_kind,
            request.rate_unit, request.crop,
        )
        if note:
            notes.append(note)

    index = len(list(state.exports.iterdir())) + 1
    out_dir = state.exports / f"pacote_{profile.key}_{index}"

    try:
        result = writers.build_package(
            out_dir,
            monitor=request.monitor,
            prescription=features,
            boundary=boundary,
            guidance_lines=request.guidance_lines or None,
            dataset=dataset,
            rate_property=request.rate_property,
            rate_kind=request.rate_kind,
            rate_unit=request.rate_unit,
            cell_m=request.cell_m,
            field_name=request.field_name,
            task_name=request.task_name,
            product_name=request.product_name,
            customer_name=request.customer_name,
            farm_name=request.farm_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _fail(f"Failed to assemble the package: {exc}")

    if not result["contents"]:
        raise _fail(
            f"Nothing was generated for the {profile.label}. Check that a prescription, "
            "a boundary or AB lines are selected."
        )

    # Verification runs over the files just written, not over what we intended
    # to write: that is the difference between trusting the code and checking
    # the result. The ZIP is only assembled afterwards.
    verification = validate_mod.validate_package(out_dir, request.monitor)

    zip_path = state.exports / f"{out_dir.name}.zip"
    bundle_info = writers.bundle([out_dir], zip_path)
    token = state.register_file(zip_path)

    return {
        **result,
        "verification": verification,
        "observacoes": notes,
        "bundle": {
            "entries": bundle_info["entries"],
            "download_url": f"/api/download/{token}",
            "filename": zip_path.name,
        },
    }


def _resolve_boundary(
    explicit: list[list[float]] | None,
    dataset_id: str | None,
) -> list[tuple[float, float]]:
    """Resolve the boundary coming from the map or from a loaded dataset."""
    if explicit:
        return [(float(p[0]), float(p[1])) for p in explicit]
    if dataset_id:
        try:
            entry = state.get(dataset_id)
        except KeyError as exc:
            raise _fail(str(exc), 404)
        boundary = _boundary_from_dataset(entry.dataset)
        if boundary:
            return boundary
    raise _fail("Give the field boundary, or pick a dataset that carries one.")


#: Unit group matching each ISOXML rate kind.
RATE_KIND_GROUP = {"mass": "rate_mass", "volume": "rate_volume", "count": "rate_count"}


def _convert_features_rate(
    features: dict[str, Any],
    rate_property: str,
    rate_kind: str,
    rate_unit: str,
    crop: str | None,
) -> tuple[dict[str, Any], str | None]:
    """Convert the features' rate to the chosen output unit.

    ISOXML is the exception: the standard fixes the unit of each DDI, so the
    grid is always written from the internal value. Only shapefile, CSV and
    GeoJSON carry the unit the user asked for.
    """
    group = RATE_KIND_GROUP.get(rate_kind, "rate_mass")
    internal = units_mod.UNIT_GROUPS[group]["internal"]
    if not rate_unit or rate_unit == internal:
        return features, None

    try:
        factor = units_mod.unit_factor(group, rate_unit, crop)
    except ValueError as exc:
        raise _fail(str(exc))
    if not factor:
        return features, None

    converted = {
        "type": "FeatureCollection",
        "features": [
            {
                **feature,
                "properties": {
                    **(feature.get("properties") or {}),
                    rate_property: (
                        round(float((feature.get("properties") or {})[rate_property]) / factor, 4)
                        if (feature.get("properties") or {}).get(rate_property) is not None
                        else None
                    ),
                },
            }
            for feature in features.get("features", [])
        ],
    }
    return converted, f"Rate converted from {internal} to {rate_unit} when written."


@app.post("/api/export")
def export(request: ExportRequest) -> dict[str, Any]:
    """Generate the requested files and return the download links."""
    outputs: list[dict[str, Any]] = []
    generated: list[Path] = []
    out_dir = state.exports / f"export_{len(list(state.exports.iterdir())) + 1}"
    out_dir.mkdir(parents=True, exist_ok=True)

    base = "".join(c for c in request.task_name if c.isalnum() or c in "_- ").strip() or "export"
    base = base.replace(" ", "_")

    notes: list[str] = []
    features_internal = request.features
    features_out = request.features
    if request.features:
        features_out, note = _convert_features_rate(
            request.features, request.rate_property, request.rate_kind,
            request.rate_unit, request.crop,
        )
        if note:
            notes.append(note)

    try:
        if request.features:
            for fmt in request.formats:
                if fmt == "shapefile":
                    info = writers.write_prescription_shapefile(
                        features_out, out_dir / f"{base}.shp",
                        brand=request.brand, rate_property=request.rate_property,
                        rate_unit=request.rate_unit, product=request.product_name,
                    )
                    generated.append(Path(info["path"]))
                elif fmt == "isoxml":
                    # Sempre a partir do valor interno: o DDI do padrão já
                    # define a unidade gravada no binário.
                    info = writers.write_prescription_isoxml(
                        features_internal, out_dir,
                        rate_property=request.rate_property, rate_kind=request.rate_kind,
                        cell_m=request.cell_m, task_name=request.task_name,
                        field_name=request.field_name, product_name=request.product_name,
                    )
                    generated.append(Path(info["path"]))
                elif fmt == "csv":
                    info = writers.write_prescription_csv(
                        features_out, out_dir / f"{base}.csv",
                        rate_property=request.rate_property,
                    )
                    generated.append(Path(info["path"]))
                elif fmt == "geojson":
                    info = writers.write_geojson(features_out, out_dir / f"{base}.geojson")
                    generated.append(Path(info["path"]))
                else:
                    raise _fail(f"Unsupported prescription format: '{fmt}'.")
                outputs.append({"format": fmt, **info})

        elif request.dataset_id:
            entry = state.get(request.dataset_id)
            export_dataset = entry.dataset
            group = RATE_KIND_GROUP.get(request.rate_kind, "rate_mass")
            internal = units_mod.UNIT_GROUPS[group]["internal"]
            if request.rate_unit and request.rate_unit != internal:
                factor = units_mod.unit_factor(group, request.rate_unit, request.crop)
                export_dataset = entry.dataset.copy()
                for column in (sch.VALUE, sch.TARGET_RATE, sch.APPLIED_RATE):
                    if column in export_dataset.df.columns:
                        export_dataset.df[column] = export_dataset.df[column] / factor
                notes.append(
                    f"Rate columns converted from {internal} to {request.rate_unit}."
                )
            for fmt in request.formats:
                if fmt == "shapefile":
                    info = writers.write_vector(
                        export_dataset, out_dir / f"{base}.shp", columns=request.columns
                    )
                elif fmt == "geojson":
                    info = writers.write_vector(
                        export_dataset, out_dir / f"{base}.geojson",
                        driver="GeoJSON", columns=request.columns,
                    )
                elif fmt == "gpkg":
                    info = writers.write_vector(
                        export_dataset, out_dir / f"{base}.gpkg",
                        driver="GPKG", columns=request.columns,
                    )
                elif fmt == "csv":
                    info = writers.write_csv(
                        export_dataset, out_dir / f"{base}.csv", columns=request.columns
                    )
                else:
                    raise _fail(f"Unsupported data format: '{fmt}'.")
                generated.append(Path(info["path"]))
                outputs.append({"format": fmt, **info})
        else:
            raise _fail("Give a dataset or a prescription to export.")

    except HTTPException:
        raise
    except KeyError as exc:
        raise _fail(str(exc), 404)
    except Exception as exc:
        raise _fail(f"Export failed: {exc}")

    zip_path = out_dir / f"{base}.zip"
    bundle_info = writers.bundle(generated, zip_path)
    token = state.register_file(zip_path)

    return {
        "outputs": outputs,
        "notes": notes,
        "bundle": {
            "entries": bundle_info["entries"],
            "download_url": f"/api/download/{token}",
            "filename": zip_path.name,
        },
        "folder": str(out_dir),
    }


@app.post("/api/validate")
def validate_folder(path: str) -> dict[str, Any]:
    """Check a package already written to disk, including from another session."""
    target = Path(path).expanduser()
    if not target.exists():
        raise _fail(f"Folder not found: {target}")
    return validate_mod.validate_package(target, "generic")


@app.get("/api/inspect/card")
def inspect_card(path: str) -> dict[str, Any]:
    """Inventory a John Deere card without importing anything."""
    target = Path(path).expanduser()
    if not target.exists():
        raise _fail(f"Path not found: {target}")
    inv = jd_mod.inventory(target)
    return {**inv.to_dict(), "layers": jd_mod.readable_layers(inv)}


@app.get("/api/download/{token}")
def download(token: str):
    try:
        path = state.file_for(token)
    except KeyError as exc:
        raise _fail(str(exc), 404)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


# ==========================================================================
# Interface
# ==========================================================================

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise _fail("Interface not found. Reinstall the app.", 500)
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "datasets": len(state.list()), "workdir": str(state.workdir)}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
