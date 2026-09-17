"""Sistemas de referência de coordenadas.

Todo cálculo métrico do AgroSuite (distância entre pontos, área de faixa,
sobreposição, vizinhança) é feito num CRS projetado em metros. Como os
monitores entregam coordenadas geográficas em WGS84, o AgroSuite escolhe
automaticamente a zona UTM correspondente ao centro do talhão.
"""

from __future__ import annotations

import math

WGS84 = "EPSG:4326"


def utm_epsg(lon: float, lat: float) -> str:
    """Código EPSG da zona UTM que contém o ponto informado.

    326xx no hemisfério norte, 327xx no hemisfério sul.
    """
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise ValueError("Coordenadas inválidas para definir a zona UTM.")
    zone = int(math.floor((lon + 180.0) / 6.0) % 60) + 1
    base = 32600 if lat >= 0 else 32700
    return f"EPSG:{base + zone}"


def pick_metric_crs(lon_series, lat_series) -> str:
    """Escolhe o CRS métrico adequado para um conjunto de pontos."""
    import numpy as np

    lon = np.asarray(lon_series, dtype="float64")
    lat = np.asarray(lat_series, dtype="float64")
    mask = np.isfinite(lon) & np.isfinite(lat)
    if not mask.any():
        raise ValueError("Nenhuma coordenada válida no conjunto de dados.")
    return utm_epsg(float(np.median(lon[mask])), float(np.median(lat[mask])))


def looks_geographic(x_series, y_series) -> bool:
    """Heurística para detectar coordenadas em graus decimais.

    Usada quando o arquivo não traz ``.prj``: valores dentro de
    ±180 / ±90 praticamente só ocorrem em coordenadas geográficas, pois um
    talhão em UTM tem X na casa das centenas de milhares.
    """
    import numpy as np

    x = np.asarray(x_series, dtype="float64")
    y = np.asarray(y_series, dtype="float64")
    mask = np.isfinite(x) & np.isfinite(y)
    if not mask.any():
        return False
    return bool(
        np.nanmax(np.abs(x[mask])) <= 180.0 and np.nanmax(np.abs(y[mask])) <= 90.0
    )
