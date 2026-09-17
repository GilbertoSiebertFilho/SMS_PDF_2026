"""Esquema canônico de colunas.

Todo dataset carregado no AgroSuite é convertido para um conjunto único de
nomes de coluna, independentemente do monitor de origem. Os dicionários de
*alias* abaixo concentram o conhecimento sobre como cada fabricante nomeia
as mesmas grandezas nos seus arquivos exportados (SHP/DBF, CSV, ISOXML).

Regras de normalização de um nome bruto antes da busca por alias:
  - caixa baixa, sem acentos
  - qualquer caractere não alfanumérico vira "_"
  - "_" repetidos são colapsados e removidos das pontas
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Colunas canônicas
# --------------------------------------------------------------------------

LON = "lon"
LAT = "lat"
X = "x"
Y = "y"
TIMESTAMP = "timestamp"
ELAPSED = "elapsed_s"
VALUE = "value"
SPEED = "speed_kmh"
SWATH = "swath_m"
DISTANCE = "distance_m"
HEADING = "heading_deg"
ELEVATION = "elev_m"
MOISTURE = "moisture_pct"
FLOW = "flow_kgs"
PASS = "pass_id"
SECTION = "section_id"
PRODUCT = "product"
TARGET_RATE = "target_rate"
APPLIED_RATE = "applied_rate"
CROP = "crop"
TRIAL_ID = "trial_id"

#: Colunas numéricas conhecidas — usadas para coerção de tipo na importação.
NUMERIC_COLUMNS = (
    LON, LAT, X, Y, ELAPSED, VALUE, SPEED, SWATH, DISTANCE, HEADING,
    ELEVATION, MOISTURE, FLOW, TARGET_RATE, APPLIED_RATE,
)

#: Rótulos em português para exibição na interface.
LABELS = {
    LON: "Longitude",
    LAT: "Latitude",
    X: "X projetado (m)",
    Y: "Y projetado (m)",
    TIMESTAMP: "Data/hora",
    ELAPSED: "Tempo decorrido (s)",
    VALUE: "Variável principal",
    SPEED: "Velocidade (km/h)",
    SWATH: "Largura de faixa (m)",
    DISTANCE: "Distância percorrida (m)",
    HEADING: "Rumo (graus)",
    ELEVATION: "Elevação (m)",
    MOISTURE: "Umidade (%)",
    FLOW: "Fluxo (kg/s)",
    PASS: "Passada",
    SECTION: "Seção",
    PRODUCT: "Produto",
    TARGET_RATE: "Dose alvo",
    APPLIED_RATE: "Dose aplicada",
    CROP: "Cultura",
    TRIAL_ID: "Faixa do ensaio",
}


def normalize_name(raw: str) -> str:
    """Reduz um nome de coluna bruto à forma usada nas tabelas de alias."""
    if raw is None:
        return ""
    text = unicodedata.normalize("NFKD", str(raw))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


# --------------------------------------------------------------------------
# Aliases por grandeza
# --------------------------------------------------------------------------
# Cada lista reúne os nomes efetivamente encontrados em exportações de
# John Deere (Operations Center / GreenStar / SMS), Raven Viper 4, Trimble
# (FmX/GFX/TMX), Case IH AFS e New Holland Precision Land Management,
# Bourgault (X35/X30), Väderstad E-Control/ISOBUS, Augmenta, Ag Leader SMS,
# Climate FieldView e do padrão ISO 11783-10 (ISOXML).

ALIASES: dict[str, tuple[str, ...]] = {
    LON: (
        "lon", "long", "longitude", "lng", "x", "gps_long", "gpslong",
        "longitude_deg", "long_dd", "lon_dd", "position_east", "easting_deg",
        "gps_longitude", "wgs84_lon", "coord_x", "point_x",
    ),
    LAT: (
        "lat", "latitude", "gps_lat", "gpslat", "latitude_deg", "lat_dd",
        "position_north", "northing_deg", "gps_latitude", "wgs84_lat",
        "coord_y", "point_y",
    ),
    TIMESTAMP: (
        "timestamp", "date_time", "datetime", "date_and_time", "time_stamp",
        "gps_time", "utc_time", "record_time", "data_hora", "isotime",
        "time", "date", "logtime", "sample_time", "acquisition_time",
    ),
    VALUE: (
        # rendimento (colheita)
        "yield", "yld", "yield_vol", "yld_vol_dr", "yld_mass_d", "yld_mass_w",
        "vryieldvol", "vryieldmass", "dry_yield", "wet_yield", "yield_dry",
        "yield_wet", "yldvoldry", "yldmassdry", "yield_bu_ac", "yield_kg_ha",
        "yield_t_ha", "rendimento", "produtividade", "crop_flow",
        "harvest_yield", "yield_rate", "massflow", "mass_flow",
        # aplicação / plantio
        "applied_rate", "rate_applied", "appliedrate", "actual_rate",
        "as_applied_rate", "app_rate", "rate", "dose", "taxa",
        "seed_rate", "seeding_rate", "population", "pop", "plant_pop",
        "prod_rate", "product_rate", "vrappliedrate", "appld_rate",
    ),
    SPEED: (
        "speed", "velocity", "gps_speed", "ground_speed", "speed_kmh",
        "speed_km_h", "speed_mph", "speed_mi_h", "velocidade", "spd",
        "machine_speed", "travel_speed", "speed_ms", "speed_m_s",
    ),
    SWATH: (
        "swath", "swath_width", "swth_wdth", "width", "swathwidth",
        "implement_width", "impl_width", "header_width", "head_width",
        "effective_width", "largura", "working_width", "boom_width",
        "swth_wdth_", "swathwidth_m", "cut_width",
    ),
    DISTANCE: (
        "distance", "dist", "dist_m", "distance_m", "interval_distance",
        "log_dist", "delta_dist", "distancia", "dst",
    ),
    HEADING: (
        "heading", "track_deg", "course", "bearing", "direction", "hdg",
        "gps_heading", "track", "azimuth", "rumo", "heading_deg",
    ),
    ELEVATION: (
        "elevation", "elev", "altitude", "alt", "height", "z", "elev_m",
        "elevation_m", "gps_altitude", "msl", "elevacao",
    ),
    MOISTURE: (
        "moisture", "moist", "moisture_pct", "grain_moisture", "humidity",
        "umidade", "moisture_percent", "wet_moisture", "moisture_",
        "grainmoist", "moisture_content",
    ),
    FLOW: (
        "flow", "flow_rate", "grain_flow", "mass_flow_rate", "crop_flow_rate",
        "flowrate", "fluxo", "clean_grain_flow",
    ),
    PASS: (
        "pass", "pass_num", "passnum", "pass_id", "swath_number", "swathnum",
        "track_number", "passada", "obj_id_pass", "run",
    ),
    SECTION: (
        "section", "section_id", "sec", "boom_section", "section_num",
        "row_unit", "secao", "channel",
    ),
    PRODUCT: (
        "product", "product_name", "prd_name", "prodname", "produto",
        "input", "input_name", "material", "fertilizer", "hybrid", "variety",
        "cultivar", "seed_name", "brand",
    ),
    TARGET_RATE: (
        "target_rate", "tgt_rate", "targetrate", "prescribed_rate", "rx_rate",
        "rx", "dose_alvo", "taxa_alvo", "rate_target", "commanded_rate",
        "setpoint", "set_rate", "tgt_rate_o", "vrtargetrate",
    ),
    APPLIED_RATE: (
        "applied_rate", "appliedrate", "actual_rate", "as_applied",
        "rate_applied", "dose_aplicada", "taxa_aplicada", "appld_rate",
    ),
    CROP: ("crop", "crop_name", "cultura", "croptype", "crop_type", "commodity"),
    ELAPSED: ("elapsed", "elapsed_s", "elapsed_time", "seconds", "sec_elapsed"),
    TRIAL_ID: (
        "trial", "trial_id", "plot", "plot_id", "strip", "strip_id",
        "treatment", "trt", "tratamento", "faixa", "block", "bloco",
    ),
}

#: Índice invertido alias -> coluna canônica (construído uma vez).
_ALIAS_INDEX: dict[str, str] = {}
for _canonical, _names in ALIASES.items():
    for _name in _names:
        # O primeiro a registrar vence: a ordem de ALIASES define a prioridade
        # (ex.: "applied_rate" resolve para VALUE, não para APPLIED_RATE).
        _ALIAS_INDEX.setdefault(normalize_name(_name), _canonical)


def resolve_column(raw: str) -> str | None:
    """Devolve a coluna canônica de um nome bruto, ou ``None`` se desconhecido."""
    key = normalize_name(raw)
    if not key:
        return None
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    # Segunda tentativa: alias contido no nome (ex.: "yld_vol_dry_2023").
    for alias, canonical in _ALIAS_INDEX.items():
        if len(alias) >= 4 and alias in key:
            return canonical
    return None


def map_columns(raw_columns) -> dict[str, str]:
    """Mapeia ``{nome_bruto: nome_canônico}`` para as colunas reconhecidas.

    Se duas colunas brutas apontarem para a mesma canônica, a primeira vence e
    a segunda é deixada de fora (permanece no dataset com o nome original).
    """
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for raw in raw_columns:
        canonical = resolve_column(raw)
        if canonical and canonical not in taken:
            mapping[raw] = canonical
            taken.add(canonical)
    return mapping
