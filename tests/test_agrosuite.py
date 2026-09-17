"""Suíte de testes do AgroSuite.

Os testes são escritos contra comportamentos que importam na prática, não
contra detalhes de implementação: um mapa em bu/ac precisa virar kg/ha com o
peso de teste certo, a limpeza precisa recuperar o sinal de um conjunto com
defeitos conhecidos, a análise precisa achar a dose ótima que foi plantada
nos dados, e o arquivo gerado precisa passar na mesma verificação que o app
mostra ao usuário.
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
    """Gera uma vez os arquivos de exemplo de todos os monitores."""
    target = tmp_path_factory.mktemp("sample_data")
    return fx.build_all(target)


# ==========================================================================
# Esquema e unidades
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
    """Duas colunas que apontam para a mesma canônica: a primeira vence."""
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
    """Ida e volta não pode acumular erro perceptível."""
    for unit in ("kg/ha", "t/ha", "sc/ha", "lb/ac", "bu/ac"):
        internal = units_mod.to_internal(137.5, "rate_mass", unit, "wheat")
        back = units_mod.from_internal(internal, "rate_mass", unit, "wheat")
        assert back == pytest.approx(137.5, rel=1e-9)


def test_quarter_section_in_acres():
    """160 acres é a unidade de terra das Pradarias; precisa bater."""
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
# Identificação de monitor
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
# Leitura dos formatos de monitor
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
    """O log binário precisa devolver dose, velocidade e largura corretas.

    Os valores gravados no exemplo são 90 e 130 kg/ha, 8 km/h e 18,29 m; se o
    desempacotamento do binário estiver deslocado, nada disso bate.
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
    """CSV europeu: ';' como separador e ',' como decimal."""
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
    # O exemplo aplica mais onde há mais vigor: a correlação tem que ser forte.
    assert summary["correlacao"] > 0.8


def test_unsupported_extension_explains_itself(tmp_path):
    bad = tmp_path / "dados.xyz"
    bad.write_bytes(b"\x00\x01")
    with pytest.raises(ValueError, match="não suportada"):
        registry.read_any(bad)


# ==========================================================================
# Limpeza
# ==========================================================================

def test_cleaning_recovers_the_signal():
    """A limpeza precisa aproximar o mapa do valor verdadeiro conhecido."""
    dataset = synthetic_harvest()
    before = float(np.corrcoef(dataset.df["value"], dataset.df["truth_kg_ha"])[0, 1])

    result = clean_pipeline.run(dataset, clean_pipeline.PRESETS["harvest"])
    clean = result.clean.df
    after = float(np.corrcoef(clean["value"], clean["truth_kg_ha"])[0, 1])

    assert after > 0.75, "a limpeza deveria recuperar o sinal"
    assert after > before + 0.3, "o ganho precisa ser substancial"
    assert result.report["totais"]["pct_removido"] < 40


def test_overlap_filter_finds_the_planted_overlap():
    """O conjunto sintético tem uma passada de repasse; o filtro tem que achá-la."""
    dataset = synthetic_harvest()
    result = clean_pipeline.run(dataset, clean_pipeline.PRESETS["harvest"])
    overlap = next(s for s in result.report["etapas"] if s["key"] == "overlap")
    assert overlap["removed"] > 200


def test_cleaning_preserves_the_original():
    dataset = synthetic_harvest()
    original_rows = len(dataset)
    clean_pipeline.run(dataset, clean_pipeline.PRESETS["harvest"])
    assert len(dataset) == original_rows


def test_minimal_preset_removes_little():
    dataset = synthetic_harvest()
    result = clean_pipeline.run(dataset, clean_pipeline.PRESETS["minimal"])
    assert result.report["totais"]["pct_removido"] < 5


def test_report_flags_excessive_removal():
    """Uma limpeza agressiva demais tem que aparecer como alerta no laudo."""
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
    levels = {f["nivel"] for f in result.report["leitura"]}
    assert "alerta" in levels


# ==========================================================================
# Resposta e economia
# ==========================================================================

def test_quadratic_eonr_matches_theory():
    """Y = 7600 + 34R − 0,070R²; com razão de preços 5, o ótimo é 207,1."""
    rates = np.repeat([0, 60, 120, 180, 240], 40).astype(float)
    yields = 7600 + 34 * rates - 0.070 * rates**2
    fit = difm_response.fit_quadratic(rates, yields)
    optimum = difm_response.optimum_rate(fit, crop_price=1.2, input_cost=6.0, rate_max=300)
    assert optimum["dose_otima"] == pytest.approx(207.1, abs=1.0)


def test_optimum_is_below_agronomic_maximum():
    """A dose de lucro máximo tem que ficar abaixo da de rendimento máximo."""
    rates = np.repeat([0, 60, 120, 180, 240], 40).astype(float)
    yields = 7600 + 34 * rates - 0.070 * rates**2
    fit = difm_response.fit_quadratic(rates, yields)
    optimum = difm_response.optimum_rate(fit, crop_price=1.2, input_cost=6.0, rate_max=300)
    assert optimum["dose_otima"] < optimum["dose_maximo_agronomico"]


def test_free_input_optimum_equals_agronomic_maximum():
    """Insumo de graça: o ótimo econômico coincide com o máximo agronômico."""
    rates = np.repeat([0, 60, 120, 180, 240], 40).astype(float)
    yields = 7600 + 34 * rates - 0.070 * rates**2
    fit = difm_response.fit_quadratic(rates, yields)
    optimum = difm_response.optimum_rate(fit, crop_price=1.2, input_cost=0.0, rate_max=300)
    assert optimum["dose_otima"] == pytest.approx(fit.plateau_rate, abs=1.0)


def test_needs_three_distinct_rates():
    with pytest.raises(ValueError, match="3 doses"):
        difm_response.fit_best(np.array([0.0, 0, 100, 100]), np.array([1.0, 2, 3, 4]))


def test_difm_recovers_zone_optima():
    """As zonas do ensaio sintético têm ótimos de 207 e 169 kg/ha."""
    dataset = synthetic_trial()
    report = difm_analysis.analyze(
        dataset, crop_price=1.2, input_cost=6.0,
        cell_m=20, edge_margin_m=6, zone_column="zone",
    )
    optima = {z["zona"]: z["dose_otima"] for z in report["zonas"]["por_zona"] if "dose_otima" in z}
    assert optima["0"] == pytest.approx(207.1, abs=15)
    assert optima["1"] == pytest.approx(169.4, abs=15)


def test_aggregation_never_invents_a_dose():
    """Célula a cavalo entre duas faixas não pode virar uma dose intermediária."""
    dataset = synthetic_trial()
    applied = set(np.round(dataset.df["applied_rate"].unique(), 1))
    report = difm_analysis.analyze(
        dataset, crop_price=1.2, input_cost=6.0, cell_m=20, edge_margin_m=6,
    )
    assert set(report["doses_testadas"]) <= applied


# ==========================================================================
# Desenho de ensaio e linhas AB
# ==========================================================================

def test_design_is_balanced():
    result = design_strips(QUARTER_SECTION, rates=[0, 50, 100, 150, 200],
                           implement_width_m=18.29, passes_per_strip=2,
                           blocks=4, buffer_m=20)
    counts = set(result["summary"]["repeticoes_por_dose"].values())
    assert len(counts) == 1, "cada dose precisa aparecer o mesmo número de vezes"
    assert result["summary"]["blocos"] >= 2


def test_design_refuses_impossible_layout():
    with pytest.raises(ValueError, match="comporta"):
        design_strips(QUARTER_SECTION, rates=[0, 50, 100, 150, 200],
                      implement_width_m=200.0, passes_per_strip=2, blocks=4)


def test_design_needs_three_rates():
    with pytest.raises(ValueError, match="3 doses"):
        design_strips(QUARTER_SECTION, rates=[0, 100], implement_width_m=18.29)


def test_ab_line_heading_is_compass_bearing():
    line = ab_line_from_direction(QUARTER_SECTION, angle_deg=0.0)
    assert line["heading"] == pytest.approx(90.0, abs=0.1)   # leste
    line = ab_line_from_direction(QUARTER_SECTION, angle_deg=90.0)
    assert line["heading"] == pytest.approx(0.0, abs=0.1)    # norte


def test_ab_line_rejects_coincident_points():
    with pytest.raises(ValueError, match="menos de um metro"):
        ab_line_from_points((-105.834, 50.452), (-105.834, 50.452))


# ==========================================================================
# ISOXML: ida e volta
# ==========================================================================

def test_prescription_round_trip(tmp_path):
    """A dose gravada em mg/m² tem que voltar como os mesmos kg/ha."""
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
# Pacotes e verificação
# ==========================================================================

@pytest.fixture
def built_package(tmp_path):
    design = design_strips(QUARTER_SECTION, rates=[0, 50, 100, 150, 200],
                           implement_width_m=18.29, passes_per_strip=2,
                           blocks=4, buffer_m=20)
    line = ab_line_from_direction(QUARTER_SECTION, design["summary"]["direcao_graus"])
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
    assert report["totals"]["falha"] == 0


def test_package_uses_the_expected_rate_field(built_package):
    design, line, tmp_path = built_package
    folder = tmp_path / "trimble"
    writers.build_package(folder, monitor="trimble", prescription=design["features"],
                          rate_unit="lb/ac", field_name="T")
    shapefiles = list(folder.rglob("*.shp"))
    assert shapefiles
    import geopandas as gpd

    columns = set(gpd.read_file(shapefiles[0]).columns)
    assert "TGT_RATE" in columns, "o Trimble procura a dose em TGT_RATE"


def test_package_readme_is_written(built_package):
    design, line, tmp_path = built_package
    folder = tmp_path / "raven"
    result = writers.build_package(folder, monitor="raven",
                                   prescription=design["features"],
                                   boundary=QUARTER_SECTION)
    readme = Path(result["readme"]).read_text(encoding="utf-8")
    assert "RAVEN VIPER 4" in readme
    assert "COMO CARREGAR" in readme


def test_validator_catches_missing_sidecar(tmp_path):
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Polygon

    poly = Polygon([(-105.83, 50.45), (-105.82, 50.45), (-105.82, 50.46)])
    gpd.GeoDataFrame(pd.DataFrame({"RATE": [100.0]}), geometry=[poly],
                     crs="EPSG:4326").to_file(tmp_path / "rx.shp")
    (tmp_path / "rx.prj").unlink()
    checks = validate.validate_shapefile(tmp_path / "rx.shp", rate_field="RATE")
    assert any(c.status == "falha" and "prj" in c.message for c in checks)


def test_validator_catches_points_used_as_prescription(tmp_path):
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    gpd.GeoDataFrame(pd.DataFrame({"RATE": [100.0]}), geometry=[Point(-105.83, 50.45)],
                     crs="EPSG:4326").to_file(tmp_path / "rx.shp")
    checks = validate.validate_shapefile(tmp_path / "rx.shp", rate_field="RATE")
    assert any(c.status == "falha" and "polígono" in c.message for c in checks)


def test_validator_catches_grid_length_mismatch(tmp_path):
    grid = np.array([[100.0, 120.0], [110.0, 130.0]])
    isoxml.write_prescription(tmp_path, grid, -105.834, 50.452, 0.0001, 0.0001)
    binary = tmp_path / "TASKDATA" / "GRD00001.BIN"
    binary.write_bytes(binary.read_bytes()[:-4])  # remove uma célula
    checks = validate.validate_taskdata(tmp_path / "TASKDATA")
    assert any(c.status == "falha" and "bytes" in c.message for c in checks)


# ==========================================================================
# Cartão John Deere
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
    with pytest.raises(ValueError, match="shapefile em vez de GreenStar"):
        registry.read_any(tmp_path)
