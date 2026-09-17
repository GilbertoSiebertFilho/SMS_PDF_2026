"""Leitura de dados no padrão Augmenta.

O Augmenta é um sistema de visão embarcado que classifica a cultura em tempo
real e comanda a aplicação em taxa variável. As exportações chegam em três
arranjos, todos cobertos aqui:

1. **GeoJSON de sessão** — ``FeatureCollection`` cujas *features* são os
   pontos ou células da passagem, com propriedades de vigor/biomassa e a
   dose efetivamente aplicada.
2. **JSON envelopado** — o GeoJSON embrulhado num objeto com metadados da
   sessão (``session``/``field``/``machine``) sob alguma chave.
3. **CSV/SHP** — tratados pelos leitores genéricos, que reconhecem as
   colunas de índice do Augmenta pelo dicionário de aliases.

O que distingue o Augmenta dos demais é a semântica: além da dose aplicada
ele traz um índice de vigor por ponto, e é esse par (vigor, dose) que
alimenta as análises de eficiência de aplicação do app.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..core import schema as sch
from ..core.dataset import Dataset, DatasetMeta

#: Chaves de propriedade que caracterizam uma exportação do Augmenta.
AUGMENTA_KEYS = {
    "vigor", "biomass", "biomass_index", "canopy", "canopy_cover",
    "crop_coverage", "weed_coverage", "applied_rate", "vra_rate",
    "session_id", "augmenta_id", "ndvi", "ndre", "nozzle", "spray_rate",
    "green_index", "plant_count", "coverage_percent",
}

#: Ordem de preferência para eleger a variável principal do dataset.
VALUE_PRIORITY = (
    "applied_rate", "vra_rate", "spray_rate", "rate",
    "vigor", "biomass_index", "biomass", "ndvi", "ndre",
    "canopy_cover", "crop_coverage", "green_index",
)

#: Índices de vigor reconhecidos — separados da dose para a análise cruzada.
VIGOR_KEYS = (
    "vigor", "biomass_index", "biomass", "ndvi", "ndre",
    "canopy_cover", "canopy", "crop_coverage", "green_index",
)


def _feature_collection(payload: Any) -> dict | None:
    """Localiza a ``FeatureCollection`` dentro de um payload possivelmente envelopado."""
    if isinstance(payload, dict):
        if payload.get("type") == "FeatureCollection" and isinstance(payload.get("features"), list):
            return payload
        for value in payload.values():
            found = _feature_collection(value)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _feature_collection(value)
            if found is not None:
                return found
    return None


def is_augmenta_payload(payload: Any) -> bool:
    """Diz se um JSON já carregado tem a assinatura do Augmenta."""
    fc = _feature_collection(payload)
    if fc is None:
        return False
    text = json.dumps(payload, default=str)[:4000].lower()
    if "augmenta" in text:
        return True
    for feature in fc.get("features", [])[:20]:
        props = (feature or {}).get("properties") or {}
        keys = {sch.normalize_name(k) for k in props}
        if len(keys & AUGMENTA_KEYS) >= 2:
            return True
    return False


def _session_metadata(payload: Any) -> dict[str, Any]:
    """Extrai metadados de sessão do envelope, quando presentes."""
    meta: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return meta
    interesting = (
        "session", "session_id", "field", "field_name", "farm", "machine",
        "machine_id", "operator", "crop", "product", "date", "start_time",
        "end_time", "application", "device", "firmware", "unit",
    )
    for key, value in payload.items():
        norm = sch.normalize_name(key)
        if norm in interesting and not isinstance(value, (list, dict)):
            meta[norm] = value
        elif norm in ("session", "field", "machine", "application") and isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if not isinstance(sub_value, (list, dict)):
                    meta[f"{norm}_{sch.normalize_name(sub_key)}"] = sub_value
    return meta


def read_augmenta(path: Path, payload: Any | None = None) -> Dataset:
    """Lê uma exportação do Augmenta e devolve o dataset normalizado."""
    path = Path(path)
    if payload is None:
        payload = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))

    fc = _feature_collection(payload)
    if fc is None:
        raise ValueError("Arquivo Augmenta sem FeatureCollection reconhecível.")

    import geopandas as gpd
    from shapely.geometry import shape

    notes: list[str] = []
    rows: list[dict[str, Any]] = []
    geometries: list[Any] = []
    polygonal = False

    for feature in fc.get("features", []):
        if not feature:
            continue
        geom_json = feature.get("geometry")
        if not geom_json:
            continue
        try:
            geom = shape(geom_json)
        except Exception:
            continue
        if geom.is_empty:
            continue
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            polygonal = True
        props = dict(feature.get("properties") or {})
        point = geom.representative_point()
        props[sch.LON] = point.x
        props[sch.LAT] = point.y
        rows.append(props)
        geometries.append(geom)

    if not rows:
        raise ValueError("Nenhuma feição válida encontrada na exportação Augmenta.")

    df = pd.DataFrame(rows)
    original_columns = [c for c in df.columns if c not in (sch.LON, sch.LAT)]
    df.columns = [str(c).strip() for c in df.columns]

    # Elege a variável principal antes do mapeamento genérico, para que a
    # dose aplicada tenha prioridade sobre qualquer outro numérico.
    norm_lookup = {sch.normalize_name(c): c for c in df.columns}
    value_source = next((norm_lookup[k] for k in VALUE_PRIORITY if k in norm_lookup), None)

    mapping = sch.map_columns([c for c in df.columns if c not in (sch.LON, sch.LAT)])
    mapping.pop(value_source, None)
    if mapping:
        df = df.rename(columns=mapping)

    if value_source:
        df[sch.VALUE] = pd.to_numeric(df[value_source], errors="coerce")
        notes.append(f"Variável principal: '{value_source}'.")

    vigor_source = next((norm_lookup[k] for k in VIGOR_KEYS if k in norm_lookup), None)
    if vigor_source:
        df["vigor_index"] = pd.to_numeric(df[vigor_source], errors="coerce")
        notes.append(f"Índice de vigor lido de '{vigor_source}'.")

    session = _session_metadata(payload)
    if polygonal:
        notes.append("Células poligonais do Augmenta: centroides usados na análise.")

    operation = "application" if value_source in (
        norm_lookup.get("applied_rate"), norm_lookup.get("vra_rate"), norm_lookup.get("spray_rate")
    ) else "vigor"

    meta = DatasetMeta(
        name=str(session.get("field_name") or session.get("session_id") or path.stem),
        source_path=str(path),
        source_format="augmenta",
        brand="augmenta",
        brand_label="Augmenta",
        operation=operation,
        crop=session.get("crop"),
        field_name=session.get("field_name") or session.get("field"),
        value_label="Dose aplicada" if operation == "application" else "Índice de vigor",
        geometry_type="polygon" if polygonal else "point",
        notes=notes,
        extra={
            "brand_confidence": 1.0,
            "augmenta_session": session,
            "original_columns": original_columns,
            "vigor_column": "vigor_index" if vigor_source else None,
        },
    )
    return Dataset(df, meta, geometry=geometries if polygonal else None)


def vigor_rate_summary(ds: Dataset, bins: int = 6) -> dict[str, Any]:
    """Cruza índice de vigor com dose aplicada.

    É a leitura que dá sentido a um dado do Augmenta: se o sistema está
    modulando corretamente, a dose deve variar de forma monótona ao longo
    das classes de vigor. Uma relação plana indica aplicação praticamente
    uniforme — ou seja, o VRA não atuou.
    """
    import numpy as np

    if "vigor_index" not in ds.df.columns or sch.VALUE not in ds.df.columns:
        return {"available": False, "reason": "Dataset sem par vigor/dose."}

    frame = ds.df[["vigor_index", sch.VALUE]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(frame) < 10:
        return {"available": False, "reason": "Pontos insuficientes para cruzar vigor e dose."}

    try:
        classes = pd.qcut(frame["vigor_index"], q=bins, duplicates="drop")
    except ValueError:
        return {"available": False, "reason": "Índice de vigor sem variação suficiente."}

    grouped = frame.groupby(classes, observed=True)[sch.VALUE].agg(["count", "mean", "std"])
    rows = [
        {
            "classe": f"{interval.left:.3g} – {interval.right:.3g}",
            "n": int(row["count"]),
            "dose_media": round(float(row["mean"]), 3),
            "desvio": round(float(row["std"]), 3) if pd.notna(row["std"]) else 0.0,
        }
        for interval, row in grouped.iterrows()
    ]

    corr = float(frame["vigor_index"].corr(frame[sch.VALUE]))
    doses = grouped["mean"].to_numpy()
    spread = float(np.ptp(doses) / np.mean(doses) * 100.0) if np.mean(doses) else 0.0

    if spread < 5:
        reading = ("A dose praticamente não variou entre as classes de vigor: "
                   "a aplicação foi, na prática, uniforme.")
    elif corr < -0.3:
        reading = ("Dose maior onde o vigor é menor — comportamento compensatório, "
                   "típico de aplicação corretiva.")
    elif corr > 0.3:
        reading = ("Dose maior onde o vigor é maior — comportamento proporcional à biomassa, "
                   "típico de fungicida e dessecação.")
    else:
        reading = "Relação fraca entre vigor e dose; verifique a configuração do mapa de resposta."

    return {
        "available": True,
        "classes": rows,
        "correlacao": round(corr, 3) if pd.notna(corr) else None,
        "amplitude_relativa_pct": round(spread, 1),
        "leitura": reading,
    }
