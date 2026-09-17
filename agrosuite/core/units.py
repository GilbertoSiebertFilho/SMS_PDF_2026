"""Agricultural unit conversion.

Monitors export the same quantity in different units depending on the
country and on how the machine was configured. Everything here is converted
to AgroSuite's internal metric system:

===================  ==========================
Quantity             Internal unit
===================  ==========================
Yield                kg/ha
Applied rate         kg/ha (or seeds/ha)
Speed                km/h
Width / distance     m
Area                 ha
Mass                 kg
===================  ==========================
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Linear factors (internal_value = raw_value * factor)
# --------------------------------------------------------------------------

LENGTH_TO_M = {
    "m": 1.0, "meter": 1.0, "meters": 1.0, "metro": 1.0, "metros": 1.0,
    "cm": 0.01, "mm": 0.001,
    "ft": 0.3048, "feet": 0.3048, "foot": 0.3048, "pe": 0.3048,
    "in": 0.0254, "inch": 0.0254, "inches": 0.0254, "pol": 0.0254,
    "yd": 0.9144, "yard": 0.9144,
}

SPEED_TO_KMH = {
    "km/h": 1.0, "kmh": 1.0, "kph": 1.0, "km_h": 1.0,
    "mph": 1.609344, "mi/h": 1.609344, "mi_h": 1.609344,
    "m/s": 3.6, "ms": 3.6, "m_s": 3.6,
    "kn": 1.852, "knot": 1.852, "knots": 1.852,
}

AREA_TO_HA = {
    "ha": 1.0, "hectare": 1.0, "hectares": 1.0,
    "ac": 0.40468564224, "acre": 0.40468564224, "acres": 0.40468564224,
    "m2": 1e-4, "m^2": 1e-4, "sqm": 1e-4,
    "alq": 2.42, "alqueire": 2.42,  # Brazilian alqueire paulista
}

MASS_TO_KG = {
    "kg": 1.0, "quilo": 1.0, "quilos": 1.0,
    "g": 0.001, "t": 1000.0, "ton": 1000.0, "tonne": 1000.0, "tonelada": 1000.0,
    "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237,
    "sc": 60.0, "saca": 60.0, "sacas": 60.0,  # 60 kg Brazilian bag
}

#: Standard test weight (kg per bushel). Needed because a bushel measures
#: volume, so the conversion depends on the crop.
BUSHEL_KG = {
    "corn": 25.4012, "milho": 25.4012, "maize": 25.4012,
    "soybean": 27.2155, "soybeans": 27.2155, "soja": 27.2155,
    "wheat": 27.2155, "trigo": 27.2155,
    "barley": 21.7724, "cevada": 21.7724,
    "oats": 14.5150, "aveia": 14.5150,
    "canola": 22.6796, "rapeseed": 22.6796,
    "sorghum": 25.4012, "sorgo": 25.4012,
    "rice": 20.4117, "arroz": 20.4117,
    "sunflower": 12.7006, "girassol": 12.7006,
    "peas": 27.2155, "ervilha": 27.2155,
    "lentils": 27.2155, "lentilha": 27.2155,
    "flax": 25.4012, "linho": 25.4012,
    "rye": 25.4012, "centeio": 25.4012,
}
DEFAULT_BUSHEL_KG = 25.4012  # corn, used when the crop is unknown

#: Friendly labels for the most common output units.
YIELD_UNITS = {
    "kg/ha": 1.0,
    "t/ha": 0.001,
    "sc/ha": 1.0 / 60.0,
    "lb/ac": 1.0 / (0.45359237 / 0.40468564224),
    "bu/ac": None,  # crop dependent; handled in ``yield_to``
}


def _key(unit: str | None) -> str:
    return (unit or "").strip().lower().replace(" ", "")


def length_to_m(value: float, unit: str) -> float:
    """Convert a length to metres."""
    return value * LENGTH_TO_M.get(_key(unit), 1.0)


def speed_to_kmh(value: float, unit: str) -> float:
    """Convert a speed to km/h."""
    return value * SPEED_TO_KMH.get(_key(unit), 1.0)


def area_to_ha(value: float, unit: str) -> float:
    """Convert an area to hectares."""
    return value * AREA_TO_HA.get(_key(unit), 1.0)


def mass_to_kg(value: float, unit: str) -> float:
    """Convert a mass to kilograms."""
    return value * MASS_TO_KG.get(_key(unit), 1.0)


def bushel_kg(crop: str | None) -> float:
    """Weight of one bushel of the given crop, in kg."""
    return BUSHEL_KG.get(_key(crop), DEFAULT_BUSHEL_KG)


def yield_factor_to_kg_ha(unit: str, crop: str | None = None) -> float:
    """Multiplier taking a yield unit to kg/ha.

    Accepts the common ``mass/area`` forms monitors use, including ``bu/ac``
    (converted through the crop's test weight) and ``sc/ha``.
    """
    key = _key(unit)
    if not key:
        return 1.0
    key = key.replace("per", "/").replace("_", "/")
    if "/" not in key:
        return 1.0
    num, den = key.split("/", 1)

    if num in ("bu", "bushel", "bushels"):
        mass_kg = bushel_kg(crop)
    else:
        mass_kg = MASS_TO_KG.get(num)
        if mass_kg is None:
            return 1.0

    area_ha = AREA_TO_HA.get(den)
    if area_ha is None or area_ha == 0:
        return 1.0
    return mass_kg / area_ha


def yield_to(value_kg_ha: float, unit: str, crop: str | None = None) -> float:
    """Convert from kg/ha (internal) to the requested display unit."""
    factor = yield_factor_to_kg_ha(unit, crop)
    return value_kg_ha / factor if factor else value_kg_ha


def guess_speed_unit(values) -> str:
    """Infer the speed unit from the magnitude of the series.

    Combines and sprayers work between roughly 2 and 25 km/h. If the observed
    median falls below 4, the data is almost certainly in m/s. Between 4 and
    20 it is ambiguous between km/h and mph, and km/h is the safe default
    because it changes nothing.
    """
    import numpy as np

    arr = np.asarray(values, dtype="float64")
    arr = arr[np.isfinite(arr) & (arr > 0)]
    if arr.size == 0:
        return "km/h"
    median = float(np.median(arr))
    if median < 4.0:
        return "m/s"
    return "km/h"


# ==========================================================================
# Catalogue of units the interface can offer
# ==========================================================================
# AgroSuite stores everything in metric, but neither the source file nor the
# user has to speak metric. The catalogue below feeds the interface pickers
# at two distinct moments:
#
#   * **on import**, to declare which unit the monitor wrote;
#   * **on display and export**, to choose which unit to read the result in
#     and to write back into the file.
#
# Each entry carries the factor that takes **the unit to the internal one**.
# Display divides by it; import multiplies by it.

#: Yield and rate units (internal: kg/ha).
#: ``needs_crop`` marks those that depend on the crop's test weight.
RATE_UNITS = [
    {"key": "kg/ha",     "label": "kg/ha",                   "factor": 1.0},
    {"key": "t/ha",      "label": "t/ha (tonne per hectare)", "factor": 1000.0},
    {"key": "sc/ha",     "label": "sc/ha (60 kg bag)",        "factor": 60.0},
    {"key": "lb/ac",     "label": "lb/ac (pound per acre)",   "factor": 0.45359237 / 0.40468564224},
    {"key": "bu/ac",     "label": "bu/ac (bushel per acre)",  "factor": None, "needs_crop": True},
    {"key": "g/m2",      "label": "g/m² (gram per square metre)", "factor": 10.0},
    {"key": "kg/ac",     "label": "kg/ac",                    "factor": 1.0 / 0.40468564224},
    {"key": "t/ac",      "label": "t/ac",                     "factor": 1000.0 / 0.40468564224},
    {"key": "sc/alq",    "label": "sc/alqueire (60 kg bag)",  "factor": 60.0 / 2.42},
    {"key": "kg/alq",    "label": "kg/alqueire",              "factor": 1.0 / 2.42},
]

#: Volume per area units — spray solution, liquid fertilizer (internal: L/ha).
VOLUME_RATE_UNITS = [
    {"key": "L/ha",   "label": "L/ha",                     "factor": 1.0},
    {"key": "gal/ac", "label": "gal/ac (US gallon)",  "factor": 3.785411784 / 0.40468564224},
    {"key": "gal/ha", "label": "gal/ha (US gallon)",  "factor": 3.785411784},
    {"key": "L/ac",   "label": "L/ac",                      "factor": 1.0 / 0.40468564224},
    {"key": "mL/m2",  "label": "mL/m²",                     "factor": 10.0},
]

#: Count per area units — seeding (internal: seeds/ha).
COUNT_RATE_UNITS = [
    {"key": "seeds/ha",   "label": "seeds/ha",              "factor": 1.0},
    {"key": "seeds/ac",   "label": "seeds/ac",              "factor": 1.0 / 0.40468564224},
    {"key": "kseeds/ha",  "label": "thousand seeds/ha",     "factor": 1000.0},
    {"key": "kseeds/ac",  "label": "thousand seeds/ac",     "factor": 1000.0 / 0.40468564224},
    {"key": "seeds/m2",   "label": "seeds/m²",              "factor": 10_000.0},
]

#: Area units (internal: ha).
AREA_UNITS = [
    {"key": "ha",  "label": "hectare (ha)",       "factor": 1.0},
    {"key": "ac",  "label": "acre (ac)",          "factor": 0.40468564224},
    {"key": "alq", "label": "alqueire paulista",  "factor": 2.42},
    {"key": "m2",  "label": "square metre (m²)",  "factor": 1e-4},
    {"key": "km2", "label": "square kilometre (km²)", "factor": 100.0},
]

#: Mass units (internal: kg).
MASS_UNITS = [
    {"key": "kg", "label": "kilogram (kg)",  "factor": 1.0},
    {"key": "t",  "label": "tonne (t)",      "factor": 1000.0},
    {"key": "lb", "label": "pound (lb)",     "factor": 0.45359237},
    {"key": "sc", "label": "60 kg bag (sc)", "factor": 60.0},
    {"key": "g",  "label": "gram (g)",       "factor": 0.001},
    {"key": "bu", "label": "bushel",         "factor": None, "needs_crop": True},
]

#: Speed units (internal: km/h).
SPEED_UNITS = [
    {"key": "km/h", "label": "km/h",             "factor": 1.0},
    {"key": "mph",  "label": "mph (miles per hour)", "factor": 1.609344},
    {"key": "m/s",  "label": "m/s",                  "factor": 3.6},
    {"key": "kn",   "label": "knot",                 "factor": 1.852},
]

#: Length units (internal: m).
LENGTH_UNITS = [
    {"key": "m",  "label": "metre (m)",      "factor": 1.0},
    {"key": "ft", "label": "foot (ft)",      "factor": 0.3048},
    {"key": "in", "label": "inch (in)",      "factor": 0.0254},
    {"key": "cm", "label": "centimetre (cm)", "factor": 0.01},
    {"key": "yd", "label": "yard (yd)",      "factor": 0.9144},
]

#: Currencies — label only, no conversion between them.
CURRENCIES = [
    {"key": "CAD", "label": "Canadian dollar (C$)", "symbol": "C$"},
    {"key": "USD", "label": "US dollar (US$)",      "symbol": "US$"},
    {"key": "EUR", "label": "Euro (€)",             "symbol": "€"},
    {"key": "BRL", "label": "Brazilian real (R$)",  "symbol": "R$"},
]

UNIT_GROUPS = {
    "rate_mass": {"label": "Yield / rate by mass", "internal": "kg/ha", "units": RATE_UNITS},
    "rate_volume": {"label": "Rate by volume", "internal": "L/ha", "units": VOLUME_RATE_UNITS},
    "rate_count": {"label": "Rate by count", "internal": "seeds/ha", "units": COUNT_RATE_UNITS},
    "area": {"label": "Area", "internal": "ha", "units": AREA_UNITS},
    "mass": {"label": "Mass", "internal": "kg", "units": MASS_UNITS},
    "speed": {"label": "Speed", "internal": "km/h", "units": SPEED_UNITS},
    "length": {"label": "Length", "internal": "m", "units": LENGTH_UNITS},
}

#: Crops with a known test weight, for the bushel-based units.
CROP_CHOICES = [
    {"key": "canola", "label": "Canola", "bushel_kg": BUSHEL_KG["canola"]},
    {"key": "wheat", "label": "Wheat", "bushel_kg": BUSHEL_KG["wheat"]},
    {"key": "barley", "label": "Barley", "bushel_kg": BUSHEL_KG["barley"]},
    {"key": "oats", "label": "Oats", "bushel_kg": BUSHEL_KG["oats"]},
    {"key": "peas", "label": "Field peas", "bushel_kg": BUSHEL_KG["peas"]},
    {"key": "lentils", "label": "Lentils", "bushel_kg": BUSHEL_KG["lentils"]},
    {"key": "flax", "label": "Flax", "bushel_kg": BUSHEL_KG["flax"]},
    {"key": "rye", "label": "Rye", "bushel_kg": BUSHEL_KG["rye"]},
    {"key": "corn", "label": "Corn", "bushel_kg": BUSHEL_KG["corn"]},
    {"key": "soybean", "label": "Soybean", "bushel_kg": BUSHEL_KG["soybean"]},
    {"key": "sorghum", "label": "Sorghum", "bushel_kg": BUSHEL_KG["sorghum"]},
    {"key": "rice", "label": "Rice", "bushel_kg": BUSHEL_KG["rice"]},
    {"key": "sunflower", "label": "Sunflower", "bushel_kg": BUSHEL_KG["sunflower"]},
]


def unit_factor(group: str, unit_key: str, crop: str | None = None) -> float:
    """Factor taking ``unit_key`` to the group's internal unit.

    Bushel units depend on the crop, because a bushel measures volume: a
    bushel of corn weighs 25.40 kg and one of canola 22.68 kg. With no crop
    declared, corn's weight is used.
    """
    group_info = UNIT_GROUPS.get(group)
    if group_info is None:
        raise ValueError(f"Unknown unit group: '{group}'.")

    for entry in group_info["units"]:
        if entry["key"] != unit_key:
            continue
        if entry.get("factor") is not None:
            return float(entry["factor"])
        # Bushel units: the factor comes from the crop's test weight.
        weight = bushel_kg(crop)
        if group == "mass":
            return weight
        if group == "rate_mass":
            return weight / AREA_TO_HA["ac"]
        raise ValueError(f"Unit '{unit_key}' is not convertible in group '{group}'.")

    raise ValueError(f"Unit '{unit_key}' does not belong to group '{group}'.")


def to_internal(value, group: str, unit_key: str, crop: str | None = None):
    """Convert from the chosen unit to the internal one."""
    return value * unit_factor(group, unit_key, crop)


def from_internal(value, group: str, unit_key: str, crop: str | None = None):
    """Convert from the internal unit to the chosen one."""
    factor = unit_factor(group, unit_key, crop)
    return value / factor if factor else value


def unit_catalog() -> dict:
    """Serializable catalogue for the interface pickers."""
    return {
        "groups": {
            key: {
                "label": info["label"],
                "internal": info["internal"],
                "units": [
                    {
                        "key": u["key"],
                        "label": u["label"],
                        "needs_crop": bool(u.get("needs_crop")),
                        # The factor for bushel units is resolved on the client,
                        # from the test weight of the selected crop.
                        "factor": u["factor"],
                    }
                    for u in info["units"]
                ],
            }
            for key, info in UNIT_GROUPS.items()
        },
        "crops": CROP_CHOICES,
        "currencies": CURRENCIES,
        "presets": UNIT_PRESETS,
        "default_preset": DEFAULT_PRESET,
        "bushel_kg_per_acre_divisor": AREA_TO_HA["ac"],
    }


# ==========================================================================
# Regional presets
# ==========================================================================
# Each region works with a coherent set of units, and mixing quantities from
# different systems is the main source of misreading. The presets below set
# every picker at once; any individual unit can still be changed afterwards.
#
# The Canadian preset is the app's default: on the Prairies, monitors are
# normally set to bu/ac for grain, lb/ac for fertilizer and seed, US gal/ac
# for spray solution, and implement width is spoken in feet — even though the
# country is officially metric.

UNIT_PRESETS = {
    "canada": {
        "label": "Canada (Prairies)",
        "description": (
            "bu/ac for grain, lb/ac for fertilizer and seed, acres, feet and mph — "
            "the way monitors are usually set up in Western Canada."
        ),
        "yield_unit": "bu/ac",
        "input_rate_unit": "lb/ac",
        "seed_rate_unit": "lb/ac",
        "volume_rate_unit": "gal/ac",
        "count_rate_unit": "seeds/ac",
        "area_unit": "ac",
        "length_unit": "ft",
        "speed_unit": "mph",
        "mass_unit": "lb",
        "currency": "CAD",
        "crop": "canola",
    },
    "usa": {
        "label": "United States",
        "description": "Same imperial set as Canada, priced in US dollars.",
        "yield_unit": "bu/ac",
        "input_rate_unit": "lb/ac",
        "seed_rate_unit": "seeds/ac",
        "volume_rate_unit": "gal/ac",
        "count_rate_unit": "seeds/ac",
        "area_unit": "ac",
        "length_unit": "ft",
        "speed_unit": "mph",
        "mass_unit": "lb",
        "currency": "USD",
        "crop": "corn",
    },
    "brazil": {
        "label": "Brazil",
        "description": "Bags per hectare, kilograms, hectares and metres; priced in reais.",
        "yield_unit": "sc/ha",
        "input_rate_unit": "kg/ha",
        "seed_rate_unit": "seeds/ha",
        "volume_rate_unit": "L/ha",
        "count_rate_unit": "seeds/ha",
        "area_unit": "ha",
        "length_unit": "m",
        "speed_unit": "km/h",
        "mass_unit": "kg",
        "currency": "BRL",
        "crop": "soybean",
    },
    "metric": {
        "label": "Metric",
        "description": "Everything in SI: kg/ha, hectares, metres and km/h.",
        "yield_unit": "kg/ha",
        "input_rate_unit": "kg/ha",
        "seed_rate_unit": "seeds/ha",
        "volume_rate_unit": "L/ha",
        "count_rate_unit": "seeds/ha",
        "area_unit": "ha",
        "length_unit": "m",
        "speed_unit": "km/h",
        "mass_unit": "kg",
        "currency": "EUR",
        "crop": "wheat",
    },
}

#: Preset applied when the app first opens.
DEFAULT_PRESET = "canada"
