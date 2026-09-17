"""Gerador de dados sintéticos de monitor.

Serve a dois propósitos: permitir experimentar o app sem ter um arquivo em
mãos e, sobretudo, dar um conjunto com **defeitos conhecidos** contra o qual
os filtros de limpeza podem ser conferidos — se o filtro de sobreposição não
encontra a sobreposição que foi plantada de propósito, ele está errado.

O talhão simulado tem:

* trajetória em vaivém com manobras de cabeceira reais;
* padrão espacial de produtividade suave (a variabilidade legítima);
* atraso de fluxo do sensor;
* uma passada de repasse sobre área já colhida;
* picos e zeros de sensor espalhados;
* trechos de velocidade fora da faixa operacional.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .core.dataset import Dataset, DatasetMeta


def synthetic_harvest(
    seed: int = 7,
    n_passes: int = 18,
    pass_length_m: float = 420.0,
    swath_m: float = 9.0,
    origin_lon: float = -51.2000,
    origin_lat: float = -23.5000,
    with_defects: bool = True,
) -> Dataset:
    """Gera um mapa de colheita sintético com defeitos plantados.

    Returns
    -------
    Dataset
        Com a coluna extra ``truth_kg_ha``, o valor limpo antes dos defeitos,
        para medir quanto da limpeza recuperou o sinal original.
    """
    rng = np.random.default_rng(seed)

    # ~1 grau de latitude = 111,32 km; a longitude encolhe com o cosseno.
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * np.cos(np.radians(origin_lat))

    step_m = 1.4  # avanço típico entre registros a ~5 km/h com log de 1 s
    rows: list[dict] = []
    time_s = 0.0

    for p in range(n_passes):
        n_points = int(pass_length_m / step_m)
        offset_x = p * swath_m
        going_north = p % 2 == 0
        along = np.arange(n_points) * step_m
        if not going_north:
            along = along[::-1]

        speed = 5.6 + rng.normal(0, 0.12, n_points)
        # Entrada e saída de passada: a máquina acelera e desacelera.
        ramp = min(18, n_points // 4)
        speed[:ramp] *= np.linspace(0.35, 1.0, ramp)
        speed[-ramp:] *= np.linspace(1.0, 0.35, ramp)

        for i in range(n_points):
            x = offset_x + rng.normal(0, 0.05)
            y = along[i]
            rows.append({
                "x_local": x,
                "y_local": y,
                "speed": speed[i],
                "pass": p,
                "t": time_s,
                "swath": swath_m,
                "heading": 0.0 if going_north else 180.0,
            })
            time_s += step_m / max(speed[i] / 3.6, 0.5)

        time_s += 14.0  # manobra de cabeceira

    df = pd.DataFrame(rows)

    # --- produtividade verdadeira: dois harmônicos + gradiente + ruído fino
    xs = df["x_local"].to_numpy()
    ys = df["y_local"].to_numpy()
    field_width = n_passes * swath_m
    truth = (
        10_500.0
        + 1_800.0 * np.sin(2 * np.pi * xs / (field_width / 1.5))
        + 1_100.0 * np.cos(2 * np.pi * ys / (pass_length_m / 2.2))
        - 900.0 * (ys / pass_length_m)
        + rng.normal(0, 260.0, len(df))
    )
    truth = np.clip(truth, 3_000.0, 18_000.0)
    df["truth_kg_ha"] = truth

    observed = truth.copy()

    if with_defects:
        # Atraso de fluxo: o sensor entrega a leitura ~12 s depois do corte.
        median_dt = float(np.median(np.diff(df["t"].to_numpy())))
        lag = max(1, int(round(12.0 / max(median_dt, 0.1))))
        observed = np.roll(observed, lag)
        observed[:lag] = observed[lag]

        # A massa em trânsito faz o valor acompanhar a velocidade com atraso.
        speed = df["speed"].to_numpy()
        speed_effect = np.clip(speed / np.median(speed), 0.25, 1.8)
        observed *= 0.35 + 0.65 * np.roll(speed_effect, lag)

        # Zeros de sensor e picos isolados.
        n = len(df)
        observed[rng.choice(n, size=int(n * 0.012), replace=False)] = 0.0
        spikes = rng.choice(n, size=int(n * 0.008), replace=False)
        observed[spikes] *= rng.uniform(2.6, 4.5, size=spikes.size)

        # Repasse: uma passada extra sobre a faixa 5, já colhida, com valor baixo.
        overlap_mask = df["pass"] == 5
        extra = df.loc[overlap_mask].copy()
        extra["x_local"] += swath_m * 0.45
        extra["t"] += float(df["t"].max()) + 60.0
        extra["pass"] = n_passes + 1
        extra_observed = truth[overlap_mask.to_numpy()] * rng.uniform(0.12, 0.35, int(overlap_mask.sum()))
        df = pd.concat([df, extra], ignore_index=True)
        observed = np.concatenate([observed, extra_observed])

    df["value"] = observed
    df["moisture_pct"] = np.clip(rng.normal(14.5, 1.1, len(df)), 6.0, 32.0)

    out = pd.DataFrame({
        "lon": origin_lon + df["x_local"].to_numpy() / m_per_deg_lon,
        "lat": origin_lat + df["y_local"].to_numpy() / m_per_deg_lat,
        "timestamp": pd.Timestamp("2025-03-12 08:00:00") + pd.to_timedelta(df["t"], unit="s"),
        "value": df["value"].to_numpy(),
        "speed_kmh": df["speed"].to_numpy(),
        "swath_m": df["swath"].to_numpy(),
        "moisture_pct": df["moisture_pct"].to_numpy(),
        "pass_id": df["pass"].to_numpy(),
        "truth_kg_ha": df["truth_kg_ha"].to_numpy(),
    }).sort_values("timestamp").reset_index(drop=True)

    meta = DatasetMeta(
        name="Talhão demonstração",
        source_path="<sintético>",
        source_format="demo",
        brand="generic",
        brand_label="Dados sintéticos",
        operation="harvest",
        crop="milho",
        field_name="Talhão demonstração",
        value_label="Rendimento",
        value_unit="kg/ha",
        notes=["Conjunto sintético com defeitos plantados para conferir a limpeza."],
    )
    ds = Dataset(out, meta)
    ds.ensure_derived(default_swath_m=swath_m)
    return ds


def synthetic_trial(seed: int = 3, rates: tuple[float, ...] = (0, 60, 120, 180, 240)) -> Dataset:
    """Gera um ensaio DIFM em faixas, com resposta conhecida à dose.

    A resposta é quadrática com platô e varia entre duas zonas de fertilidade,
    que é exatamente o que uma análise DIFM deve conseguir separar.
    """
    rng = np.random.default_rng(seed)
    # Cada faixa do ensaio tem 3 passadas de largura — abaixo disso, a faixa
    # fica estreita demais para sobrar área útil depois de descartar as bordas.
    passes_per_strip = 3
    n_blocks = 4
    n_strips = len(rates) * n_blocks
    ds = synthetic_harvest(
        seed=seed, n_passes=n_strips * passes_per_strip, with_defects=False
    )
    df = ds.df

    x = df["x"].to_numpy()
    x_rel = (x - x.min()) / max(float(np.ptp(x)), 1.0)
    strip = np.clip(np.floor(x_rel * n_strips).astype(int), 0, n_strips - 1)

    # Sorteia a ordem das doses dentro de cada bloco, como num experimento
    # em blocos casualizados.
    assignment = np.zeros(n_strips, dtype=int)
    for block in range(n_blocks):
        assignment[block * len(rates):(block + 1) * len(rates)] = rng.permutation(len(rates))
    rate = np.array(rates)[assignment[strip]]

    y = df["y"].to_numpy()
    y_rel = (y - y.min()) / max(float(np.ptp(y)), 1.0)
    zone = (y_rel > 0.5).astype(int)  # zona 1 = mais fértil

    # Resposta quadrática: platô mais alto e dose ótima menor na zona fértil.
    base = np.where(zone == 1, 9_800.0, 7_600.0)
    gain = np.where(zone == 1, 26.0, 34.0)
    curve = np.where(zone == 1, -0.062, -0.070)
    yield_kg = base + gain * rate + curve * rate**2 + rng.normal(0, 380.0, len(df))

    df["value"] = np.clip(yield_kg, 1_000.0, None)
    df["applied_rate"] = rate.astype(float)
    df["trial_id"] = strip
    df["zone"] = zone
    df = df.drop(columns=["truth_kg_ha"], errors="ignore")
    ds.df = df

    ds.meta.name = "Ensaio DIFM demonstração"
    ds.meta.notes = ["Ensaio sintético em faixas com resposta quadrática conhecida."]
    ds.meta.extra["rates"] = list(rates)
    return ds
