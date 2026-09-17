"""AgroSuite test suite.

The tests are written against behaviours that matter in practice, not against
implementation details: a map in bu/ac has to become kg/ha with the right test
weight, cleaning has to recover the signal from a dataset with known defects,
the analysis has to find the optimum rate that was planted in the data, and
the generated file has to pass the same verification the app shows the user.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agrosuite.clean import pipeline as clean_pipeline
from agrosuite.core import schema as sch
from agrosuite.core import units as units_mod
from agrosuite.core.guidance import ab_line_from_direction, ab_line_from_points
from agrosuite.demo import synthetic_harvest, synthetic_trial
from agrosuite.difm import analysis as difm_analysis
from agrosuite.difm import response as difm_response
from agrosuite.difm.design import design_strips
from agrosuite.formats import brands, isoxml, registry, validate, writers

import fixtures as fx

QUARTER_SECTION = [
    (-105.8340, 50.4520), (-105.8229, 50.4520),
    (-105.8229, 50.4592), (-105.8340, 50.4592),
]


@pytest.fixture(scope="session")
def sample_data(tmp_path_factory) -> dict[str, Path]:
    """Generate the sample files for every monitor once."""
    target = tmp_path_factory.mktemp("sample_data")
    return fx.build_all(target)


# ==========================================================================
# Schema and units
# ==========================================================================

@pytest.mark.parametrize("raw,expected", [
    ("Yld_Vol_Dr", sch.VALUE),
    ("VRYIELDVOL", sch.VALUE),
    ("Swth_Wdth_", sch.SWATH),
    ("Arbeitsbreite (m)", sch.SWATH),
    ("Geschwindigkeit (km/h)", sch.SPEED),
    ("Longitude", sch.LON),
    ("Umidade", sch.MOISTURE),
    ("Tgt_Rate_O", sch.TARGET_RATE),
    ("coluna_inventada_xyz", None),
])
def test_alias_resolution(raw, expected):
    assert sch.resolve_column(raw) == expected


def test_map_columns_does_not_duplicate_targets():
    """Two columns pointing at the same canonical name: the first one wins."""
    mapping = sch.map_columns(["Yield", "Dry Yield", "Latitude", "Longitude"])
    assert list(mapping.values()).count(sch.VALUE) == 1


@pytest.mark.parametrize("value,unit,crop,expected", [
    (200, "bu/ac", "corn", 12553.5),
    (60, "bu/ac", "soybean", 4035.1),
    (50, "bu/ac", "canola", 2802.0),
    (90, "lb/ac", None, 100.9),
    (100, "sc/ha", None, 6000.0),
])
def test_rate_to_internal(value, unit, crop, expected):
    result = units_mod.to_internal(value, "rate_mass", unit, crop)
    assert result == pytest.approx(expected, rel=1e-3)


def test_rate_round_trip():
    """A round trip must not accumulate noticeable error."""
    for unit in ("kg/ha", "t/ha", "sc/ha", "lb/ac", "bu/ac"):
        internal = units_mod.to_internal(137.5, "rate_mass", unit, "wheat")
        back = units_mod.from_internal(internal, "rate_mass", unit, "wheat")
        assert back == pytest.approx(137.5, rel=1e-9)


def test_quarter_section_in_acres():
    """160 acres is the Prairie land unit; it has to come out right."""
    assert units_mod.to_internal(160, "area", "ac") == pytest.approx(64.75, rel=1e-3)


def test_canadian_preset_is_default():
    catalog = units_mod.unit_catalog()
    assert catalog["default_preset"] == "canada"
    preset = catalog["presets"]["canada"]
    assert preset["yield_unit"] == "bu/ac"
    assert preset["input_rate_unit"] == "lb/ac"
    assert preset["area_unit"] == "ac"
    assert preset["length_unit"] == "ft"
    assert preset["currency"] == "CAD"


# ==========================================================================
# Monitor identification
# ==========================================================================

@pytest.mark.parametrize("columns,path,expected", [
    (["VRYIELDVOL", "Machine", "Variety"], "colheita.shp", "john_deere"),
    (["Obj__Id", "Swth_Wdth_", "Track_deg_"], "export.shp", "ag_leader"),
    (["Longitude", "Prod Rate", "Boom Section"], "viper4/log.csv", "raven"),
    (["Longitude", "Tgt_Rate_O"], "gfx/coverage.csv", "trimble"),
    (["vigor", "session_id", "ndvi"], "augmenta/s.geojson", "augmenta"),
])
def test_brand_detection(columns, path, expected):
    key, confidence = brands.detect_brand(columns, path)
    assert key == expected
    assert confidence > 0.3


# ==========================================================================
# Reading the monitor formats
# ==========================================================================

@pytest.mark.parametrize("key,brand,operation", [
    ("john_deere_shp", "john_deere", "harvest"),
    ("john_deere_zip", "john_deere", "harvest"),
    ("ag_leader_shp", "ag_leader", "harvest"),
    ("raven_csv", "raven", "application"),
    ("trimble_csv", "trimble", "application"),
    ("bourgault_csv", "bourgault", "planting"),
    ("semicolon_csv", "vaderstad", "planting"),
    ("isoxml", "case_ih", "application"),
    ("augmenta", "augmenta", "application"),
])
def test_reads_monitor_formats(sample_data, key, brand, operation):
    dataset = registry.read_any(sample_data[key])
    dataset.ensure_derived()
    assert dataset.meta.brand == brand
    assert dataset.meta.operation == operation
    assert len(dataset) > 100
    assert sch.LON in dataset.df.columns and sch.LAT in dataset.df.columns
    assert dataset.df[sch.LAT].between(-90, 90).all()


def test_isoxml_binary_log_decodes_values(sample_data):
    """The binary log has to return the right rate, speed and width.

    The values written into the sample are 90 and 130 kg/ha, 8 km/h and
    18.29 m; if the binary unpacking is off by a byte, none of that matches.
    """
    dataset = registry.read_any(sample_data["isoxml"])
    dataset.ensure_derived()
    rates = dataset.df[sch.APPLIED_RATE].dropna().unique()
    assert set(np.round(rates)) == {90.0, 130.0}
    assert dataset.df[sch.SPEED].median() == pytest.approx(8.0, abs=0.5)
    assert dataset.df[sch.SWATH].median() == pytest.approx(18.29, abs=0.05)


def test_isoxml_carries_boundary(sample_data):
    dataset = registry.read_any(sample_data["isoxml"])
    setup = dataset.meta.extra.get("field_setup")
    assert setup and setup["fields"]
    assert setup["fields"][0]["boundaries"]


def test_semicolon_csv_decimal_comma(sample_data):
    """European CSV: ';' as separator and ',' as decimal mark."""
    dataset = registry.read_any(sample_data["semicolon_csv"])
    dataset.ensure_derived()
    assert dataset.df[sch.SPEED].median() == pytest.approx(8.0, abs=0.5)
    assert dataset.df[sch.SWATH].median() == pytest.approx(18.29, abs=0.05)


def test_augmenta_pairs_vigor_and_rate(sample_data):
    from agrosuite.formats.augmenta import vigor_rate_summary

    dataset = registry.read_any(sample_data["augmenta"])
    assert "vigor_index" in dataset.df.columns
    summary = vigor_rate_summary(dataset)
    assert summary["available"]
    # The sample applies more where vigour is higher: correlation must be strong.
    assert summary["correlation"] > 0.8


def test_unsupported_extension_explains_itself(tmp_path):
    bad = tmp_path / "dados.xyz"
    bad.write_bytes(b"\x00\x01")
    with pytest.raises(ValueError, match="not supported"):
        registry.read_any(bad)


# ==========================================================================
# Cleaning
# ==========================================================================

def test_cleaning_recovers_the_signal():
    """Cleaning has to move the map closer to the known true value."""
    dataset = synthetic_harvest()
    before = float(np.corrcoef(dataset.df["value"], dataset.df["truth_kg_ha"])[0, 1])

    result = clean_pipeline.run(dataset, clean_pipeline.PRESETS["harvest"])
    clean = result.clean.df
    after = float(np.corrcoef(clean["value"], clean["truth_kg_ha"])[0, 1])

    assert after > 0.75, "cleaning should recover the signal"
    assert after > before + 0.3, "the gain has to be substantial"
    assert result.report["totals"]["removed_pct"] < 40


def test_overlap_filter_finds_the_planted_overlap():
    """The synthetic set has one re-run pass; the filter has to find it."""
    dataset = synthetic_harvest()
    result = clean_pipeline.run(dataset, clean_pipeline.PRESETS["harvest"])
    overlap = next(s for s in result.report["steps"] if s["key"] == "overlap")
    assert overlap["removed"] > 200


def test_cleaning_preserves_the_original():
    dataset = synthetic_harvest()
    original_rows = len(dataset)
    clean_pipeline.run(dataset, clean_pipeline.PRESETS["harvest"])
    assert len(dataset) == original_rows


def test_minimal_preset_removes_little():
    dataset = synthetic_harvest()
    result = clean_pipeline.run(dataset, clean_pipeline.PRESETS["minimal"])
    assert result.report["totals"]["removed_pct"] < 5


def test_report_flags_excessive_removal():
    """Cleaning that is too aggressive has to show up as an alert in the report."""
    dataset = synthetic_harvest()
    config = {
        "corrections": {"flow_delay_s": 0},
        "steps": {
            "null_value": {"enabled": True},
            "global_outlier": {"enabled": True, "method": "percentile",
                               "lower_pct": 30.0, "upper_pct": 70.0},
        },
    }
    result = clean_pipeline.run(dataset, config)
    levels = {f["level"] for f in result.report["findings"]}
    assert "alert" in levels


# ==========================================================================
# Response and economics
# ==========================================================================

def test_quadratic_eonr_matches_theory():
    """Y = 7600 + 34R - 0.070R^2; with a price ratio of 5, the optimum is 207.1."""
    rates = np.repeat([0, 60, 120, 180, 240], 40).astype(float)
    yields = 7600 + 34 * rates - 0.070 * rates**2
    fit = difm_response.fit_quadratic(rates, yields)
    optimum = difm_response.optimum_rate(fit, crop_price=1.2, input_cost=6.0, rate_max=300)
    assert optimum["optimum_rate"] == pytest.approx(207.1, abs=1.0)


def test_optimum_is_below_agronomic_maximum():
    """The maximum-profit rate has to sit below the maximum-yield rate."""
    rates = np.repeat([0, 60, 120, 180, 240], 40).astype(float)
    yields = 7600 + 34 * rates - 0.070 * rates**2
    fit = difm_response.fit_quadratic(rates, yields)
    optimum = difm_response.optimum_rate(fit, crop_price=1.2, input_cost=6.0, rate_max=300)
    assert optimum["optimum_rate"] < optimum["agronomic_maximum"]


def test_free_input_optimum_equals_agronomic_maximum():
    """Free input: the economic optimum coincides with the agronomic maximum."""
    rates = np.repeat([0, 60, 120, 180, 240], 40).astype(float)
    yields = 7600 + 34 * rates - 0.070 * rates**2
    fit = difm_response.fit_quadratic(rates, yields)
    optimum = difm_response.optimum_rate(fit, crop_price=1.2, input_cost=0.0, rate_max=300)
    assert optimum["optimum_rate"] == pytest.approx(fit.plateau_rate, abs=1.0)


def test_needs_three_distinct_rates():
    with pytest.raises(ValueError, match="3 distinct rates"):
        difm_response.fit_best(np.array([0.0, 0, 100, 100]), np.array([1.0, 2, 3, 4]))


def test_difm_recovers_zone_optima():
    """The synthetic trial's zones have optima of 207 and 169 kg/ha."""
    dataset = synthetic_trial()
    report = difm_analysis.analyze(
        dataset, crop_price=1.2, input_cost=6.0,
        cell_m=20, edge_margin_m=6, zone_column="zone",
    )
    optima = {z["zone"]: z["optimum_rate"] for z in report["zones"]["by_zone"] if "optimum_rate" in z}
    assert optima["0"] == pytest.approx(207.1, abs=15)
    assert optima["1"] == pytest.approx(169.4, abs=15)


def test_aggregation_never_invents_a_dose():
    """A cell straddling two strips must not become an in-between rate."""
    dataset = synthetic_trial()
    applied = set(np.round(dataset.df["applied_rate"].unique(), 1))
    report = difm_analysis.analyze(
        dataset, crop_price=1.2, input_cost=6.0, cell_m=20, edge_margin_m=6,
    )
    assert set(report["rates_tested"]) <= applied


# ==========================================================================
# Trial layout and AB lines
# ==========================================================================

def test_design_is_balanced():
    result = design_strips(QUARTER_SECTION, rates=[0, 50, 100, 150, 200],
                           implement_width_m=18.29, passes_per_strip=2,
                           blocks=4, buffer_m=20)
    counts = set(result["summary"]["reps_per_rate"].values())
    assert len(counts) == 1, "each rate has to appear the same number of times"
    assert result["summary"]["blocks"] >= 2


def test_design_refuses_impossible_layout():
    with pytest.raises(ValueError, match="only fits"):
        design_strips(QUARTER_SECTION, rates=[0, 50, 100, 150, 200],
                      implement_width_m=200.0, passes_per_strip=2, blocks=4)


def test_design_needs_three_rates():
    with pytest.raises(ValueError, match="3 distinct rates"):
        design_strips(QUARTER_SECTION, rates=[0, 100], implement_width_m=18.29)


def test_ab_line_heading_is_compass_bearing():
    line = ab_line_from_direction(QUARTER_SECTION, angle_deg=0.0)
    assert line["heading"] == pytest.approx(90.0, abs=0.1)   # east
    line = ab_line_from_direction(QUARTER_SECTION, angle_deg=90.0)
    assert line["heading"] == pytest.approx(0.0, abs=0.1)    # north


def test_ab_line_rejects_coincident_points():
    with pytest.raises(ValueError, match="less than a metre"):
        ab_line_from_points((-105.834, 50.452), (-105.834, 50.452))


# ==========================================================================
# ISOXML round trips
# ==========================================================================

def test_prescription_round_trip(tmp_path):
    """A rate written in mg/m2 has to come back as the same kg/ha."""
    grid = np.array([[100.0, 120.0, 140.0], [110.0, 130.0, 150.0]])
    isoxml.write_prescription(tmp_path, grid, -105.834, 50.452, 0.0001, 0.0001,
                              rate_kind="mass", field_name="NW-14-32-W2")
    dataset = isoxml.read_isoxml(tmp_path / "TASKDATA")
    recovered = sorted(dataset.df[sch.TARGET_RATE].round(1).unique())
    assert recovered == [100.0, 110.0, 120.0, 130.0, 140.0, 150.0]


def test_field_setup_round_trip(tmp_path):
    lines = [{"name": "AB Principal", "type": 1,
              "a": (-105.8335, 50.4525), "b": (-105.8285, 50.4525)}]
    out = isoxml.write_field_setup(tmp_path, field_name="NW-14-32-W2",
                                   boundary=QUARTER_SECTION, guidance_lines=lines)
    back = isoxml.read_field_setup(out / "TASKDATA.XML")
    field = back["fields"][0]
    assert field["name"] == "NW-14-32-W2"
    assert field["boundaries"]
    assert field["guidance_lines"][0]["a"] == pytest.approx((-105.8335, 50.4525))
    assert field["guidance_lines"][0]["b"] == pytest.approx((-105.8285, 50.4525))


# ==========================================================================
# Packages and verification
# ==========================================================================

@pytest.fixture
def built_package(tmp_path):
    design = design_strips(QUARTER_SECTION, rates=[0, 50, 100, 150, 200],
                           implement_width_m=18.29, passes_per_strip=2,
                           blocks=4, buffer_m=20)
    line = ab_line_from_direction(QUARTER_SECTION, design["summary"]["direction_deg"])
    return design, line, tmp_path


@pytest.mark.parametrize("monitor", ["raven", "john_deere", "case_ih", "trimble", "vaderstad"])
def test_package_passes_its_own_verification(built_package, monitor):
    design, line, tmp_path = built_package
    folder = tmp_path / monitor
    writers.build_package(folder, monitor=monitor, prescription=design["features"],
                          boundary=QUARTER_SECTION, guidance_lines=[line],
                          rate_unit="lb/ac", cell_m=10, field_name="NW-14-32-W2")
    report = validate.validate_package(folder, monitor)
    assert report["verdict"] == "ok", report["summary"]
    assert report["totals"]["fail"] == 0


def test_package_uses_the_expected_rate_field(built_package):
    design, line, tmp_path = built_package
    folder = tmp_path / "trimble"
    writers.build_package(folder, monitor="trimble", prescription=design["features"],
                          rate_unit="lb/ac", field_name="T")
    shapefiles = list(folder.rglob("*.shp"))
    assert shapefiles
    import geopandas as gpd

    columns = set(gpd.read_file(shapefiles[0]).columns)
    assert "TGT_RATE" in columns, "Trimble looks for the rate in TGT_RATE"


def test_package_readme_is_written(built_package):
    design, line, tmp_path = built_package
    folder = tmp_path / "raven"
    result = writers.build_package(folder, monitor="raven",
                                   prescription=design["features"],
                                   boundary=QUARTER_SECTION)
    readme = Path(result["readme"]).read_text(encoding="utf-8")
    assert "RAVEN VIPER 4" in readme
    assert "HOW TO LOAD IT" in readme


def test_validator_catches_missing_sidecar(tmp_path):
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Polygon

    poly = Polygon([(-105.83, 50.45), (-105.82, 50.45), (-105.82, 50.46)])
    gpd.GeoDataFrame(pd.DataFrame({"RATE": [100.0]}), geometry=[poly],
                     crs="EPSG:4326").to_file(tmp_path / "rx.shp")
    (tmp_path / "rx.prj").unlink()
    checks = validate.validate_shapefile(tmp_path / "rx.shp", rate_field="RATE")
    assert any(c.status == "fail" and "prj" in c.message for c in checks)


def test_validator_catches_points_used_as_prescription(tmp_path):
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    gpd.GeoDataFrame(pd.DataFrame({"RATE": [100.0]}), geometry=[Point(-105.83, 50.45)],
                     crs="EPSG:4326").to_file(tmp_path / "rx.shp")
    checks = validate.validate_shapefile(tmp_path / "rx.shp", rate_field="RATE")
    assert any(c.status == "fail" and "polygons" in c.message for c in checks)


def test_validator_catches_grid_length_mismatch(tmp_path):
    grid = np.array([[100.0, 120.0], [110.0, 130.0]])
    isoxml.write_prescription(tmp_path, grid, -105.834, 50.452, 0.0001, 0.0001)
    binary = tmp_path / "TASKDATA" / "GRD00001.BIN"
    binary.write_bytes(binary.read_bytes()[:-4])  # drop one cell
    checks = validate.validate_taskdata(tmp_path / "TASKDATA")
    assert any(c.status == "fail" and "bytes" in c.message for c in checks)


# ==========================================================================
# John Deere card
# ==========================================================================

def test_greenstar_card_is_recognized(tmp_path):
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Polygon

    from agrosuite.formats import johndeere as jd

    card = tmp_path / "GS3_2630"
    (card / "SETUP").mkdir(parents=True)
    (card / "RCD").mkdir()
    poly = Polygon(QUARTER_SECTION)
    gpd.GeoDataFrame(pd.DataFrame([{"NAME": "NW-14-32-W2"}]), geometry=[poly],
                     crs="EPSG:4326").to_file(card / "SETUP" / "Boundary.shp")
    (card / "SETUP" / "Setup.jdf").write_bytes(b"JDF" + bytes(64))

    inventory = jd.inventory(tmp_path)
    assert "GreenStar 3" in inventory.generation
    assert inventory.readable and inventory.proprietary
    assert jd.readable_layers(inventory)[0]["role"] == "boundary"

    dataset = registry.read_any(tmp_path)
    assert dataset.meta.brand == "john_deere"
    assert dataset.meta.extra["field_setup"]["fields"][0]["boundaries"]


def test_card_with_only_proprietary_files_explains_the_way_out(tmp_path):
    card = tmp_path / "GS3_2630"
    (card / "SETUP").mkdir(parents=True)
    (card / "SETUP" / "Setup.jdf").write_bytes(b"JDF" + bytes(64))
    with pytest.raises(ValueError, match="shapefile instead of GreenStar"):
        registry.read_any(tmp_path)
