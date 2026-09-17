"""Execução e laudo da limpeza.

A limpeza acontece em duas fases. Primeiro as **correções**, que alteram
valores sem descartar nada (atraso de fluxo, conversão para massa seca).
Depois os **filtros**, que só marcam registros para remoção. A separação
importa: corrigir depois de filtrar aplicaria a correção sobre uma série
com buracos, e o deslocamento temporal do fluxo deixaria de fazer sentido.

Nada é sobrescrito. O resultado traz o conjunto limpo, o conjunto removido
com o motivo de cada descarte, e o laudo comparativo — que é o material
para julgar se a limpeza foi adequada ou exagerada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..core import schema as sch
from ..core.dataset import Dataset
from . import steps as steps_mod

#: Configurações iniciais por tipo de operação. São ponto de partida —
#: a interface expõe cada parâmetro para ajuste.
PRESETS: dict[str, dict[str, Any]] = {
    "harvest": {
        "label": "Colheita (mapa de rendimento)",
        "description": (
            "Sequência completa: corrige o atraso de fluxo e aplica todos os "
            "filtros, incluindo sobreposição e bordadura."
        ),
        "corrections": {"flow_delay_s": 12.0},
        "steps": {
            "null_value": {"enabled": True},
            "position": {"enabled": True, "max_jump_m": 25.0, "drop_duplicates": True},
            "value_range": {"enabled": False, "min": None, "max": None},
            "moisture": {"enabled": True, "min": 5.0, "max": 40.0},
            "speed_range": {"enabled": True, "min": 1.5, "max": 20.0},
            "speed_change": {"enabled": True, "max_change_pct": 25.0},
            "swath_partial": {"enabled": True, "min_fraction": 0.5},
            "short_pass": {"enabled": True, "min_points": 8},
            "pass_ends": {"enabled": True, "start_m": 6.0, "end_m": 6.0},
            "overlap": {"enabled": True, "max_overlap_pct": 40.0},
            "boundary": {"enabled": False, "buffer_m": 0.0},
            "global_outlier": {"enabled": True, "method": "std", "k": 3.0},
            "local_outlier": {"enabled": True, "k_neighbors": 12, "threshold": 3.5},
        },
    },
    "application": {
        "label": "Aplicação (as-applied)",
        "description": (
            "Sem atraso de fluxo e sem filtro de outlier local: numa aplicação "
            "em taxa variável, a mudança brusca de dose entre zonas é o sinal, "
            "não o ruído."
        ),
        "corrections": {"flow_delay_s": 0.0},
        "steps": {
            "null_value": {"enabled": True, "drop_zero": False},
            "position": {"enabled": True, "max_jump_m": 30.0, "drop_duplicates": True},
            "value_range": {"enabled": False, "min": None, "max": None},
            "moisture": {"enabled": False},
            "speed_range": {"enabled": True, "min": 1.0, "max": 30.0},
            "speed_change": {"enabled": False, "max_change_pct": 40.0},
            "swath_partial": {"enabled": True, "min_fraction": 0.3},
            "short_pass": {"enabled": True, "min_points": 5},
            "pass_ends": {"enabled": True, "start_m": 3.0, "end_m": 3.0},
            "overlap": {"enabled": True, "max_overlap_pct": 50.0},
            "boundary": {"enabled": False, "buffer_m": 0.0},
            "global_outlier": {"enabled": True, "method": "percentile",
                               "lower_pct": 0.5, "upper_pct": 99.5},
            "local_outlier": {"enabled": False, "k_neighbors": 12, "threshold": 4.0},
        },
    },
    "planting": {
        "label": "Plantio / semeadura",
        "description": "Foco em falhas de dosador e trechos de manobra.",
        "corrections": {"flow_delay_s": 0.0},
        "steps": {
            "null_value": {"enabled": True},
            "position": {"enabled": True, "max_jump_m": 25.0, "drop_duplicates": True},
            "value_range": {"enabled": False, "min": None, "max": None},
            "moisture": {"enabled": False},
            "speed_range": {"enabled": True, "min": 2.0, "max": 15.0},
            "speed_change": {"enabled": True, "max_change_pct": 30.0},
            "swath_partial": {"enabled": True, "min_fraction": 0.5},
            "short_pass": {"enabled": True, "min_points": 8},
            "pass_ends": {"enabled": True, "start_m": 4.0, "end_m": 4.0},
            "overlap": {"enabled": True, "max_overlap_pct": 40.0},
            "boundary": {"enabled": False, "buffer_m": 0.0},
            "global_outlier": {"enabled": True, "method": "std", "k": 3.5},
            "local_outlier": {"enabled": True, "k_neighbors": 12, "threshold": 4.0},
        },
    },
    "vigor": {
        "label": "Vigor / Augmenta",
        "description": (
            "Limpeza leve: o índice de vigor varia legitimamente entre plantas "
            "vizinhas, então só posição e extremos são tratados."
        ),
        "corrections": {"flow_delay_s": 0.0},
        "steps": {
            "null_value": {"enabled": True, "drop_zero": False},
            "position": {"enabled": True, "max_jump_m": 30.0, "drop_duplicates": True},
            "value_range": {"enabled": False, "min": None, "max": None},
            "moisture": {"enabled": False},
            "speed_range": {"enabled": True, "min": 0.5, "max": 30.0},
            "speed_change": {"enabled": False},
            "swath_partial": {"enabled": False},
            "short_pass": {"enabled": False},
            "pass_ends": {"enabled": False},
            "overlap": {"enabled": False},
            "boundary": {"enabled": False, "buffer_m": 0.0},
            "global_outlier": {"enabled": True, "method": "percentile",
                               "lower_pct": 0.5, "upper_pct": 99.5},
            "local_outlier": {"enabled": False},
        },
    },
    "minimal": {
        "label": "Mínima (apenas erros evidentes)",
        "description": "Só descarta o que é indefensável: nulo, posição inválida, duplicata.",
        "corrections": {"flow_delay_s": 0.0},
        "steps": {
            "null_value": {"enabled": True},
            "position": {"enabled": True, "max_jump_m": 50.0, "drop_duplicates": True},
            "value_range": {"enabled": False},
            "moisture": {"enabled": False},
            "speed_range": {"enabled": False},
            "speed_change": {"enabled": False},
            "swath_partial": {"enabled": False},
            "short_pass": {"enabled": False},
            "pass_ends": {"enabled": False},
            "overlap": {"enabled": False},
            "boundary": {"enabled": False},
            "global_outlier": {"enabled": False},
            "local_outlier": {"enabled": False},
        },
    },
}


def preset_for(operation: str) -> str:
    """Preset recomendado para um tipo de operação."""
    return operation if operation in PRESETS else "minimal"


@dataclass
class CleaningResult:
    """Saída completa de uma execução de limpeza."""

    clean: Dataset
    removed: Dataset
    report: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Correções
# --------------------------------------------------------------------------

def apply_flow_delay(ds: Dataset, delay_s: float, value_column: str = sch.VALUE) -> str | None:
    """Desloca a variável no tempo para compensar o trânsito dentro da máquina.

    Do corte até o sensor de fluxo passam-se alguns segundos. Sem essa
    correção, o valor medido é atribuído ao ponto onde a máquina está
    **agora**, e não ao ponto de onde o produto realmente veio — o que
    desloca todo o mapa alguns metros no sentido do deslocamento.
    """
    if not delay_s or delay_s <= 0 or value_column not in ds.df.columns:
        return None

    dt = ds._time_delta_seconds()
    if dt is None or not np.isfinite(dt).any():
        return "Atraso de fluxo não aplicado: intervalo entre registros indisponível."

    median_dt = float(np.nanmedian(dt))
    if not np.isfinite(median_dt) or median_dt <= 0:
        return "Atraso de fluxo não aplicado: intervalo entre registros inconsistente."

    shift = int(round(delay_s / median_dt))
    if shift <= 0:
        return None

    ds.df[value_column] = ds.df[value_column].shift(-shift)
    return (
        f"Atraso de fluxo de {delay_s:g} s aplicado "
        f"({shift} registros a {median_dt:.2f} s cada)."
    )


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------

def run(
    dataset: Dataset,
    config: dict[str, Any] | None = None,
    value_column: str = sch.VALUE,
) -> CleaningResult:
    """Executa a limpeza e devolve dados limpos, removidos e laudo.

    Parameters
    ----------
    dataset:
        Dataset já importado e com campos derivados calculados.
    config:
        Dicionário no formato dos presets. Ausente, usa o preset do tipo de
        operação detectado.
    value_column:
        Coluna a tratar como variável principal.
    """
    if config is None:
        config = PRESETS[preset_for(dataset.meta.operation)]

    working = dataset.copy()
    working.sort_by_time()
    working.ensure_derived()

    before_stats = working.stats(value_column)
    before_values = pd.to_numeric(
        working.df.get(value_column), errors="coerce"
    ) if value_column in working.df.columns else pd.Series(dtype="float64")

    corrections: list[str] = []
    delay = float((config.get("corrections") or {}).get("flow_delay_s") or 0.0)
    message = apply_flow_delay(working, delay, value_column)
    if message:
        corrections.append(message)

    ctx = steps_mod.Context(working.df, value_column)
    step_config = config.get("steps") or {}
    results: list[steps_mod.StepResult] = []

    for step_class in steps_mod.STEP_CLASSES:
        settings = dict(step_config.get(step_class.key) or {})
        enabled = settings.pop("enabled", False)
        if not enabled:
            continue
        step = step_class(**{k: v for k, v in settings.items() if v is not None or k in ("min", "max")})
        results.append(step.run(ctx))

    clean = working.subset(ctx.alive)
    removed = working.subset(~ctx.alive)
    if len(removed):
        removed.df["removal_reason"] = ctx.reason[~ctx.alive]

    clean.meta.notes = list(clean.meta.notes) + corrections
    clean.meta.name = f"{dataset.meta.name} (limpo)"
    removed.meta.name = f"{dataset.meta.name} (removidos)"

    report = build_report(
        dataset, clean, removed, results, corrections,
        before_stats, before_values, value_column,
    )
    return CleaningResult(clean=clean, removed=removed, report=report)


# --------------------------------------------------------------------------
# Laudo
# --------------------------------------------------------------------------

def _histogram(series: pd.Series, bins: int = 30) -> dict[str, list[float]]:
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy()
    if values.size < 2:
        return {"edges": [], "counts": []}
    counts, edges = np.histogram(values, bins=bins)
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


def _assess(removed_pct: float, before: dict, after: dict) -> list[dict[str, str]]:
    """Traduz os números do laudo em leituras acionáveis.

    Não basta dizer quantos pontos saíram; o que interessa é se a limpeza
    ficou dentro do razoável e o que mudou na distribuição.
    """
    findings: list[dict[str, str]] = []

    if removed_pct > 45:
        findings.append({
            "nivel": "alerta",
            "texto": (
                f"{removed_pct:.1f}% dos registros foram descartados. Acima de ~40% "
                "o mapa passa a refletir mais os filtros do que a lavoura — "
                "reveja os parâmetros mais agressivos antes de usar o resultado."
            ),
        })
    elif removed_pct > 25:
        findings.append({
            "nivel": "atencao",
            "texto": (
                f"{removed_pct:.1f}% dos registros descartados. É defensável em dado "
                "de colheita com muita manobra, mas confira quais filtros dominaram."
            ),
        })
    elif removed_pct < 2:
        findings.append({
            "nivel": "atencao",
            "texto": (
                f"Apenas {removed_pct:.1f}% foi descartado. Dado bruto de monitor "
                "raramente é tão limpo — verifique se os filtros estavam ativos."
            ),
        })
    else:
        findings.append({
            "nivel": "ok",
            "texto": f"{removed_pct:.1f}% dos registros descartados — dentro do usual.",
        })

    cv_before = before.get("cv")
    cv_after = after.get("cv")
    if cv_before and cv_after:
        delta = cv_after - cv_before
        if delta < -3:
            findings.append({
                "nivel": "ok",
                "texto": (
                    f"Coeficiente de variação caiu de {cv_before:.1f}% para "
                    f"{cv_after:.1f}% — o ruído saiu e a variação restante tende a "
                    "ser a do talhão."
                ),
            })
        elif delta > 2:
            findings.append({
                "nivel": "alerta",
                "texto": (
                    f"Coeficiente de variação subiu de {cv_before:.1f}% para "
                    f"{cv_after:.1f}%. Uma limpeza que aumenta a dispersão costuma "
                    "indicar filtro cortando de um lado só da distribuição."
                ),
            })

    mean_before, mean_after = before.get("mean"), after.get("mean")
    if mean_before and mean_after:
        shift = (mean_after - mean_before) / mean_before * 100.0
        if abs(shift) > 8:
            findings.append({
                "nivel": "alerta",
                "texto": (
                    f"A média mudou {shift:+.1f}% com a limpeza. Um deslocamento "
                    "desse tamanho muda a conclusão agronômica: confirme que os "
                    "pontos removidos eram mesmo erro, e não área de baixa produção."
                ),
            })
        else:
            findings.append({
                "nivel": "ok",
                "texto": f"Média deslocou {shift:+.1f}% — a limpeza preservou o patamar do talhão.",
            })

    return findings


def build_report(
    original: Dataset,
    clean: Dataset,
    removed: Dataset,
    results: list[steps_mod.StepResult],
    corrections: list[str],
    before_stats: dict,
    before_values: pd.Series,
    value_column: str,
) -> dict[str, Any]:
    """Monta o laudo comparativo da limpeza."""
    total = len(original)
    kept = len(clean)
    removed_count = len(removed)
    removed_pct = (removed_count / total * 100.0) if total else 0.0
    after_stats = clean.stats(value_column)

    by_reason: list[dict[str, Any]] = []
    if removed_count and "removal_reason" in removed.df.columns:
        counts = removed.df["removal_reason"].value_counts()
        by_reason = [
            {
                "motivo": str(reason),
                "registros": int(count),
                "pct_do_total": round(count / total * 100.0, 2) if total else 0.0,
            }
            for reason, count in counts.items()
        ]

    return {
        "totais": {
            "entrada": total,
            "mantidos": kept,
            "removidos": removed_count,
            "pct_removido": round(removed_pct, 2),
            "area_ha_antes": round(original.area_ha(), 2),
            "area_ha_depois": round(clean.area_ha(), 2),
        },
        "coluna_analisada": value_column,
        "correcoes": corrections,
        "etapas": [r.to_dict() for r in results],
        "por_motivo": by_reason,
        "estatisticas": {"antes": before_stats, "depois": after_stats},
        "histograma": {
            "antes": _histogram(before_values),
            "depois": _histogram(clean.df.get(value_column, pd.Series(dtype="float64"))),
        },
        "leitura": _assess(removed_pct, before_stats, after_stats),
    }
