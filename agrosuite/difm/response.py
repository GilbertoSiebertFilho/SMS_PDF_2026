"""Funções de resposta à dose e otimização econômica.

O objetivo de um ensaio em faixas não é descobrir qual dose deu o maior
rendimento — é descobrir qual dose dá o maior **lucro**. As duas respostas
raramente coincidem: a curva de rendimento continua subindo quando o
incremento já não paga o insumo.

A dose econômica ótima é o ponto em que a derivada da curva de rendimento
iguala a razão de preços::

    dY/dR = custo_do_insumo / preço_do_produto

Abaixo dela, cada quilo a mais de insumo devolve mais do que custa; acima,
devolve menos. Todos os modelos aqui expõem essa derivada analiticamente,
para que a dose ótima seja resolvida sem busca numérica quando possível.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np


@dataclass
class ResponseFit:
    """Ajuste de um modelo de resposta a um conjunto (dose, rendimento)."""

    model: str
    label: str
    params: dict[str, float]
    r2: float
    rmse: float
    n: int
    predict: Callable[[np.ndarray], np.ndarray] = field(repr=False, default=None)
    derivative: Callable[[np.ndarray], np.ndarray] = field(repr=False, default=None)
    plateau_rate: float | None = None
    converged: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "label": self.label,
            "params": {k: round(float(v), 6) for k, v in self.params.items()},
            "r2": round(float(self.r2), 4),
            "rmse": round(float(self.rmse), 2),
            "n": int(self.n),
            "plateau_rate": round(float(self.plateau_rate), 2) if self.plateau_rate else None,
            "converged": self.converged,
            "message": self.message,
        }


def _goodness(y: np.ndarray, y_hat: np.ndarray) -> tuple[float, float]:
    residual = y - y_hat
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    rmse = float(np.sqrt(ss_res / len(y))) if len(y) else float("nan")
    return r2, rmse


# --------------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------------

def fit_quadratic(rates: np.ndarray, yields: np.ndarray) -> ResponseFit:
    """Resposta quadrática: ``Y = a + b·R + c·R²``.

    É o modelo de referência da literatura de adubação. Exige apenas três
    doses distintas e tem dose ótima em forma fechada, mas impõe simetria à
    curva — o que superestima a queda em doses altas.
    """
    coef = np.polyfit(rates, yields, 2)
    c, b, a = float(coef[0]), float(coef[1]), float(coef[2])
    predict = lambda r: a + b * np.asarray(r, dtype="float64") + c * np.asarray(r, dtype="float64") ** 2
    derivative = lambda r: b + 2 * c * np.asarray(r, dtype="float64")
    r2, rmse = _goodness(yields, predict(rates))
    plateau = -b / (2 * c) if c < 0 else None
    return ResponseFit(
        model="quadratic",
        label="Quadrática",
        params={"a": a, "b": b, "c": c},
        r2=r2, rmse=rmse, n=len(rates),
        predict=predict, derivative=derivative,
        plateau_rate=plateau,
        message="" if c < 0 else "Curvatura positiva: sem máximo agronômico na faixa testada.",
    )


def fit_quadratic_plateau(rates: np.ndarray, yields: np.ndarray) -> ResponseFit:
    """Quadrática com platô: sobe até a dose crítica e estabiliza.

    Mais fiel ao comportamento biológico que a quadrática pura, porque não
    obriga o rendimento a cair depois do ponto máximo.
    """
    from scipy.optimize import curve_fit

    def model(r, a, b, c):
        r = np.asarray(r, dtype="float64")
        critical = -b / (2 * c) if c < 0 else np.inf
        r_eff = np.minimum(r, critical)
        return a + b * r_eff + c * r_eff**2

    start = np.polyfit(rates, yields, 2)
    p0 = [float(start[2]), float(start[1]), float(min(start[0], -1e-6))]
    try:
        popt, _ = curve_fit(model, rates, yields, p0=p0, maxfev=20_000)
        converged, message = True, ""
    except Exception as exc:
        popt, converged, message = p0, False, f"Ajuste não convergiu ({exc}); usados valores iniciais."

    a, b, c = (float(v) for v in popt)
    critical = -b / (2 * c) if c < 0 else float("inf")

    def predict(r):
        r = np.asarray(r, dtype="float64")
        return model(r, a, b, c)

    def derivative(r):
        r = np.asarray(r, dtype="float64")
        return np.where(r < critical, b + 2 * c * r, 0.0)

    r2, rmse = _goodness(yields, predict(rates))
    return ResponseFit(
        model="quadratic_plateau",
        label="Quadrática com platô",
        params={"a": a, "b": b, "c": c},
        r2=r2, rmse=rmse, n=len(rates),
        predict=predict, derivative=derivative,
        plateau_rate=critical if np.isfinite(critical) else None,
        converged=converged, message=message,
    )


def fit_linear_plateau(rates: np.ndarray, yields: np.ndarray) -> ResponseFit:
    """Linear com platô: resposta constante até a dose crítica, depois nada.

    Útil quando o insumo é claramente limitante até um ponto e irrelevante
    depois — comum em correção de deficiência.
    """
    from scipy.optimize import curve_fit

    def model(r, a, b, critical):
        r = np.asarray(r, dtype="float64")
        return a + b * np.minimum(r, critical)

    span = float(np.max(rates) - np.min(rates)) or 1.0
    p0 = [float(np.min(yields)), span and float((np.max(yields) - np.min(yields)) / span),
          float(np.median(rates))]
    try:
        popt, _ = curve_fit(model, rates, yields, p0=p0, maxfev=20_000)
        converged, message = True, ""
    except Exception as exc:
        popt, converged, message = p0, False, f"Ajuste não convergiu ({exc}); usados valores iniciais."

    a, b, critical = (float(v) for v in popt)
    predict = lambda r: model(r, a, b, critical)
    derivative = lambda r: np.where(np.asarray(r, dtype="float64") < critical, b, 0.0)
    r2, rmse = _goodness(yields, predict(rates))
    return ResponseFit(
        model="linear_plateau",
        label="Linear com platô",
        params={"a": a, "b": b, "critical": critical},
        r2=r2, rmse=rmse, n=len(rates),
        predict=predict, derivative=derivative,
        plateau_rate=critical,
        converged=converged, message=message,
    )


def fit_mitscherlich(rates: np.ndarray, yields: np.ndarray) -> ResponseFit:
    """Mitscherlich: ``Y = A·(1 − exp(−c·(R + b)))``.

    Assíntota suave, sem queda em doses altas. É o modelo mais conservador
    para extrapolar acima da maior dose testada.
    """
    from scipy.optimize import curve_fit

    def model(r, A, b, c):
        return A * (1.0 - np.exp(-c * (np.asarray(r, dtype="float64") + b)))

    p0 = [float(np.max(yields) * 1.05), 10.0, 0.01]
    try:
        popt, _ = curve_fit(
            model, rates, yields, p0=p0, maxfev=40_000,
            bounds=([0, -np.inf, 1e-6], [np.inf, np.inf, 1.0]),
        )
        converged, message = True, ""
    except Exception as exc:
        popt, converged, message = p0, False, f"Ajuste não convergiu ({exc}); usados valores iniciais."

    A, b, c = (float(v) for v in popt)
    predict = lambda r: model(r, A, b, c)
    derivative = lambda r: A * c * np.exp(-c * (np.asarray(r, dtype="float64") + b))
    r2, rmse = _goodness(yields, predict(rates))
    return ResponseFit(
        model="mitscherlich",
        label="Mitscherlich",
        params={"A": A, "b": b, "c": c},
        r2=r2, rmse=rmse, n=len(rates),
        predict=predict, derivative=derivative,
        converged=converged, message=message,
    )


MODELS: dict[str, Callable[[np.ndarray, np.ndarray], ResponseFit]] = {
    "quadratic": fit_quadratic,
    "quadratic_plateau": fit_quadratic_plateau,
    "linear_plateau": fit_linear_plateau,
    "mitscherlich": fit_mitscherlich,
}

MODEL_LABELS = {
    "quadratic": "Quadrática",
    "quadratic_plateau": "Quadrática com platô",
    "linear_plateau": "Linear com platô",
    "mitscherlich": "Mitscherlich",
}


def fit_best(rates: np.ndarray, yields: np.ndarray, candidates=None) -> tuple[ResponseFit, list[ResponseFit]]:
    """Ajusta os modelos candidatos e devolve o melhor por R², com os demais.

    Modelos com menos doses distintas que parâmetros são descartados: um
    ajuste que passa exatamente pelos pontos não informa nada.
    """
    rates = np.asarray(rates, dtype="float64")
    yields = np.asarray(yields, dtype="float64")
    mask = np.isfinite(rates) & np.isfinite(yields)
    rates, yields = rates[mask], yields[mask]

    distinct = len(np.unique(rates))
    if distinct < 3:
        raise ValueError(
            f"São necessárias ao menos 3 doses distintas para ajustar uma curva "
            f"de resposta; o conjunto tem {distinct}."
        )

    names = candidates or list(MODELS)
    if distinct < 4:
        names = [n for n in names if n == "quadratic"] or ["quadratic"]

    fits: list[ResponseFit] = []
    for name in names:
        try:
            fits.append(MODELS[name](rates, yields))
        except Exception:
            continue
    if not fits:
        raise ValueError("Nenhum modelo de resposta pôde ser ajustado a estes dados.")

    fits.sort(key=lambda f: (-f.r2, f.rmse))
    return fits[0], fits


# --------------------------------------------------------------------------
# Economia
# --------------------------------------------------------------------------

def optimum_rate(
    fit: ResponseFit,
    crop_price: float,
    input_cost: float,
    rate_min: float = 0.0,
    rate_max: float = 400.0,
) -> dict[str, float]:
    """Dose econômica ótima e o que ela rende.

    Parameters
    ----------
    crop_price:
        Preço do produto colhido, por unidade da variável de rendimento
        (ex.: R$/kg se o rendimento está em kg/ha).
    input_cost:
        Custo do insumo, por unidade de dose (ex.: R$/kg de N).
    rate_min, rate_max:
        Faixa em que a busca é permitida. Fora do intervalo testado no
        ensaio a curva é extrapolação, e o resultado vem marcado como tal.
    """
    if crop_price <= 0:
        raise ValueError("O preço do produto precisa ser positivo.")

    price_ratio = input_cost / crop_price  # unidades de rendimento por unidade de dose
    grid = np.linspace(rate_min, rate_max, 2001)
    profit = fit.predict(grid) * crop_price - grid * input_cost
    best_index = int(np.argmax(profit))
    best_rate = float(grid[best_index])

    # Refina pela condição marginal, quando a derivada cruza a razão de preços.
    derivative = fit.derivative(grid)
    crossing = np.flatnonzero(np.diff(np.sign(derivative - price_ratio)) < 0)
    if crossing.size:
        candidate = float(grid[crossing[0] + 1])
        if rate_min <= candidate <= rate_max:
            candidate_profit = float(fit.predict(np.array([candidate]))[0] * crop_price
                                     - candidate * input_cost)
            if candidate_profit >= profit[best_index] - 1e-6:
                best_rate = candidate

    yield_at_best = float(fit.predict(np.array([best_rate]))[0])
    agronomic = fit.plateau_rate
    return {
        "dose_otima": round(best_rate, 2),
        "rendimento_na_otima": round(yield_at_best, 1),
        "lucro_na_otima": round(yield_at_best * crop_price - best_rate * input_cost, 2),
        "razao_de_precos": round(price_ratio, 4),
        "dose_maximo_agronomico": round(float(agronomic), 2) if agronomic and np.isfinite(agronomic) else None,
        "no_limite_da_faixa": bool(best_rate >= rate_max - 1e-6 or best_rate <= rate_min + 1e-6),
    }


def profit_curve(
    fit: ResponseFit,
    crop_price: float,
    input_cost: float,
    rate_min: float = 0.0,
    rate_max: float = 400.0,
    points: int = 60,
) -> list[dict[str, float]]:
    """Amostra a curva de rendimento e a de lucro para exibição."""
    grid = np.linspace(rate_min, rate_max, points)
    predicted = fit.predict(grid)
    profit = predicted * crop_price - grid * input_cost
    return [
        {
            "dose": round(float(r), 2),
            "rendimento": round(float(y), 1),
            "lucro": round(float(p), 2),
        }
        for r, y, p in zip(grid, predicted, profit)
    ]
