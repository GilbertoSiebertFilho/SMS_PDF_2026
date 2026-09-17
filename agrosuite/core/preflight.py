"""Preliminary analysis, run the moment a file is opened.

Before anyone decides what to do with a file, they need to know what it is
and whether it is worth trusting. This module answers that in one pass, so
the app can say — without being asked — what came in, what is missing, what
looks wrong, and what the sensible next step is.

Three things it checks that matter most in practice:

**Are the units what they seem?**
    A mean yield of 55 on a canola map is bu/ac, not kg/ha. Reading it as
    kg/ha would put every later number out by a factor of fifty. The check
    compares the observed magnitude against what the operation and crop make
    plausible and names the unit that fits.

**Is anything missing that blocks the next step?**
    Cleaning without speed or swath width loses its best filters. A DIFM
    analysis without an applied rate cannot start at all. Saying so up front
    beats failing three screens later.

**Does the data look like one field, one job?**
    Two fields merged into one file, a fragment of a pass, or a logging gap
    of several hours all change how the numbers should be read.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd

from . import schema as sch
from . import units as units_mod

#: Plausible yield ranges in kg/ha, per crop, for a field that actually grew
#: something. Used only to judge whether the declared unit makes sense — never
#: to alter data.
YIELD_RANGE_KG_HA = {
    "canola": (800, 6_000),
    "wheat": (1_000, 9_000),
    "barley": (1_000, 9_000),
    "oats": (800, 7_000),
    "peas": (800, 6_000),
    "lentils": (500, 4_000),
    "flax": (500, 3_500),
    "rye": (800, 7_000),
    "corn": (3_000, 20_000),
    "soybean": (1_000, 7_000),
    "sorghum": (1_000, 12_000),
    "rice": (2_000, 12_000),
    "sunflower": (800, 5_000),
}
DEFAULT_YIELD_RANGE = (500, 20_000)

#: Plausible input rate ranges in kg/ha for dry fertilizer and seed.
RATE_RANGE_KG_HA = (5.0, 600.0)

#: Operating speed range in km/h for field work.
SPEED_RANGE_KMH = (1.0, 30.0)


@dataclass
class Finding:
    """One observation from the preliminary pass."""

    level: str      # 'ok' | 'warning' | 'alert'
    title: str
    detail: str
    action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(title, detail, action=""):
    return Finding("ok", title, detail, action)


def _warn(title, detail, action=""):
    return Finding("warning", title, detail, action)


def _alert(title, detail, action=""):
    return Finding("alert", title, detail, action)


# ==========================================================================
# Individual checks
# ==========================================================================

def _check_coverage(ds) -> tuple[list[Finding], dict[str, Any]]:
    """Extent, worked area and point density."""
    findings: list[Finding] = []
    bounds = ds.bounds()
    info: dict[str, Any] = {"bounds": bounds}

    if not bounds:
        return [_alert("No coordinates", "The file carries no usable position.",
                       "Check whether the export included latitude and longitude.")], info

    area_ha = ds.area_ha()
    info["area_ha"] = round(area_ha, 2)
    info["rows"] = len(ds)

    if area_ha > 0:
        density = len(ds) / area_ha
        info["points_per_ha"] = round(density, 1)
        if density < 5:
            findings.append(_warn(
                "Sparse data",
                f"{density:.1f} records per hectare. A monitor logging once a second "
                "normally produces hundreds.",
                "It may be an already-aggregated file, or only part of the job.",
            ))
        else:
            findings.append(_ok(
                "Coverage",
                f"{len(ds):,} records over {area_ha:.1f} ha "
                f"({density:.0f} per hectare).".replace(",", " "),
            ))

    # A bounding box far larger than the worked area usually means two fields
    # ended up in the same file.
    if bounds and area_ha > 0:
        west, south, east, north = bounds
        mid_lat = (south + north) / 2.0
        width_m = (east - west) * 111_320.0 * np.cos(np.radians(mid_lat))
        height_m = (north - south) * 111_320.0
        box_ha = abs(width_m * height_m) / 10_000.0
        info["bbox_ha"] = round(box_ha, 2)
        if box_ha > area_ha * 6 and box_ha > 20:
            findings.append(_warn(
                "Scattered extent",
                f"The records span {box_ha:.0f} ha of ground but only cover {area_ha:.0f} ha "
                "of it.",
                "This often means two fields, or two jobs, ended up in one file.",
            ))
    return findings, info


def _check_time(ds) -> tuple[list[Finding], dict[str, Any]]:
    """Duration, logging interval and gaps."""
    findings: list[Finding] = []
    info: dict[str, Any] = {}

    if sch.TIMESTAMP not in ds.df.columns:
        # A prescription has no timeline: it was never driven.
        if ds.meta.operation not in ("prescription", "boundary", "guidance"):
            findings.append(_warn(
                "No timestamp",
                "The file carries no date or time.",
                "Filters that depend on sequence — flow delay, pass ends — cannot run.",
            ))
        return findings, info

    stamps = pd.to_datetime(ds.df[sch.TIMESTAMP], errors="coerce").dropna()
    if stamps.empty:
        findings.append(_warn("Unreadable timestamp", "The time column could not be parsed."))
        return findings, info

    span = stamps.max() - stamps.min()
    deltas = stamps.sort_values().diff().dt.total_seconds().dropna()
    interval = float(deltas.median()) if len(deltas) else 0.0
    info.update({
        "start": stamps.min().isoformat(),
        "end": stamps.max().isoformat(),
        "duration_h": round(span.total_seconds() / 3600.0, 2),
        "log_interval_s": round(interval, 2),
    })

    gaps = deltas[deltas > 1800]
    if len(gaps):
        findings.append(_warn(
            "Interrupted logging",
            f"{len(gaps)} gap(s) longer than 30 minutes; the longest is "
            f"{gaps.max() / 3600:.1f} h.",
            "Separate jobs in one file: consider whether they should be analysed apart.",
        ))
    else:
        findings.append(_ok(
            "Timeline",
            f"{span.total_seconds() / 3600:.1f} h of work, logging every "
            f"{interval:.1f} s.",
        ))
    return findings, info


def _check_columns(ds) -> tuple[list[Finding], dict[str, Any]]:
    """Which canonical columns are present, and what their absence blocks."""
    present = set(ds.df.columns)
    info = {"present": sorted(present & set(sch.LABELS)), "missing": []}
    findings: list[Finding] = []

    # A plan or a boundary is a map, not a log. It never carries a speed or a
    # swath width, and saying so would be noise dressed up as a warning.
    if ds.meta.operation in ("prescription", "boundary", "guidance"):
        if sch.VALUE in present or sch.TARGET_RATE in present or ds.geometry:
            findings.append(_ok(
                "Columns",
                "A map layer: it carries geometry and a value, which is all it needs.",
            ))
            return findings, info

    blocking = {
        sch.SPEED: "the speed and speed-change filters",
        sch.SWATH: "the partial-swath, overlap and field-edge filters",
        sch.VALUE: "every analysis — there is no variable to work on",
    }
    missing = [c for c in blocking if c not in present]
    info["missing"] = missing

    if sch.VALUE not in present:
        findings.append(_alert(
            "No main variable",
            "No column was recognized as the measured value.",
            "Open the file's column list and point at the right one.",
        ))
    for column in missing:
        if column == sch.VALUE:
            continue
        findings.append(_warn(
            f"Missing: {sch.LABELS[column]}",
            f"Without it, {blocking[column]} cannot run.",
            "The app reconstructs speed from the track when there is a timestamp; "
            "swath width has to be declared.",
        ))
    if not missing:
        findings.append(_ok("Columns", "Value, speed and swath width are all present."))
    return findings, info


#: Typical magnitudes of a working speed and an implement width, per unit.
#: A monitor is configured as a whole system, so these are read together: a
#: file whose swath reads 60 and whose speed reads 5 is in feet and mph, not
#: in metres and km/h.
SPEED_HINTS = {"km/h": (3.0, 25.0), "mph": (2.0, 16.0), "m/s": (0.8, 7.0)}
LENGTH_HINTS = {"m": (2.0, 30.0), "ft": (8.0, 130.0)}


def _detect_crop(ds) -> str | None:
    """Find the crop, from the metadata or from a crop column in the file."""
    if ds.meta.crop:
        return str(ds.meta.crop).strip().lower()
    for column in ds.df.columns:
        if sch.normalize_name(column) in ("crop", "crop_name", "crop_type", "commodity"):
            values = ds.df[column].dropna().astype(str)
            if len(values):
                candidate = values.iloc[0].strip().lower()
                if candidate in YIELD_RANGE_KG_HA or candidate in units_mod.BUSHEL_KG:
                    return candidate
    return None


def _median_positive(series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    values = values[values > 0]
    return float(values.median()) if len(values) >= 20 else None


def _guess_unit(ds, operation: str, crop: str | None) -> tuple[list[Finding], dict[str, Any]]:
    """Judge whether the file's values are plausible under the assumed units.

    The app stores everything as kg/ha, km/h and metres. If a file was written
    in the imperial set and imported without declaring it, the yield comes in
    roughly fifty times too small and every later conclusion inherits that
    error. Rather than converting silently, this names the unit set that would
    make the magnitudes plausible and leaves the decision to the user.

    Value, speed and width are judged **together**, because a monitor is
    configured as one system. Agreement between three independent quantities
    is far stronger evidence than any one of them alone.
    """
    findings: list[Finding] = []
    info: dict[str, Any] = {}

    crop = crop or _detect_crop(ds)
    if crop:
        info["crop_detected"] = crop

    # ---- speed and width: cheap, independent corroboration
    speed_guess = width_guess = None
    speed_median = _median_positive(ds.df.get(sch.SPEED, pd.Series(dtype=float)))
    if speed_median is not None:
        info["median_speed"] = round(speed_median, 2)
        fits = [u for u, (lo, hi) in SPEED_HINTS.items() if lo <= speed_median <= hi]
        # km/h and mph overlap; only an unambiguous fit is worth reporting.
        speed_guess = fits[0] if len(fits) == 1 else None

    width_median = _median_positive(ds.df.get(sch.SWATH, pd.Series(dtype=float)))
    if width_median is not None:
        info["median_swath"] = round(width_median, 2)
        fits = [u for u, (lo, hi) in LENGTH_HINTS.items() if lo <= width_median <= hi]
        width_guess = fits[0] if len(fits) == 1 else None
    if speed_guess:
        info["speed_unit_guess"] = speed_guess
    if width_guess:
        info["length_unit_guess"] = width_guess

    # ---- the main variable
    value_median = _median_positive(ds.df.get(sch.VALUE, pd.Series(dtype=float)))
    if value_median is None:
        return findings, info
    info["median_value"] = round(value_median, 2)

    if operation == "harvest":
        low, high = YIELD_RANGE_KG_HA.get(crop or "", DEFAULT_YIELD_RANGE)
        label = "yield"
    elif operation in ("application", "planting", "prescription"):
        low, high = RATE_RANGE_KG_HA
        label = "rate"
    else:
        return findings, info

    if low <= value_median <= high:
        findings.append(_ok(
            "Magnitude",
            f"Median {label} of {_thousands(value_median)} kg/ha sits inside the "
            f"plausible range for {crop or 'this crop'} "
            f"({_thousands(low)}–{_thousands(high)}).",
        ))
        # A plausible magnitude is not proof. An input rate is plausible at both
        # 96 kg/ha and 96 lb/ac, so when speed and width clearly say imperial,
        # the rate deserves a second look even though its number looks fine.
        if speed_guess == "mph" or width_guess == "ft":
            evidence = []
            if speed_guess == "mph":
                evidence.append(f"speed reads {speed_median:.1f}, which suits mph")
            if width_guess == "ft":
                evidence.append(f"width reads {width_median:.0f}, which suits feet")
            imperial_equivalent = value_median * units_mod.unit_factor(
                "rate_mass", "lb/ac", crop
            )
            findings.append(_warn(
                "Possibly imperial units",
                f"The {label} magnitude is plausible as kg/ha, but the rest of the file "
                f"looks imperial: {'; '.join(evidence)}. If the file is in lb/ac, the "
                f"real {label} is {_thousands(imperial_equivalent)} kg/ha.",
                "Check how the monitor was configured and declare the units if imperial.",
            ))
        return findings, info

    # Which declarable unit would put the median inside the plausible range?
    candidates = []
    for entry in units_mod.RATE_UNITS:
        try:
            factor = units_mod.unit_factor("rate_mass", entry["key"], crop)
        except ValueError:
            continue
        if factor == 1.0:
            continue
        converted = value_median * factor
        if low <= converted <= high:
            candidates.append({"unit": entry["key"], "kg_ha": round(converted, 1)})

    if candidates:
        # Rank by the unit set the rest of the file points at, then by the
        # manufacturer's usual export unit, then by how centred the result is.
        imperial = {"bu/ac", "lb/ac", "kg/ac", "t/ac"}
        brand_default = (ds.meta.extra or {}).get("brand_default_rate_unit")
        centre = (low + high) / 2.0
        looks_imperial = speed_guess == "mph" or width_guess == "ft"

        def score(candidate):
            unit = candidate["unit"]
            return (
                0 if unit == brand_default else 1,
                0 if (looks_imperial and unit in imperial) else 1,
                abs(candidate["kg_ha"] - centre),
            )

        candidates.sort(key=score)
        info["unit_candidates"] = candidates
        best = candidates[0]

        corroboration = []
        if speed_guess and speed_guess != "km/h":
            corroboration.append(f"speed reads {speed_median:.1f}, which suits {speed_guess}")
        if width_guess and width_guess != "m":
            corroboration.append(f"width reads {width_median:.0f}, which suits {width_guess}")

        detail = (
            f"Median {label} of {_thousands(value_median)} is outside the plausible "
            f"range for {crop or 'this crop'} "
            f"({_thousands(low)}–{_thousands(high)} kg/ha). Read as {best['unit']} it "
            f"becomes {_thousands(best['kg_ha'])} kg/ha, which fits."
        )
        if corroboration:
            detail += " " + ("The rest of the file agrees: " + "; ".join(corroboration) + ".")

        action = f"Open 'File units' and declare {best['unit']}"
        if speed_guess == "mph" or width_guess == "ft":
            action += f", {speed_guess or 'mph'} and {width_guess or 'ft'}"
        action += " before going any further."

        findings.append(_alert("Units look wrong", detail, action))
    else:
        findings.append(_warn(
            "Unusual magnitude",
            f"Median {label} of {_thousands(value_median)} kg/ha falls outside the "
            f"plausible range ({_thousands(low)}–{_thousands(high)}), and no standard "
            "unit explains it.",
            "Check the sensor calibration and which column was taken as the value.",
        ))
    return findings, info


def _thousands(value: float) -> str:
    """Thousands separator without touching the sentence's own punctuation."""
    return f"{value:,.0f}".replace(",", "\u202f")


def _check_quality(ds) -> tuple[list[Finding], dict[str, Any]]:
    """Defects that cleaning will have to deal with."""
    findings: list[Finding] = []
    info: dict[str, Any] = {}
    total = len(ds)
    if not total:
        return findings, info

    if sch.VALUE in ds.df.columns:
        values = pd.to_numeric(ds.df[sch.VALUE], errors="coerce")
        nulls = int(values.isna().sum())
        zeros = int((values == 0).sum())
        negatives = int((values < 0).sum())
        info.update({
            "null_pct": round(nulls / total * 100, 2),
            "zero_pct": round(zeros / total * 100, 2),
            "negative_pct": round(negatives / total * 100, 2),
        })
        bad = nulls + zeros + negatives
        if bad / total > 0.25:
            findings.append(_warn(
                "Many empty readings",
                f"{bad / total * 100:.1f}% of the records are null, zero or negative.",
                "Normal in a file that includes road travel; cleaning will drop them.",
            ))

    if sch.X in ds.df.columns:
        duplicated = int(
            pd.DataFrame({
                "x": ds.df[sch.X].round(2), "y": ds.df[sch.Y].round(2)
            }).duplicated().sum()
        )
        info["duplicate_position_pct"] = round(duplicated / total * 100, 2)
        if duplicated / total > 0.05:
            findings.append(_warn(
                "Repeated positions",
                f"{duplicated / total * 100:.1f}% of the records share a coordinate "
                "with another.",
                "Usually a GPS that froze, or the machine standing still while logging.",
            ))

    if sch.SPEED in ds.df.columns:
        speed = pd.to_numeric(ds.df[sch.SPEED], errors="coerce").dropna()
        if len(speed):
            outside = int(((speed < SPEED_RANGE_KMH[0]) | (speed > SPEED_RANGE_KMH[1])).sum())
            info["speed_median_kmh"] = round(float(speed.median()), 2)
            info["speed_outside_pct"] = round(outside / total * 100, 2)
            if outside / total > 0.20:
                findings.append(_warn(
                    "Speed out of range",
                    f"{outside / total * 100:.1f}% of the records fall outside "
                    f"{SPEED_RANGE_KMH[0]:g}–{SPEED_RANGE_KMH[1]:g} km/h.",
                    "Road travel and long stops inside the file.",
                ))
    return findings, info


# ==========================================================================
# Role and next step
# ==========================================================================

#: Role each dataset can play inside a project.
ROLES = {
    "yield": "Yield (harvest)",
    "as_applied": "As-applied rate",
    "plan": "Plan / prescription",
    "vigor": "Vigour (Augmenta)",
    "boundary": "Field boundary",
    "guidance": "Guidance lines",
    "soil": "Soil sampling",
    "other": "Other",
}

#: Operation type -> the role the dataset most likely plays.
ROLE_BY_OPERATION = {
    "harvest": "yield",
    "application": "as_applied",
    "planting": "as_applied",
    "prescription": "plan",
    "boundary": "boundary",
    "guidance": "guidance",
    "vigor": "vigor",
    "soil": "soil",
    "unknown": "other",
}


def suggest_next_step(ds, findings: list[Finding], role: str) -> dict[str, Any]:
    """Say what to do next with this file, and why.

    The suggestion follows the order the work actually happens in: fix the
    units first, because everything downstream inherits them; then clean;
    then analyse. Setup layers skip straight to export.
    """
    blocking = [f for f in findings if f.level == "alert"]
    if any("Units" in f.title for f in blocking):
        return {
            "step": "units",
            "label": "Declare the file's units",
            "why": "The magnitude does not match the assumed unit, and everything "
                   "downstream would inherit the error.",
        }
    if any(f.title == "No main variable" for f in blocking):
        return {
            "step": "columns",
            "label": "Pick the main variable",
            "why": "No column was recognized as the measured value.",
        }
    if role in ("boundary", "guidance"):
        return {
            "step": "export",
            "label": "Send it to a monitor",
            "why": "This is setup material: it can go straight to another platform.",
        }
    if role == "vigor":
        return {
            "step": "augmenta",
            "label": "Cross vigour against rate",
            "why": "Augmenta data carries the pair that shows whether variable rate "
                   "actually engaged.",
        }
    return {
        "step": "clean",
        "label": "Clean the data",
        "why": "Raw monitor data carries overlap, turns and sensor faults that "
               "distort every statistic that follows.",
    }


def run(ds) -> dict[str, Any]:
    """Run the full preliminary pass over a freshly imported dataset."""
    findings: list[Finding] = []
    info: dict[str, Any] = {}

    for check in (_check_coverage, _check_time, _check_columns, _check_quality):
        new_findings, new_info = check(ds)
        findings.extend(new_findings)
        info.update(new_info)

    unit_findings, unit_info = _guess_unit(ds, ds.meta.operation, ds.meta.crop)
    findings.extend(unit_findings)
    info.update(unit_info)

    role = ROLE_BY_OPERATION.get(ds.meta.operation, "other")
    next_step = suggest_next_step(ds, findings, role)

    alerts = sum(1 for f in findings if f.level == "alert")
    warnings = sum(1 for f in findings if f.level == "warning")
    if alerts:
        verdict, summary = "alert", (
            f"{alerts} thing(s) need attention before this file is usable."
        )
    elif warnings:
        verdict, summary = "warning", (
            f"The file is usable, with {warnings} point(s) worth knowing about."
        )
    else:
        verdict, summary = "ok", "The file came in clean and complete."

    return {
        "verdict": verdict,
        "summary": summary,
        "findings": [f.to_dict() for f in findings],
        "info": info,
        "suggested_role": role,
        "role_label": ROLES[role],
        "next_step": next_step,
    }
