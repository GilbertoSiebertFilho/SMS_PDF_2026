"""Desenho de ensaios em faixas no padrão DIFM.

Um ensaio DIFM útil precisa de três propriedades que o desenho aqui garante:

* **Casualização em blocos.** As doses são sorteadas dentro de cada bloco de
  faixas consecutivas, de modo que um gradiente de fertilidade atravessando
  o talhão não fique confundido com o efeito da dose.
* **Faixas operáveis.** A largura é múltipla da largura do implemento, senão
  o operador não consegue executar o ensaio.
* **Repetição.** Cada dose aparece uma vez por bloco; o número de blocos é o
  número de repetições.

A saída já sai pronta para virar prescrição em qualquer um dos formatos de
exportação do app.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _principal_direction(polygon) -> float:
    """Ângulo (graus) do lado mais longo do retângulo mínimo que envolve o talhão.

    Faixas paralelas ao lado mais longo ficam mais compridas, o que reduz o
    número de manobras e aumenta a área útil de cada tratamento.
    """
    rectangle = polygon.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)[:4]
    best_length, best_angle = -1.0, 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:] + coords[:1]):
        length = math.hypot(x2 - x1, y2 - y1)
        if length > best_length:
            best_length = length
            best_angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    return best_angle


def design_strips(
    boundary_lonlat: list[tuple[float, float]],
    rates: list[float],
    implement_width_m: float = 12.0,
    passes_per_strip: int = 2,
    blocks: int = 4,
    angle_deg: float | None = None,
    buffer_m: float = 0.0,
    seed: int = 0,
) -> dict[str, Any]:
    """Gera faixas de ensaio sobre um contorno de talhão.

    Parameters
    ----------
    boundary_lonlat:
        Contorno do talhão como lista de ``(lon, lat)`` em WGS84.
    rates:
        Doses a testar. Pelo menos três — abaixo disso não há curva a ajustar.
    implement_width_m:
        Largura de trabalho do implemento que vai aplicar o ensaio.
    passes_per_strip:
        Quantas passadas do implemento compõem cada faixa. Duas ou mais
        deixam a passada central livre do efeito das faixas vizinhas.
    blocks:
        Número de repetições (blocos casualizados).
    angle_deg:
        Direção das faixas. Ausente, usa o lado mais longo do talhão.
    buffer_m:
        Recuo para dentro do contorno, descartando a bordadura.

    Returns
    -------
    dict
        Com ``features`` (GeoJSON das faixas), ``summary`` e ``warnings``.
    """
    from pyproj import Transformer
    from shapely import affinity
    from shapely.geometry import Polygon, mapping
    from shapely.ops import unary_union

    if len(rates) < 3:
        raise ValueError("Um ensaio de resposta precisa de ao menos 3 doses distintas.")
    if len(boundary_lonlat) < 3:
        raise ValueError("Contorno do talhão inválido: menos de 3 vértices.")
    if blocks < 1:
        raise ValueError("O ensaio precisa de ao menos 1 bloco.")

    from ..core.crs import WGS84, pick_metric_crs

    lons = [p[0] for p in boundary_lonlat]
    lats = [p[1] for p in boundary_lonlat]
    metric_crs = pick_metric_crs(lons, lats)
    to_metric = Transformer.from_crs(WGS84, metric_crs, always_xy=True)
    to_wgs = Transformer.from_crs(metric_crs, WGS84, always_xy=True)

    xs, ys = to_metric.transform(lons, lats)
    field = Polygon(zip(xs, ys))
    if not field.is_valid:
        field = field.buffer(0)
    if field.is_empty:
        raise ValueError("Contorno do talhão resultou em polígono vazio.")

    warnings: list[str] = []
    working = field.buffer(-abs(buffer_m)) if buffer_m else field
    if working.is_empty:
        raise ValueError(
            f"O recuo de {buffer_m:g} m consumiu o talhão inteiro. Reduza a bordadura."
        )
    if working.geom_type == "MultiPolygon":
        working = max(working.geoms, key=lambda g: g.area)
        warnings.append("O recuo dividiu o talhão; foi usada a maior parte contínua.")

    angle = angle_deg if angle_deg is not None else _principal_direction(working)
    centroid = working.centroid
    # Gira o talhão para que as faixas fiquem alinhadas com o eixo X.
    aligned = affinity.rotate(working, -angle, origin=centroid, use_radians=False)
    min_x, min_y, max_x, max_y = aligned.bounds

    strip_width = implement_width_m * max(1, int(passes_per_strip))
    total_strips = int(math.floor((max_y - min_y) / strip_width))
    if total_strips < len(rates):
        raise ValueError(
            f"O talhão comporta apenas {total_strips} faixa(s) de {strip_width:g} m, "
            f"e o ensaio precisa de pelo menos {len(rates)} (uma por dose). "
            "Reduza a largura da faixa ou o número de doses."
        )

    usable_blocks = min(blocks, total_strips // len(rates))
    if usable_blocks < blocks:
        warnings.append(
            f"O talhão comporta {usable_blocks} bloco(s) completo(s) em vez dos "
            f"{blocks} pedidos; o ensaio foi ajustado."
        )
    if usable_blocks < 2:
        warnings.append(
            "Com um único bloco não há repetição: a análise não conseguirá "
            "separar efeito da dose de variação natural do talhão."
        )

    rng = np.random.default_rng(seed)
    assignment: list[float] = []
    for _ in range(usable_blocks):
        order = rng.permutation(len(rates))
        assignment.extend(float(rates[i]) for i in order)

    features: list[dict[str, Any]] = []
    areas: list[float] = []
    for index, rate in enumerate(assignment):
        y0 = min_y + index * strip_width
        band = Polygon([
            (min_x - 10, y0), (max_x + 10, y0),
            (max_x + 10, y0 + strip_width), (min_x - 10, y0 + strip_width),
        ])
        piece = aligned.intersection(band)
        if piece.is_empty or piece.area < strip_width * implement_width_m:
            continue
        piece = affinity.rotate(piece, angle, origin=centroid, use_radians=False)

        geoms = piece.geoms if piece.geom_type == "MultiPolygon" else [piece]
        for part in geoms:
            if part.area < strip_width * implement_width_m:
                continue
            ring = [to_wgs.transform(x, y) for x, y in part.exterior.coords]
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]},
                "properties": {
                    "faixa": index + 1,
                    "bloco": index // len(rates) + 1,
                    "dose": round(float(rate), 2),
                    "area_ha": round(part.area / 10_000.0, 3),
                },
            })
            areas.append(part.area / 10_000.0)

    if not features:
        raise ValueError("Nenhuma faixa utilizável foi gerada com esses parâmetros.")

    counts: dict[float, int] = {}
    for feature in features:
        dose = feature["properties"]["dose"]
        counts[dose] = counts.get(dose, 0) + 1

    return {
        "features": {"type": "FeatureCollection", "features": features},
        "summary": {
            "doses": sorted(counts),
            "repeticoes_por_dose": {str(k): v for k, v in sorted(counts.items())},
            "faixas": len(features),
            "blocos": usable_blocks,
            "largura_faixa_m": strip_width,
            "passadas_por_faixa": max(1, int(passes_per_strip)),
            "direcao_graus": round(angle % 180.0, 1),
            "area_total_ha": round(sum(areas), 2),
            "area_media_faixa_ha": round(sum(areas) / len(areas), 3),
            "crs_metrico": metric_crs,
        },
        "warnings": warnings,
    }
