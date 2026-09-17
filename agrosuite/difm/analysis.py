"""Análise DIFM de ensaios em faixas.

DIFM (*Data-Intensive Farm Management*) trata o talhão comercial como o
próprio experimento: faixas de doses diferentes são aplicadas com a
máquina do produtor e a resposta é lida no monitor de colheita. A análise
precisa de três cuidados que a diferenciam de um experimento de parcelas:

1. **Agregação.** Ponto a ponto, o erro de GPS e o ruído do sensor dominam.
   Os dados são agregados em células ou em trechos de faixa antes de ajustar
   qualquer curva.
2. **Borda de faixa.** Onde duas doses se encontram há mistura e efeito de
   vizinhança. Uma margem interna descarta essa zona.
3. **Heterogeneidade.** A dose ótima muda dentro do talhão. Analisar por zona
   é o que justifica economicamente a taxa variável.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..core import schema as sch
from ..core.dataset import Dataset
from . import response as response_mod


def rate_levels(rates: np.ndarray, max_levels: int = 12) -> tuple[np.ndarray, list[float]]:
    """Reduz as doses observadas aos níveis de tratamento do ensaio.

    Um ensaio em faixas tem um punhado de doses planejadas, mas o log da
    máquina registra pequenas oscilações em torno de cada uma. Agrupar pelo
    valor bruto criaria dezenas de "doses" de um ponto só; agrupar pelo nível
    devolve o desenho real do experimento.

    Returns
    -------
    (nivel_por_ponto, lista_de_niveis)
        ``nivel_por_ponto`` traz a dose nominal de cada registro.
    """
    rates = np.asarray(rates, dtype="float64")
    finite = rates[np.isfinite(rates)]
    if finite.size == 0:
        return rates, []

    rounded = np.round(finite, 1)
    unique = np.unique(rounded)
    if unique.size <= max_levels:
        levels = unique
    else:
        # Muitas doses distintas: agrupa por quantis, preservando a ordem.
        edges = np.quantile(finite, np.linspace(0, 1, max_levels + 1))
        edges = np.unique(edges)
        centers = (edges[:-1] + edges[1:]) / 2.0
        levels = centers

    assigned = levels[np.argmin(np.abs(rates[:, None] - levels[None, :]), axis=1)]
    assigned = np.where(np.isfinite(rates), assigned, np.nan)
    return assigned, [float(v) for v in levels]


def aggregate_cells(
    df: pd.DataFrame,
    cell_m: float = 20.0,
    rate_column: str = sch.APPLIED_RATE,
    value_column: str = sch.VALUE,
    group_columns: tuple[str, ...] = (),
    min_points: int = 3,
) -> pd.DataFrame:
    """Agrega os registros em células quadradas de ``cell_m`` metros.

    A média dentro da célula absorve o erro de posicionamento e o ruído do
    sensor. A agregação inclui o **nível de dose** na chave de agrupamento:
    sem isso, uma célula que pega duas faixas vizinhas produziria uma dose
    média que ninguém aplicou, e o ponto resultante não pertenceria a
    nenhum tratamento do ensaio.
    """
    required = {sch.X, sch.Y, rate_column, value_column}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Colunas ausentes para agregar: {', '.join(sorted(missing))}.")

    work = df.copy()
    work[rate_column] = pd.to_numeric(work[rate_column], errors="coerce")
    work[value_column] = pd.to_numeric(work[value_column], errors="coerce")
    work = work.dropna(subset=[sch.X, sch.Y, rate_column, value_column])
    if work.empty:
        raise ValueError("Nenhum registro com dose e rendimento simultaneamente válidos.")

    work["_nivel"], _ = rate_levels(work[rate_column].to_numpy())
    work["_col"] = np.floor(work[sch.X] / cell_m).astype("int64")
    work["_row"] = np.floor(work[sch.Y] / cell_m).astype("int64")

    keys = ["_col", "_row", "_nivel"] + [c for c in group_columns if c in work.columns]
    aggregation = {
        value_column: ["mean", "std", "count"],
        rate_column: ["mean", "std"],
        sch.X: "mean",
        sch.Y: "mean",
        sch.LON: "mean",
        sch.LAT: "mean",
    }
    aggregation = {k: v for k, v in aggregation.items() if k in work.columns}
    grouped = work.groupby(keys, observed=True).agg(aggregation)
    grouped.columns = ["_".join(c).strip("_") for c in grouped.columns]
    grouped = grouped.reset_index()

    rename = {
        f"{value_column}_mean": "rendimento",
        f"{value_column}_std": "rendimento_dp",
        f"{value_column}_count": "n",
        f"{rate_column}_mean": "dose_media",
        f"{rate_column}_std": "dose_dp",
        f"{sch.X}_mean": sch.X,
        f"{sch.Y}_mean": sch.Y,
        f"{sch.LON}_mean": sch.LON,
        f"{sch.LAT}_mean": sch.LAT,
    }
    grouped = grouped.rename(columns={k: v for k, v in rename.items() if k in grouped.columns})
    grouped = grouped.rename(columns={"_nivel": "dose"})
    grouped = grouped[grouped["n"] >= min_points]
    if grouped.empty:
        raise ValueError(
            f"Nenhuma célula de {cell_m:g} m reuniu ao menos {min_points} registros. "
            "Reduza o tamanho da célula ou o mínimo exigido."
        )
    return grouped


def drop_strip_edges(
    df: pd.DataFrame,
    rate_column: str = sch.APPLIED_RATE,
    margin_m: float = 6.0,
) -> tuple[pd.DataFrame, int]:
    """Descarta registros próximos à transição entre doses vizinhas.

    Na fronteira entre duas faixas a aplicação se mistura e a cultura sofre
    efeito da parcela vizinha. Manter esses pontos achata a curva de resposta
    e puxa a dose ótima para o centro da faixa testada.

    A fronteira não fica sobre os pontos, e sim **entre** duas passadas: se o
    registro mais próximo com dose diferente está a ``d`` metros, a transição
    está a aproximadamente ``d/2``. É essa distância — e não ``d`` — que é
    comparada com a margem, senão nenhum ponto seria marcado quando o
    espaçamento entre passadas excede a margem pedida.
    """
    if margin_m <= 0 or sch.X not in df.columns:
        return df, 0

    from scipy.spatial import cKDTree

    work = df.dropna(subset=[sch.X, sch.Y, rate_column])
    if len(work) < 10:
        return df, 0

    points = np.column_stack([work[sch.X].to_numpy(), work[sch.Y].to_numpy()])
    levels, level_values = rate_levels(work[rate_column].to_numpy())
    if len(level_values) < 2:
        return df, 0

    boundary_distance = np.full(len(work), np.inf)
    for level in level_values:
        this = levels == level
        other = ~this
        if not this.any() or not other.any():
            continue
        tree = cKDTree(points[other])
        distance, _ = tree.query(points[this], k=1, workers=-1)
        boundary_distance[this] = distance / 2.0

    near_edge = boundary_distance < margin_m
    keep_index = work.index[~near_edge]
    dropped = int(near_edge.sum())
    # Uma margem que engole o ensaio inteiro é erro de parâmetro, não limpeza.
    if dropped >= len(work) * 0.9:
        return df, 0
    return df.loc[keep_index], dropped


def analyze(
    dataset: Dataset,
    rate_column: str = sch.APPLIED_RATE,
    value_column: str = sch.VALUE,
    crop_price: float = 1.0,
    input_cost: float = 0.0,
    cell_m: float = 20.0,
    edge_margin_m: float = 6.0,
    zone_column: str | None = None,
    models: list[str] | None = None,
    rate_max: float | None = None,
) -> dict[str, Any]:
    """Executa a análise DIFM completa e devolve o laudo.

    O laudo traz a curva ajustada, a dose econômica ótima, a comparação
    entre taxa uniforme e taxa variável por zona, e o resumo por dose
    aplicada — que é a leitura mais direta para conferir se o ensaio saiu
    como planejado.
    """
    df = dataset.df
    if rate_column not in df.columns:
        raise ValueError(
            f"Coluna de dose '{rate_column}' não encontrada. Colunas numéricas "
            f"disponíveis: {', '.join(dataset.numeric_columns())}."
        )
    if value_column not in df.columns:
        raise ValueError(f"Coluna de rendimento '{value_column}' não encontrada.")

    notes: list[str] = []
    trimmed, dropped = drop_strip_edges(df, rate_column, edge_margin_m)
    if dropped:
        notes.append(
            f"{dropped} registros descartados por caírem dentro da margem de borda "
            "das faixas, onde as doses vizinhas se misturam."
        )

    group_columns = (zone_column,) if zone_column and zone_column in df.columns else ()
    cells = aggregate_cells(
        trimmed, cell_m=cell_m, rate_column=rate_column,
        value_column=value_column, group_columns=group_columns,
    )
    notes.append(f"{len(cells)} células utilizadas no ajuste da curva.")

    rates = cells["dose"].to_numpy(dtype="float64")
    yields = cells["rendimento"].to_numpy(dtype="float64")
    observed_max = float(np.max(rates))
    search_max = float(rate_max) if rate_max else observed_max

    best, all_fits = response_mod.fit_best(rates, yields, models)

    economics: dict[str, Any] = {}
    curve: list[dict[str, float]] = []
    if crop_price > 0:
        economics = response_mod.optimum_rate(
            best, crop_price, input_cost, rate_min=0.0, rate_max=search_max
        )
        curve = response_mod.profit_curve(
            best, crop_price, input_cost, rate_min=0.0, rate_max=search_max
        )
        if economics["dose_otima"] > observed_max - 1e-6:
            notes.append(
                "A dose ótima caiu no limite superior da faixa testada: o ensaio "
                "não chegou a mostrar o ponto de retorno decrescente. Trate o "
                "valor como 'pelo menos isso' e inclua doses maiores no próximo ano."
            )

    by_rate = (
        cells.groupby("dose", observed=True)
        .agg(n=("rendimento", "size"), rendimento_medio=("rendimento", "mean"),
             desvio=("rendimento", "std"))
        .reset_index()
        .rename(columns={"dose": "dose"})
    )
    if crop_price > 0:
        by_rate["lucro_medio"] = (
            by_rate["rendimento_medio"] * crop_price - by_rate["dose"] * input_cost
        )
    rate_table = [
        {k: (round(float(v), 2) if isinstance(v, (int, float, np.floating)) and pd.notna(v) else None)
         for k, v in row.items()}
        for row in by_rate.to_dict("records")
    ]

    zones = _zone_analysis(
        cells, zone_column, crop_price, input_cost, search_max, models
    ) if group_columns else None

    return {
        "coluna_dose": rate_column,
        "coluna_rendimento": value_column,
        "celulas": len(cells),
        # Parâmetros ecoados em unidade interna, para a interface reexibi-los
        # na unidade que o usuário escolheu.
        "parametros": {
            "cell_m": cell_m,
            "edge_margin_m": edge_margin_m,
            "registros_descartados_borda": dropped,
        },
        "doses_testadas": sorted({round(float(r), 1) for r in np.unique(rates)}),
        "modelo_escolhido": best.to_dict(),
        "modelos_avaliados": [f.to_dict() for f in all_fits],
        "economia": economics,
        "curva": curve,
        "por_dose": rate_table,
        "zonas": zones,
        "observacoes": notes,
        "precos": {"preco_produto": crop_price, "custo_insumo": input_cost},
        "celulas_geojson": _cells_geojson(cells),
    }


def _brl(value: float) -> str:
    """Formata um valor em reais no padrão brasileiro (1.234,56)."""
    formatted = f"{value:,.2f}"
    return "R$ " + formatted.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def _zone_analysis(
    cells: pd.DataFrame,
    zone_column: str,
    crop_price: float,
    input_cost: float,
    rate_max: float,
    models: list[str] | None,
) -> dict[str, Any]:
    """Ajusta uma curva por zona e compara taxa uniforme com taxa variável.

    O ganho da taxa variável é a diferença entre somar o lucro de cada zona
    na sua própria dose ótima e aplicar a todas as zonas a melhor dose única.
    Se essa diferença não paga o trabalho de gerar e carregar o mapa, a taxa
    uniforme é a decisão correta — e o laudo diz isso.
    """
    results: list[dict[str, Any]] = []
    weights: list[float] = []
    fits: list[response_mod.ResponseFit] = []

    for zone_value, group in cells.groupby(zone_column, observed=True):
        rates = group["dose"].to_numpy(dtype="float64")
        yields = group["rendimento"].to_numpy(dtype="float64")
        if len(np.unique(rates)) < 3:
            results.append({
                "zona": str(zone_value),
                "celulas": int(len(group)),
                "erro": "Menos de 3 doses distintas nesta zona.",
            })
            continue
        try:
            fit, _ = response_mod.fit_best(rates, yields, models)
        except ValueError as exc:
            results.append({"zona": str(zone_value), "celulas": int(len(group)), "erro": str(exc)})
            continue

        entry: dict[str, Any] = {
            "zona": str(zone_value),
            "celulas": int(len(group)),
            "modelo": fit.label,
            "r2": round(fit.r2, 4),
        }
        if crop_price > 0:
            optimum = response_mod.optimum_rate(fit, crop_price, input_cost, 0.0, rate_max)
            entry.update(optimum)
        results.append(entry)
        weights.append(float(len(group)))
        fits.append(fit)

    comparison: dict[str, Any] = {}
    if crop_price > 0 and len(fits) >= 2:
        total_weight = sum(weights)
        grid = np.linspace(0.0, rate_max, 1001)

        variable_profit = sum(
            w * max(fit.predict(grid) * crop_price - grid * input_cost)
            for fit, w in zip(fits, weights)
        ) / total_weight

        uniform_profit_by_rate = sum(
            w * (fit.predict(grid) * crop_price - grid * input_cost)
            for fit, w in zip(fits, weights)
        ) / total_weight
        best_uniform_index = int(np.argmax(uniform_profit_by_rate))

        gain = float(variable_profit - uniform_profit_by_rate[best_uniform_index])
        comparison = {
            "dose_unica_otima": round(float(grid[best_uniform_index]), 2),
            "lucro_taxa_unica": round(float(uniform_profit_by_rate[best_uniform_index]), 2),
            "lucro_taxa_variavel": round(float(variable_profit), 2),
            "ganho_por_ha": round(gain, 2),
            "leitura": (
                f"A taxa variável por zona rende {_brl(gain)}/ha a mais que a melhor "
                f"dose única. Compare esse valor com o custo de gerar e operar o mapa "
                f"antes de decidir."
            )
            if gain > 0 else
            "Neste conjunto a taxa variável não superou a melhor dose única: as zonas "
            "responderam de forma parecida demais para justificar o mapa.",
        }

    return {"coluna_zona": zone_column, "por_zona": results, "comparacao": comparison}


def _cells_geojson(cells: pd.DataFrame, limit: int = 8000) -> dict[str, Any]:
    """Células agregadas como GeoJSON para desenhar no mapa."""
    if sch.LON not in cells.columns or sch.LAT not in cells.columns:
        return {"type": "FeatureCollection", "features": []}
    sample = cells if len(cells) <= limit else cells.sample(limit, random_state=0)
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(row[sch.LON]), float(row[sch.LAT])]},
            "properties": {
                "dose": round(float(row["dose"]), 2),
                "rendimento": round(float(row["rendimento"]), 1),
                "n": int(row["n"]),
            },
        }
        for _, row in sample.iterrows()
    ]
    return {"type": "FeatureCollection", "features": features}
