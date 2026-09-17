"""Linhas de orientação (AB).

Uma linha AB é o par de pontos que define a direção de trabalho; o monitor
gera as passadas paralelas a partir dela, espaçadas pela largura do
implemento. Quando o ensaio em faixas é desenhado numa direção, a linha AB
precisa seguir exatamente essa direção — senão as passadas do operador
cruzam as faixas e o experimento se perde.
"""

from __future__ import annotations

import math
from typing import Any


def ab_line_from_direction(
    boundary_lonlat: list[tuple[float, float]],
    angle_deg: float,
    name: str = "AB",
    extend_m: float = 100.0,
) -> dict[str, Any]:
    """Cria uma linha AB atravessando o talhão numa direção dada.

    Parameters
    ----------
    angle_deg:
        Direção em graus **matemáticos** — 0 aponta para leste, crescendo no
        sentido anti-horário. É o mesmo ângulo devolvido pelo desenho de
        ensaio, para que a linha e as faixas fiquem alinhadas.
    extend_m:
        Quanto prolongar a linha além do talhão, em metros. Sobra é boa: o
        monitor precisa da referência antes da máquina entrar na área.
    """
    from pyproj import Transformer
    from shapely.geometry import Polygon

    from .crs import WGS84, pick_metric_crs

    if len(boundary_lonlat) < 3:
        raise ValueError("Contorno insuficiente para gerar uma linha AB.")

    lons = [p[0] for p in boundary_lonlat]
    lats = [p[1] for p in boundary_lonlat]
    metric_crs = pick_metric_crs(lons, lats)
    to_metric = Transformer.from_crs(WGS84, metric_crs, always_xy=True)
    to_wgs = Transformer.from_crs(metric_crs, WGS84, always_xy=True)

    xs, ys = to_metric.transform(lons, lats)
    field = Polygon(zip(xs, ys))
    if not field.is_valid:
        field = field.buffer(0)
    centroid = field.centroid

    # Meia diagonal do retângulo envolvente garante que a linha atravesse o
    # talhão inteiro em qualquer direção.
    min_x, min_y, max_x, max_y = field.bounds
    half = math.hypot(max_x - min_x, max_y - min_y) / 2.0 + extend_m

    rad = math.radians(angle_deg)
    dx, dy = math.cos(rad) * half, math.sin(rad) * half
    a = to_wgs.transform(centroid.x - dx, centroid.y - dy)
    b = to_wgs.transform(centroid.x + dx, centroid.y + dy)

    # Rumo de bússola: 0 = norte, crescendo no sentido horário.
    heading = (90.0 - angle_deg) % 360.0
    return {
        "name": name,
        "type": 1,  # linha AB
        "a": (float(a[0]), float(a[1])),
        "b": (float(b[0]), float(b[1])),
        "heading": round(heading, 2),
    }


def ab_line_from_points(
    a_lonlat: tuple[float, float],
    b_lonlat: tuple[float, float],
    name: str = "AB",
) -> dict[str, Any]:
    """Monta uma linha AB a partir de dois pontos escolhidos no mapa."""
    from pyproj import Transformer

    from .crs import WGS84, pick_metric_crs

    metric_crs = pick_metric_crs([a_lonlat[0], b_lonlat[0]], [a_lonlat[1], b_lonlat[1]])
    to_metric = Transformer.from_crs(WGS84, metric_crs, always_xy=True)
    ax, ay = to_metric.transform(*a_lonlat)
    bx, by = to_metric.transform(*b_lonlat)

    length = math.hypot(bx - ax, by - ay)
    if length < 1.0:
        raise ValueError(
            "Os pontos A e B estão a menos de um metro um do outro; a direção "
            "resultante seria imprecisa. Afaste-os ao longo da passada."
        )
    heading = (math.degrees(math.atan2(bx - ax, by - ay)) + 360.0) % 360.0
    return {
        "name": name,
        "type": 1,
        "a": (float(a_lonlat[0]), float(a_lonlat[1])),
        "b": (float(b_lonlat[0]), float(b_lonlat[1])),
        "heading": round(heading, 2),
        "length_m": round(length, 1),
    }


def ab_lines_to_geojson(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Converte linhas AB em GeoJSON, para desenhar no mapa e exportar."""
    features = []
    for line in lines:
        if line.get("a") and line.get("b"):
            coordinates = [list(line["a"]), list(line["b"])]
        elif line.get("points"):
            coordinates = [list(p) for p in line["points"]]
        else:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coordinates},
            "properties": {
                "name": line.get("name"),
                "type": line.get("type", 1),
                "heading": line.get("heading"),
            },
        })
    return {"type": "FeatureCollection", "features": features}
