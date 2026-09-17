"""Filtros de limpeza de dados de monitor.

Cada etapa é uma classe com a mesma interface: recebe o contexto com os
vetores já preparados e devolve uma máscara booleana marcando os registros
a **remover**, mais as estatísticas que alimentam o laudo.

A sequência segue a prática consolidada na literatura de edição de mapas de
rendimento (filtros de posição → tempo → largura → sobreposição → bordadura
→ estatísticos), porque cada etapa depende da anterior ter saneado o que
alimenta seus cálculos: não adianta procurar outlier local antes de remover
os pontos de manobra, que são justamente os que puxam a vizinhança.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..core import schema as sch

#: Valor sentinela para "nenhum ponto anterior cobriu esta célula".
_NO_COVER = np.iinfo(np.int32).max


@dataclass
class StepResult:
    """Resultado de uma etapa de limpeza."""

    key: str
    label: str
    removed: int
    remaining: int
    detail: str = ""
    skipped: bool = False
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "removed": int(self.removed),
            "remaining": int(self.remaining),
            "detail": self.detail,
            "skipped": self.skipped,
            "params": self.params,
        }


class Context:
    """Estado compartilhado entre as etapas de uma execução de limpeza.

    Mantém a máscara de registros ainda válidos, o motivo da remoção de cada
    registro descartado e caches caros (grade de cobertura, vizinhança
    espacial) que várias etapas reaproveitam.
    """

    def __init__(self, df: pd.DataFrame, value_column: str = sch.VALUE) -> None:
        self.df = df
        self.value_column = value_column
        self.n = len(df)
        self.alive = np.ones(self.n, dtype=bool)
        self.reason = np.full(self.n, "", dtype=object)
        self._coverage: dict[str, Any] | None = None
        self._kdtree = None
        self.messages: list[str] = []

    # -- acesso a colunas ------------------------------------------------
    def column(self, name: str) -> np.ndarray | None:
        """Vetor float da coluna, ou ``None`` se ausente."""
        if name not in self.df.columns:
            return None
        return pd.to_numeric(self.df[name], errors="coerce").to_numpy(dtype="float64")

    @property
    def values(self) -> np.ndarray | None:
        return self.column(self.value_column)

    def apply(self, remove: np.ndarray, reason: str) -> int:
        """Descarta os registros marcados, registrando o motivo."""
        remove = np.asarray(remove, dtype=bool) & self.alive
        count = int(remove.sum())
        if count:
            self.reason[remove] = reason
            self.alive[remove] = False
        return count

    # -- caches ----------------------------------------------------------
    def coverage(self, cell_size: float | None = None) -> dict[str, Any] | None:
        """Grade de cobertura do talhão, construída sob demanda.

        Para cada célula guarda o índice do **primeiro** registro que a
        cobriu. Com isso, a sobreposição de um registro é simplesmente a
        fração das suas células já cobertas por registros anteriores — o que
        evita unir dezenas de milhares de polígonos.
        """
        if self._coverage is not None:
            return self._coverage

        x = self.column(sch.X)
        y = self.column(sch.Y)
        swath = self.column(sch.SWATH)
        if x is None or y is None or swath is None:
            return None

        valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(swath) & (swath > 0)
        if valid.sum() < 10:
            return None

        median_swath = float(np.median(swath[valid]))
        cell = cell_size or max(0.5, min(2.0, median_swath / 8.0))

        x0, x1 = float(np.min(x[valid])), float(np.max(x[valid]))
        y0, y1 = float(np.min(y[valid])), float(np.max(y[valid]))
        margin = median_swath
        x0, y0 = x0 - margin, y0 - margin
        x1, y1 = x1 + margin, y1 + margin

        # Limita a memória da grade ajustando a célula se preciso.
        max_cells = 24_000_000
        while ((x1 - x0) / cell + 1) * ((y1 - y0) / cell + 1) > max_cells:
            cell *= 1.5

        ncols = int((x1 - x0) / cell) + 1
        nrows = int((y1 - y0) / cell) + 1

        # Amostras transversais à direção de deslocamento cobrem a faixa.
        heading = self.column(sch.HEADING)
        if heading is None:
            heading = np.zeros(self.n)
        rad = np.radians(np.nan_to_num(heading))
        # Rumo 0 = norte; o vetor perpendicular à direção é (cos, -sin).
        perp_x, perp_y = np.cos(rad), -np.sin(rad)

        samples = max(3, int(np.ceil(median_swath / cell)) + 1)
        offsets = np.linspace(-0.5, 0.5, samples)

        half = np.where(valid, swath, median_swath)
        sample_x = x[:, None] + perp_x[:, None] * half[:, None] * offsets[None, :]
        sample_y = y[:, None] + perp_y[:, None] * half[:, None] * offsets[None, :]

        col = np.clip(((sample_x - x0) / cell).astype(np.int64), 0, ncols - 1)
        row = np.clip(((sample_y - y0) / cell).astype(np.int64), 0, nrows - 1)
        flat = (row * ncols + col).ravel()

        point_index = np.repeat(np.arange(self.n, dtype=np.int64), samples)
        invalid = ~np.repeat(valid, samples)
        point_index = point_index.copy()
        point_index[invalid] = _NO_COVER

        first_cover = np.full(nrows * ncols, _NO_COVER, dtype=np.int64)
        np.minimum.at(first_cover, flat, point_index)

        self._coverage = {
            "cell": cell,
            "ncols": ncols,
            "nrows": nrows,
            "x0": x0,
            "y0": y0,
            "flat": flat,
            "samples": samples,
            "first_cover": first_cover,
            "valid": valid,
        }
        return self._coverage

    def neighbors(self, k: int = 12):
        """Índices dos ``k`` vizinhos mais próximos de cada registro vivo."""
        if self._kdtree is not None:
            return self._kdtree

        from scipy.spatial import cKDTree

        x = self.column(sch.X)
        y = self.column(sch.Y)
        if x is None or y is None:
            return None
        alive_idx = np.flatnonzero(self.alive & np.isfinite(x) & np.isfinite(y))
        if alive_idx.size < k + 1:
            return None
        points = np.column_stack([x[alive_idx], y[alive_idx]])
        tree = cKDTree(points)
        _, idx = tree.query(points, k=min(k + 1, alive_idx.size), workers=-1)
        self._kdtree = (alive_idx, idx[:, 1:])  # descarta o próprio ponto
        return self._kdtree


# ==========================================================================
# Etapas
# ==========================================================================

class CleaningStep:
    """Contrato comum das etapas de limpeza."""

    key = "step"
    label = "Etapa"
    description = ""
    defaults: dict[str, Any] = {}

    def __init__(self, **params: Any) -> None:
        self.params = {**self.defaults, **params}

    def run(self, ctx: Context) -> StepResult:  # pragma: no cover - interface
        raise NotImplementedError

    def _result(self, removed: int, ctx: Context, detail: str = "", skipped: bool = False):
        return StepResult(
            key=self.key, label=self.label, removed=removed,
            remaining=int(ctx.alive.sum()), detail=detail, skipped=skipped,
            params=dict(self.params),
        )


class NullValueFilter(CleaningStep):
    """Descarta registros sem leitura válida da variável."""

    key = "null_value"
    label = "Valores nulos e não positivos"
    description = (
        "Remove registros sem leitura ou com valor ≤ 0. São paradas, trechos "
        "com o implemento levantado e falhas de sensor — nunca produção real."
    )
    defaults = {"drop_zero": True, "drop_negative": True}

    def run(self, ctx: Context) -> StepResult:
        values = ctx.values
        if values is None:
            return self._result(0, ctx, "Coluna de valor ausente.", skipped=True)
        remove = ~np.isfinite(values)
        if self.params.get("drop_negative", True):
            remove |= values < 0
        if self.params.get("drop_zero", True):
            remove |= values == 0
        return self._result(ctx.apply(remove, self.label), ctx)


class ValueRangeFilter(CleaningStep):
    """Aplica limites absolutos, definidos pelo agrônomo."""

    key = "value_range"
    label = "Faixa absoluta da variável"
    description = (
        "Corta valores fora do intervalo fisicamente plausível para a cultura. "
        "Deixe em branco para não aplicar um dos limites."
    )
    defaults = {"min": None, "max": None}

    def run(self, ctx: Context) -> StepResult:
        values = ctx.values
        if values is None:
            return self._result(0, ctx, "Coluna de valor ausente.", skipped=True)
        lo, hi = self.params.get("min"), self.params.get("max")
        if lo is None and hi is None:
            return self._result(0, ctx, "Sem limites definidos.", skipped=True)
        remove = np.zeros(ctx.n, dtype=bool)
        if lo is not None:
            remove |= values < float(lo)
        if hi is not None:
            remove |= values > float(hi)
        remove &= np.isfinite(values)
        return self._result(ctx.apply(remove, self.label), ctx)


class SpeedRangeFilter(CleaningStep):
    """Remove registros fora da faixa operacional de velocidade."""

    key = "speed_range"
    label = "Faixa de velocidade"
    description = (
        "Abaixo do mínimo a máquina está parando ou manobrando; acima do "
        "máximo é deslocamento em estrada ou erro de GPS."
    )
    defaults = {"min": 1.5, "max": 20.0}

    def run(self, ctx: Context) -> StepResult:
        speed = ctx.column(sch.SPEED)
        if speed is None:
            return self._result(0, ctx, "Velocidade indisponível.", skipped=True)
        lo = float(self.params.get("min") or 0)
        hi = float(self.params.get("max") or 1e9)
        remove = np.isfinite(speed) & ((speed < lo) | (speed > hi))
        remove |= ~np.isfinite(speed)
        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"Faixa aceita: {lo:g}–{hi:g} km/h.",
        )


class SpeedChangeFilter(CleaningStep):
    """Remove registros durante aceleração ou frenagem brusca.

    Numa colheitadeira, a massa de grão em trânsito dentro da máquina faz o
    sensor continuar registrando o fluxo da velocidade anterior. O resultado
    são picos e vales artificiais sempre que a velocidade muda depressa.
    """

    key = "speed_change"
    label = "Variação brusca de velocidade"
    description = (
        "Descarta registros em que a velocidade variou acima do limite entre "
        "leituras consecutivas — a inércia do fluxo distorce o valor medido."
    )
    defaults = {"max_change_pct": 25.0}

    def run(self, ctx: Context) -> StepResult:
        speed = ctx.column(sch.SPEED)
        if speed is None:
            return self._result(0, ctx, "Velocidade indisponível.", skipped=True)
        limit = float(self.params.get("max_change_pct", 25.0)) / 100.0
        previous = np.roll(speed, 1)
        previous[0] = speed[0]
        with np.errstate(divide="ignore", invalid="ignore"):
            change = np.abs(speed - previous) / np.where(previous > 0, previous, np.nan)
        remove = np.isfinite(change) & (change > limit)
        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"Variação máxima tolerada: {limit * 100:g}%.",
        )


class SwathWidthFilter(CleaningStep):
    """Remove passadas com plataforma parcialmente cheia."""

    key = "swath_partial"
    label = "Faixa parcial"
    description = (
        "Quando a plataforma não trabalha na largura cheia, o monitor divide "
        "a massa por uma área maior que a real e o valor cai artificialmente."
    )
    defaults = {"min_fraction": 0.5}

    def run(self, ctx: Context) -> StepResult:
        swath = ctx.column(sch.SWATH)
        if swath is None:
            return self._result(0, ctx, "Largura de faixa indisponível.", skipped=True)
        valid = np.isfinite(swath) & (swath > 0)
        if valid.sum() < 10:
            return self._result(0, ctx, "Larguras insuficientes.", skipped=True)
        full = float(np.percentile(swath[valid], 90))
        fraction = float(self.params.get("min_fraction", 0.5))
        remove = valid & (swath < full * fraction)
        remove |= ~valid
        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"Largura cheia estimada: {full:.2f} m; mínimo aceito: {full * fraction:.2f} m.",
        )


class OverlapFilter(CleaningStep):
    """Remove registros sobre área já trabalhada.

    Ao repassar por cima de uma faixa já colhida, a plataforma recolhe pouco
    ou nenhum produto, mas o monitor continua contando a área cheia. É a
    principal fonte de valores baixos espúrios num mapa de rendimento.
    """

    key = "overlap"
    label = "Sobreposição de faixas"
    description = (
        "Compara a área coberta por cada registro com o que já havia sido "
        "trabalhado antes e descarta o que excede a fração tolerada."
    )
    defaults = {"max_overlap_pct": 40.0}

    def run(self, ctx: Context) -> StepResult:
        coverage = ctx.coverage()
        if coverage is None:
            return self._result(0, ctx, "Sem geometria de faixa para calcular.", skipped=True)

        first_cover = coverage["first_cover"]
        flat = coverage["flat"]
        samples = coverage["samples"]
        point_index = np.repeat(np.arange(ctx.n, dtype=np.int64), samples)
        prior = first_cover[flat] < point_index
        fraction = prior.reshape(ctx.n, samples).mean(axis=1)

        limit = float(self.params.get("max_overlap_pct", 40.0)) / 100.0
        remove = coverage["valid"] & (fraction > limit)
        removed = ctx.apply(remove, self.label)
        return self._result(
            removed, ctx,
            detail=(
                f"Célula de {coverage['cell']:.2f} m; tolerância de "
                f"{limit * 100:g}% de área repetida."
            ),
        )


class BoundaryFilter(CleaningStep):
    """Remove a bordadura (cabeceiras e contorno do talhão).

    A distância até a borda é medida sobre a própria grade de cobertura:
    uma transformada de distância dá, para cada célula trabalhada, quantos
    metros faltam até a área não trabalhada. Isso acompanha talhões de
    formato irregular, o que um simples envoltório convexo não faria.
    """

    key = "boundary"
    label = "Bordadura do talhão"
    description = (
        "Descarta registros a menos da distância informada da borda da área "
        "trabalhada — onde há manobra, compactação e sobreposição."
    )
    defaults = {"buffer_m": 0.0}

    def run(self, ctx: Context) -> StepResult:
        buffer_m = float(self.params.get("buffer_m") or 0.0)
        if buffer_m <= 0:
            return self._result(0, ctx, "Desativado.", skipped=True)

        coverage = ctx.coverage()
        if coverage is None:
            return self._result(0, ctx, "Sem geometria de faixa para calcular.", skipped=True)

        from scipy import ndimage

        nrows, ncols = coverage["nrows"], coverage["ncols"]
        covered = (coverage["first_cover"] != _NO_COVER).reshape(nrows, ncols)
        distance = ndimage.distance_transform_edt(covered) * coverage["cell"]

        x = ctx.column(sch.X)
        y = ctx.column(sch.Y)
        col = np.clip(((x - coverage["x0"]) / coverage["cell"]).astype(np.int64), 0, ncols - 1)
        row = np.clip(((y - coverage["y0"]) / coverage["cell"]).astype(np.int64), 0, nrows - 1)
        point_distance = distance[row, col]

        remove = coverage["valid"] & (point_distance < buffer_m)
        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"Faixa de {buffer_m:g} m a partir da borda trabalhada.",
        )


class PassEndsFilter(CleaningStep):
    """Remove o início e o fim de cada passada.

    Na entrada da passada o fluxo ainda não estabilizou; na saída, o que
    resta dentro da máquina continua sendo pesado sobre uma área que já
    acabou. Os dois trechos produzem valores sem relação com o local.
    """

    key = "pass_ends"
    label = "Início e fim de passada"
    description = (
        "Descarta os primeiros e os últimos metros de cada passada, onde o "
        "fluxo dentro da máquina ainda não corresponde ao ponto medido."
    )
    defaults = {"start_m": 6.0, "end_m": 6.0}

    def run(self, ctx: Context) -> StepResult:
        if sch.PASS not in ctx.df.columns:
            return self._result(0, ctx, "Passadas não identificadas.", skipped=True)
        distance = ctx.column(sch.DISTANCE)
        if distance is None:
            return self._result(0, ctx, "Distância entre registros indisponível.", skipped=True)

        start_m = float(self.params.get("start_m") or 0.0)
        end_m = float(self.params.get("end_m") or 0.0)
        if start_m <= 0 and end_m <= 0:
            return self._result(0, ctx, "Desativado.", skipped=True)

        step = np.nan_to_num(distance, nan=0.0)
        pass_id = ctx.df[sch.PASS].to_numpy()
        remove = np.zeros(ctx.n, dtype=bool)

        frame = pd.DataFrame({"pass": pass_id, "step": step})
        cumulative = frame.groupby("pass")["step"].cumsum()
        totals = frame.groupby("pass")["step"].transform("sum")

        if start_m > 0:
            remove |= (cumulative <= start_m).to_numpy()
        if end_m > 0:
            remove |= ((totals - cumulative) <= end_m).to_numpy()

        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"{start_m:g} m no início e {end_m:g} m no fim de cada passada.",
        )


class ShortPassFilter(CleaningStep):
    """Remove passadas curtas demais para serem confiáveis."""

    key = "short_pass"
    label = "Passadas curtas"
    description = (
        "Passadas com poucos registros costumam ser manobra, retoque de "
        "cabeceira ou entrada equivocada — não representam a lavoura."
    )
    defaults = {"min_points": 8}

    def run(self, ctx: Context) -> StepResult:
        if sch.PASS not in ctx.df.columns:
            return self._result(0, ctx, "Passadas não identificadas.", skipped=True)
        minimum = int(self.params.get("min_points", 8))
        pass_id = pd.Series(ctx.df[sch.PASS].to_numpy())
        counts = pass_id.map(pass_id[ctx.alive].value_counts()).fillna(0).to_numpy()
        remove = counts < minimum
        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"Mínimo de {minimum} registros por passada.",
        )


class MoistureFilter(CleaningStep):
    """Aplica limites de umidade do grão."""

    key = "moisture"
    label = "Faixa de umidade"
    description = (
        "Umidade fora da faixa esperada indica sensor descalibrado ou "
        "leitura em vazio, e contamina a correção para massa seca."
    )
    defaults = {"min": 5.0, "max": 40.0}

    def run(self, ctx: Context) -> StepResult:
        moisture = ctx.column(sch.MOISTURE)
        if moisture is None:
            return self._result(0, ctx, "Umidade indisponível.", skipped=True)
        lo = float(self.params.get("min") or 0)
        hi = float(self.params.get("max") or 100)
        remove = np.isfinite(moisture) & ((moisture < lo) | (moisture > hi))
        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"Faixa aceita: {lo:g}–{hi:g}%.",
        )


class PositionFilter(CleaningStep):
    """Remove coordenadas repetidas ou com salto impossível."""

    key = "position"
    label = "Posição inconsistente"
    description = (
        "Coordenada repetida indica GPS travado; salto grande demais entre "
        "leituras consecutivas indica perda de correção."
    )
    defaults = {"max_jump_m": 25.0, "drop_duplicates": True}

    def run(self, ctx: Context) -> StepResult:
        x, y = ctx.column(sch.X), ctx.column(sch.Y)
        if x is None or y is None:
            return self._result(0, ctx, "Coordenadas projetadas indisponíveis.", skipped=True)

        remove = ~np.isfinite(x) | ~np.isfinite(y)
        if self.params.get("drop_duplicates", True):
            duplicated = pd.DataFrame({"x": np.round(x, 3), "y": np.round(y, 3)}).duplicated()
            remove |= duplicated.to_numpy()

        max_jump = float(self.params.get("max_jump_m") or 0)
        if max_jump > 0:
            dx = np.diff(x, prepend=x[0])
            dy = np.diff(y, prepend=y[0])
            step = np.hypot(dx, dy)
            step[0] = 0.0
            remove |= np.isfinite(step) & (step > max_jump)

        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"Salto máximo aceito: {max_jump:g} m.",
        )


class GlobalOutlierFilter(CleaningStep):
    """Remove valores extremos em relação ao conjunto todo."""

    key = "global_outlier"
    label = "Outliers globais"
    description = (
        "Corta a cauda da distribuição do talhão inteiro. Use o desvio padrão "
        "quando a distribuição for simétrica e o percentil quando for torta."
    )
    defaults = {"method": "std", "k": 3.0, "lower_pct": 1.0, "upper_pct": 99.0}

    def run(self, ctx: Context) -> StepResult:
        values = ctx.values
        if values is None:
            return self._result(0, ctx, "Coluna de valor ausente.", skipped=True)
        alive_values = values[ctx.alive]
        alive_values = alive_values[np.isfinite(alive_values)]
        if alive_values.size < 20:
            return self._result(0, ctx, "Registros insuficientes.", skipped=True)

        method = self.params.get("method", "std")
        if method == "percentile":
            lo = float(np.percentile(alive_values, float(self.params.get("lower_pct", 1.0))))
            hi = float(np.percentile(alive_values, float(self.params.get("upper_pct", 99.0))))
            detail = f"Percentis {self.params.get('lower_pct')}–{self.params.get('upper_pct')}."
        else:
            k = float(self.params.get("k", 3.0))
            mean = float(np.mean(alive_values))
            std = float(np.std(alive_values, ddof=1))
            lo, hi = mean - k * std, mean + k * std
            detail = f"Média {mean:.1f} ± {k:g}·{std:.1f}."

        remove = np.isfinite(values) & ((values < lo) | (values > hi))
        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=f"{detail} Intervalo aceito: {lo:.1f} a {hi:.1f}.",
        )


class LocalOutlierFilter(CleaningStep):
    """Remove valores discrepantes em relação à vizinhança imediata.

    É o filtro que separa variabilidade real de ruído: numa lavoura, pontos
    próximos tendem a se parecer. Um registro muito distante da mediana dos
    seus vizinhos, medido em desvios absolutos medianos, é erro de sensor —
    não um ponto de alta produtividade.
    """

    key = "local_outlier"
    label = "Outliers locais"
    description = (
        "Compara cada registro com a mediana dos vizinhos mais próximos e "
        "descarta os que se afastam além do limite, em desvios absolutos medianos."
    )
    defaults = {"k_neighbors": 12, "threshold": 3.5}

    def run(self, ctx: Context) -> StepResult:
        values = ctx.values
        if values is None:
            return self._result(0, ctx, "Coluna de valor ausente.", skipped=True)

        neighbors = ctx.neighbors(int(self.params.get("k_neighbors", 12)))
        if neighbors is None:
            return self._result(0, ctx, "Vizinhança insuficiente.", skipped=True)

        alive_idx, neighbor_idx = neighbors
        local = values[alive_idx]
        neighbor_values = values[alive_idx[neighbor_idx]]

        median = np.nanmedian(neighbor_values, axis=1)
        mad = np.nanmedian(np.abs(neighbor_values - median[:, None]), axis=1)
        # 1.4826 leva o MAD à escala de um desvio padrão sob normalidade.
        scale = mad * 1.4826
        # Onde a vizinhança é praticamente constante, o MAD colapsa e qualquer
        # diferença viraria outlier; um piso relativo evita esse falso positivo.
        floor = np.nanmedian(np.abs(local - np.nanmedian(local))) * 1.4826 * 0.1
        scale = np.where(scale > floor, scale, floor)

        with np.errstate(divide="ignore", invalid="ignore"):
            score = np.abs(local - median) / scale

        threshold = float(self.params.get("threshold", 3.5))
        flagged = np.isfinite(score) & (score > threshold)

        remove = np.zeros(ctx.n, dtype=bool)
        remove[alive_idx[flagged]] = True
        return self._result(
            ctx.apply(remove, self.label), ctx,
            detail=(
                f"{self.params.get('k_neighbors', 12)} vizinhos, limite de "
                f"{threshold:g} desvios."
            ),
        )


#: Registro das etapas disponíveis, na ordem recomendada de execução.
STEP_CLASSES: tuple[type[CleaningStep], ...] = (
    NullValueFilter,
    PositionFilter,
    ValueRangeFilter,
    MoistureFilter,
    SpeedRangeFilter,
    SpeedChangeFilter,
    SwathWidthFilter,
    ShortPassFilter,
    PassEndsFilter,
    OverlapFilter,
    BoundaryFilter,
    GlobalOutlierFilter,
    LocalOutlierFilter,
)

STEPS_BY_KEY = {cls.key: cls for cls in STEP_CLASSES}
