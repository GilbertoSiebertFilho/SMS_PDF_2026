"""Conversão de unidades agrícolas.

Os monitores exportam a mesma grandeza em unidades diferentes conforme o
país e a configuração da máquina. Aqui tudo é convertido para o sistema
métrico interno do AgroSuite:

===================  ==========================
Grandeza             Unidade interna
===================  ==========================
Rendimento           kg/ha
Dose aplicada        kg/ha (ou sementes/ha)
Velocidade           km/h
Largura / distância  m
Área                 ha
Massa                kg
===================  ==========================
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Fatores lineares (valor_interno = valor_bruto * fator)
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
    "alq": 2.42, "alqueire": 2.42,  # alqueire paulista
}

MASS_TO_KG = {
    "kg": 1.0, "quilo": 1.0, "quilos": 1.0,
    "g": 0.001, "t": 1000.0, "ton": 1000.0, "tonne": 1000.0, "tonelada": 1000.0,
    "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237,
    "sc": 60.0, "saca": 60.0, "sacas": 60.0,  # saca brasileira padrão de 60 kg
}

#: Peso de teste padrão (kg por bushel) — necessário porque o bushel é uma
#: unidade de volume e a conversão depende da cultura.
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
DEFAULT_BUSHEL_KG = 25.4012  # milho, usado quando a cultura é desconhecida

#: Rótulos amigáveis para as unidades de saída mais usadas.
YIELD_UNITS = {
    "kg/ha": 1.0,
    "t/ha": 0.001,
    "sc/ha": 1.0 / 60.0,
    "lb/ac": 1.0 / (0.45359237 / 0.40468564224),
    "bu/ac": None,  # depende da cultura; tratado em ``yield_to``
}


def _key(unit: str | None) -> str:
    return (unit or "").strip().lower().replace(" ", "")


def length_to_m(value: float, unit: str) -> float:
    """Converte comprimento para metros."""
    return value * LENGTH_TO_M.get(_key(unit), 1.0)


def speed_to_kmh(value: float, unit: str) -> float:
    """Converte velocidade para km/h."""
    return value * SPEED_TO_KMH.get(_key(unit), 1.0)


def area_to_ha(value: float, unit: str) -> float:
    """Converte área para hectares."""
    return value * AREA_TO_HA.get(_key(unit), 1.0)


def mass_to_kg(value: float, unit: str) -> float:
    """Converte massa para quilogramas."""
    return value * MASS_TO_KG.get(_key(unit), 1.0)


def bushel_kg(crop: str | None) -> float:
    """Peso de um bushel da cultura informada, em kg."""
    return BUSHEL_KG.get(_key(crop), DEFAULT_BUSHEL_KG)


def yield_factor_to_kg_ha(unit: str, crop: str | None = None) -> float:
    """Fator multiplicativo que leva uma unidade de rendimento a kg/ha.

    Aceita as formas ``massa/area`` mais comuns dos monitores, incluindo
    ``bu/ac`` (convertida via peso de teste da cultura) e ``sc/ha``.
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
    """Converte de kg/ha (interno) para a unidade de exibição pedida."""
    factor = yield_factor_to_kg_ha(unit, crop)
    return value_kg_ha / factor if factor else value_kg_ha


def guess_speed_unit(values) -> str:
    """Infere a unidade de velocidade a partir da magnitude da série.

    Colheitadeiras e pulverizadores trabalham entre ~2 e ~25 km/h. Se a
    mediana observada fica abaixo de 4, os dados quase certamente estão em
    m/s; acima de 40, em algo já convertido erroneamente. Entre 4 e 20 é
    ambíguo entre km/h e mph, e aí km/h é o padrão seguro (não altera nada).
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
# Catálogo de unidades selecionáveis pela interface
# ==========================================================================
# O AgroSuite guarda tudo em métrico, mas nem o arquivo de origem nem o
# usuário precisam falar métrico. O catálogo abaixo alimenta os seletores da
# interface em dois momentos distintos:
#
#   * **na importação**, para declarar em que unidade o monitor gravou;
#   * **na exibição e na exportação**, para escolher em que unidade ler o
#     resultado e gravar o arquivo de volta.
#
# Cada entrada traz o fator que leva **da unidade para a interna**. Quem
# exibe divide; quem importa multiplica.

#: Unidades de rendimento e dose (interna: kg/ha).
#: ``needs_crop`` marca as que dependem do peso de teste da cultura.
RATE_UNITS = [
    {"key": "kg/ha",     "label": "kg/ha",                   "factor": 1.0},
    {"key": "t/ha",      "label": "t/ha (tonelada)",         "factor": 1000.0},
    {"key": "sc/ha",     "label": "sc/ha (saca de 60 kg)",   "factor": 60.0},
    {"key": "lb/ac",     "label": "lb/ac (libra por acre)",  "factor": 0.45359237 / 0.40468564224},
    {"key": "bu/ac",     "label": "bu/ac (bushel por acre)", "factor": None, "needs_crop": True},
    {"key": "g/m2",      "label": "g/m² (grama por metro²)", "factor": 10.0},
    {"key": "kg/ac",     "label": "kg/ac",                   "factor": 1.0 / 0.40468564224},
    {"key": "t/ac",      "label": "t/ac",                    "factor": 1000.0 / 0.40468564224},
    {"key": "sc/alq",    "label": "sc/alqueire (60 kg)",     "factor": 60.0 / 2.42},
    {"key": "kg/alq",    "label": "kg/alqueire",             "factor": 1.0 / 2.42},
]

#: Unidades de volume por área — calda, fertilizante líquido (interna: L/ha).
VOLUME_RATE_UNITS = [
    {"key": "L/ha",   "label": "L/ha",                     "factor": 1.0},
    {"key": "gal/ac", "label": "gal/ac (galão americano)",  "factor": 3.785411784 / 0.40468564224},
    {"key": "gal/ha", "label": "gal/ha (galão americano)",  "factor": 3.785411784},
    {"key": "L/ac",   "label": "L/ac",                      "factor": 1.0 / 0.40468564224},
    {"key": "mL/m2",  "label": "mL/m²",                     "factor": 10.0},
]

#: Unidades de contagem por área — semeadura (interna: sementes/ha).
COUNT_RATE_UNITS = [
    {"key": "sementes/ha",  "label": "sementes/ha",          "factor": 1.0},
    {"key": "sementes/ac",  "label": "sementes/ac (seeds/ac)", "factor": 1.0 / 0.40468564224},
    {"key": "mil_sem/ha",   "label": "mil sementes/ha",      "factor": 1000.0},
    {"key": "mil_sem/ac",   "label": "mil sementes/ac",      "factor": 1000.0 / 0.40468564224},
    {"key": "sementes/m2",  "label": "sementes/m²",          "factor": 10_000.0},
]

#: Unidades de área (interna: ha).
AREA_UNITS = [
    {"key": "ha",  "label": "hectare (ha)",          "factor": 1.0},
    {"key": "ac",  "label": "acre (ac)",             "factor": 0.40468564224},
    {"key": "alq", "label": "alqueire paulista",     "factor": 2.42},
    {"key": "m2",  "label": "metro quadrado (m²)",   "factor": 1e-4},
    {"key": "km2", "label": "quilômetro² (km²)",     "factor": 100.0},
]

#: Unidades de massa (interna: kg).
MASS_UNITS = [
    {"key": "kg", "label": "quilograma (kg)",   "factor": 1.0},
    {"key": "t",  "label": "tonelada (t)",      "factor": 1000.0},
    {"key": "lb", "label": "libra (lb)",        "factor": 0.45359237},
    {"key": "sc", "label": "saca de 60 kg",     "factor": 60.0},
    {"key": "g",  "label": "grama (g)",         "factor": 0.001},
    {"key": "bu", "label": "bushel",            "factor": None, "needs_crop": True},
]

#: Unidades de velocidade (interna: km/h).
SPEED_UNITS = [
    {"key": "km/h", "label": "km/h",             "factor": 1.0},
    {"key": "mph",  "label": "mph (milha/hora)", "factor": 1.609344},
    {"key": "m/s",  "label": "m/s",              "factor": 3.6},
    {"key": "kn",   "label": "nó (knot)",        "factor": 1.852},
]

#: Unidades de comprimento (interna: m).
LENGTH_UNITS = [
    {"key": "m",  "label": "metro (m)",     "factor": 1.0},
    {"key": "ft", "label": "pé (ft)",       "factor": 0.3048},
    {"key": "in", "label": "polegada (in)", "factor": 0.0254},
    {"key": "cm", "label": "centímetro (cm)", "factor": 0.01},
    {"key": "yd", "label": "jarda (yd)",    "factor": 0.9144},
]

#: Unidades monetárias — só rótulo, sem conversão entre moedas.
CURRENCIES = [
    {"key": "BRL", "label": "Real (R$)",   "symbol": "R$"},
    {"key": "USD", "label": "Dólar (US$)", "symbol": "US$"},
    {"key": "EUR", "label": "Euro (€)",    "symbol": "€"},
    {"key": "CAD", "label": "Dólar canadense (C$)", "symbol": "C$"},
]

UNIT_GROUPS = {
    "rate_mass": {"label": "Rendimento / dose em massa", "internal": "kg/ha", "units": RATE_UNITS},
    "rate_volume": {"label": "Dose em volume", "internal": "L/ha", "units": VOLUME_RATE_UNITS},
    "rate_count": {"label": "Dose em contagem", "internal": "sementes/ha", "units": COUNT_RATE_UNITS},
    "area": {"label": "Área", "internal": "ha", "units": AREA_UNITS},
    "mass": {"label": "Massa", "internal": "kg", "units": MASS_UNITS},
    "speed": {"label": "Velocidade", "internal": "km/h", "units": SPEED_UNITS},
    "length": {"label": "Comprimento", "internal": "m", "units": LENGTH_UNITS},
}

#: Culturas com peso de teste conhecido, para as unidades em bushel.
CROP_CHOICES = [
    {"key": "corn", "label": "Milho", "bushel_kg": BUSHEL_KG["corn"]},
    {"key": "soybean", "label": "Soja", "bushel_kg": BUSHEL_KG["soybean"]},
    {"key": "wheat", "label": "Trigo", "bushel_kg": BUSHEL_KG["wheat"]},
    {"key": "barley", "label": "Cevada", "bushel_kg": BUSHEL_KG["barley"]},
    {"key": "oats", "label": "Aveia", "bushel_kg": BUSHEL_KG["oats"]},
    {"key": "canola", "label": "Canola", "bushel_kg": BUSHEL_KG["canola"]},
    {"key": "sorghum", "label": "Sorgo", "bushel_kg": BUSHEL_KG["sorghum"]},
    {"key": "rice", "label": "Arroz", "bushel_kg": BUSHEL_KG["rice"]},
    {"key": "sunflower", "label": "Girassol", "bushel_kg": BUSHEL_KG["sunflower"]},
    {"key": "peas", "label": "Ervilha", "bushel_kg": BUSHEL_KG["peas"]},
    {"key": "lentils", "label": "Lentilha", "bushel_kg": BUSHEL_KG["lentils"]},
    {"key": "flax", "label": "Linho", "bushel_kg": BUSHEL_KG["flax"]},
    {"key": "rye", "label": "Centeio", "bushel_kg": BUSHEL_KG["rye"]},
]


def unit_factor(group: str, unit_key: str, crop: str | None = None) -> float:
    """Fator que leva ``unit_key`` à unidade interna do grupo.

    As unidades em bushel dependem da cultura, porque o bushel mede volume:
    um bushel de milho pesa 25,4 kg e um de soja, 27,2 kg. Sem cultura
    declarada, é usado o peso do milho — e a interface avisa.
    """
    group_info = UNIT_GROUPS.get(group)
    if group_info is None:
        raise ValueError(f"Grupo de unidades desconhecido: '{group}'.")

    for entry in group_info["units"]:
        if entry["key"] != unit_key:
            continue
        if entry.get("factor") is not None:
            return float(entry["factor"])
        # Unidades em bushel: o fator sai do peso de teste da cultura.
        weight = bushel_kg(crop)
        if group == "mass":
            return weight
        if group == "rate_mass":
            return weight / AREA_TO_HA["ac"]
        raise ValueError(f"Unidade '{unit_key}' não é conversível no grupo '{group}'.")

    raise ValueError(f"Unidade '{unit_key}' não pertence ao grupo '{group}'.")


def to_internal(value, group: str, unit_key: str, crop: str | None = None):
    """Converte da unidade escolhida para a interna."""
    return value * unit_factor(group, unit_key, crop)


def from_internal(value, group: str, unit_key: str, crop: str | None = None):
    """Converte da unidade interna para a escolhida."""
    factor = unit_factor(group, unit_key, crop)
    return value / factor if factor else value


def unit_catalog() -> dict:
    """Catálogo serializável para os seletores da interface."""
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
                        # O fator das unidades em bushel é resolvido no cliente,
                        # a partir do peso de teste da cultura escolhida.
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
# Predefinições regionais
# ==========================================================================
# Cada região trabalha com um conjunto coerente de unidades, e misturar
# grandezas de sistemas diferentes é a principal fonte de erro de leitura.
# As predefinições abaixo ligam todos os seletores de uma vez; qualquer
# unidade individual continua podendo ser trocada depois.
#
# A predefinição canadense é a inicial do app: nas Pradarias, os monitores
# saem de fábrica em bu/ac para grão, lb/ac para fertilizante e semente,
# gal/ac (galão americano) para calda, e a largura do implemento é falada
# em pés — mesmo o país sendo oficialmente métrico.

UNIT_PRESETS = {
    "canada": {
        "label": "Canadá (Pradarias)",
        "description": (
            "bu/ac para grão, lb/ac para fertilizante e semente, acres, pés e mph — "
            "como os monitores costumam sair configurados no oeste canadense."
        ),
        "yield_unit": "bu/ac",
        "input_rate_unit": "lb/ac",
        "seed_rate_unit": "lb/ac",
        "volume_rate_unit": "gal/ac",
        "count_rate_unit": "sementes/ac",
        "area_unit": "ac",
        "length_unit": "ft",
        "speed_unit": "mph",
        "mass_unit": "lb",
        "currency": "CAD",
        "crop": "canola",
    },
    "usa": {
        "label": "Estados Unidos",
        "description": "Mesmo conjunto imperial do Canadá, com preços em dólar americano.",
        "yield_unit": "bu/ac",
        "input_rate_unit": "lb/ac",
        "seed_rate_unit": "sementes/ac",
        "volume_rate_unit": "gal/ac",
        "count_rate_unit": "sementes/ac",
        "area_unit": "ac",
        "length_unit": "ft",
        "speed_unit": "mph",
        "mass_unit": "lb",
        "currency": "USD",
        "crop": "corn",
    },
    "brasil": {
        "label": "Brasil",
        "description": "Sacas por hectare, quilos, hectares e metros; preços em real.",
        "yield_unit": "sc/ha",
        "input_rate_unit": "kg/ha",
        "seed_rate_unit": "sementes/ha",
        "volume_rate_unit": "L/ha",
        "count_rate_unit": "sementes/ha",
        "area_unit": "ha",
        "length_unit": "m",
        "speed_unit": "km/h",
        "mass_unit": "kg",
        "currency": "BRL",
        "crop": "soybean",
    },
    "metrico": {
        "label": "Métrico puro",
        "description": "Tudo no SI: kg/ha, hectares, metros e km/h.",
        "yield_unit": "kg/ha",
        "input_rate_unit": "kg/ha",
        "seed_rate_unit": "sementes/ha",
        "volume_rate_unit": "L/ha",
        "count_rate_unit": "sementes/ha",
        "area_unit": "ha",
        "length_unit": "m",
        "speed_unit": "km/h",
        "mass_unit": "kg",
        "currency": "EUR",
        "crop": "wheat",
    },
}

#: Predefinição aplicada quando o app abre pela primeira vez.
DEFAULT_PRESET = "canada"
