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
