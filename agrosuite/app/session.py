"""Estado da sessão do app.

Como o AgroSuite roda localmente e para uma pessoa só, o estado vive em
memória: os datasets carregados, os resultados de limpeza e análise, e os
arquivos gerados para exportação. Nada é gravado sem o usuário pedir.

Um dataset nunca é sobrescrito. Limpar gera dois novos datasets (limpo e
removidos) que convivem com o original — é o que permite comparar antes e
depois, e desfazer uma limpeza malfeita sem reimportar o arquivo.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..core import schema as sch
from ..core.dataset import Dataset

#: Teto de pontos enviados ao mapa numa única resposta. Acima disso a
#: amostragem é sistemática — preserva o padrão espacial sem travar o navegador.
MAP_POINT_LIMIT = 60_000


@dataclass
class Entry:
    """Um dataset na sessão, com seu histórico."""

    id: str
    dataset: Dataset
    label: str
    origin: str = "import"
    parent_id: str | None = None
    reports: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        data = self.dataset.summary()
        data.update({
            "id": self.id,
            "label": self.label,
            "origin": self.origin,
            "parent_id": self.parent_id,
            "has_clean_report": "clean" in self.reports,
            "has_difm_report": "difm" in self.reports,
        })
        return data


class Session:
    """Repositório em memória dos dados da sessão."""

    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}
        self._files: dict[str, Path] = {}
        self._lock = threading.Lock()
        self.workdir = Path(tempfile.mkdtemp(prefix="agrosuite_"))
        self.uploads = self.workdir / "uploads"
        self.exports = self.workdir / "exports"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.exports.mkdir(parents=True, exist_ok=True)

    # -- datasets --------------------------------------------------------
    def add(
        self,
        dataset: Dataset,
        label: str | None = None,
        origin: str = "import",
        parent_id: str | None = None,
    ) -> Entry:
        entry = Entry(
            id=uuid.uuid4().hex[:12],
            dataset=dataset,
            label=label or dataset.meta.name,
            origin=origin,
            parent_id=parent_id,
        )
        with self._lock:
            self._entries[entry.id] = entry
        return entry

    def get(self, dataset_id: str) -> Entry:
        entry = self._entries.get(dataset_id)
        if entry is None:
            raise KeyError(f"Dataset '{dataset_id}' não está carregado na sessão.")
        return entry

    def remove(self, dataset_id: str) -> None:
        with self._lock:
            self._entries.pop(dataset_id, None)

    def list(self) -> list[dict[str, Any]]:
        return [entry.summary() for entry in self._entries.values()]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # -- arquivos --------------------------------------------------------
    def register_file(self, path: Path) -> str:
        """Registra um arquivo gerado e devolve o token de download."""
        token = uuid.uuid4().hex[:16]
        with self._lock:
            self._files[token] = Path(path)
        return token

    def file_for(self, token: str) -> Path:
        path = self._files.get(token)
        if path is None or not path.exists():
            raise KeyError("Arquivo não encontrado ou já removido.")
        return path

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# Serialização para o mapa e os gráficos
# --------------------------------------------------------------------------

def map_payload(
    dataset: Dataset,
    column: str = sch.VALUE,
    limit: int = MAP_POINT_LIMIT,
) -> dict[str, Any]:
    """Prepara os pontos para o desenho no mapa.

    Devolve vetores paralelos em vez de GeoJSON: para dezenas de milhares de
    pontos, o GeoJSON multiplica por cinco o tamanho da resposta e o tempo de
    interpretação no navegador, sem nenhum ganho — o desenho é feito em
    canvas, que só precisa das coordenadas e do valor.
    """
    df = dataset.df
    if sch.LON not in df.columns or sch.LAT not in df.columns:
        return {"count": 0, "lon": [], "lat": [], "values": [], "column": column}

    columns = [sch.LON, sch.LAT] + ([column] if column in df.columns else [])
    frame = df[columns].dropna(subset=[sch.LON, sch.LAT])

    total = len(frame)
    if total > limit:
        # Amostragem sistemática: mantém a cobertura espacial da passada,
        # ao contrário de um corte nas primeiras N linhas.
        step = int(np.ceil(total / limit))
        frame = frame.iloc[::step]

    values: list[float | None] = []
    if column in frame.columns:
        series = pd.to_numeric(frame[column], errors="coerce")
        values = [None if pd.isna(v) else round(float(v), 4) for v in series]

    payload: dict[str, Any] = {
        "count": int(len(frame)),
        "total": int(total),
        "sampled": bool(total > limit),
        "column": column,
        "lon": [round(float(v), 7) for v in frame[sch.LON]],
        "lat": [round(float(v), 7) for v in frame[sch.LAT]],
        "values": values,
        "bounds": dataset.bounds(),
    }

    if values:
        finite = [v for v in values if v is not None]
        if finite:
            array = np.asarray(finite, dtype="float64")
            payload["scale"] = {
                "min": float(np.min(array)),
                "max": float(np.max(array)),
                # A escala de cor usa percentis: um único ponto extremo não
                # deve achatar o contraste do mapa inteiro.
                "low": float(np.percentile(array, 2)),
                "high": float(np.percentile(array, 98)),
            }

    if dataset.geometry is not None:
        payload["polygons"] = _polygon_payload(dataset)

    return payload


def _polygon_payload(dataset: Dataset, limit: int = 4000) -> list[list[list[float]]]:
    """Anéis externos dos polígonos, para desenhar contornos e grades."""
    rings: list[list[list[float]]] = []
    for geometry in (dataset.geometry or [])[:limit]:
        if geometry is None or geometry.is_empty:
            continue
        parts = geometry.geoms if geometry.geom_type.startswith("Multi") else [geometry]
        for part in parts:
            if part.geom_type == "Polygon":
                rings.append([[round(x, 7), round(y, 7)] for x, y in part.exterior.coords])
            elif part.geom_type == "LineString":
                rings.append([[round(x, 7), round(y, 7)] for x, y in part.coords])
    return rings


def preview_table(dataset: Dataset, rows: int = 25) -> dict[str, Any]:
    """Amostra da tabela para inspeção visual na interface."""
    frame = dataset.df.head(rows).copy()
    for column in frame.columns:
        if pd.api.types.is_datetime64_any_dtype(frame[column]):
            frame[column] = frame[column].dt.strftime("%Y-%m-%d %H:%M:%S")
    frame = frame.replace({np.nan: None, np.inf: None, -np.inf: None})

    records = []
    for record in frame.to_dict("records"):
        clean: dict[str, Any] = {}
        for key, value in record.items():
            if isinstance(value, (np.integer,)):
                clean[key] = int(value)
            elif isinstance(value, (np.floating,)):
                clean[key] = None if not np.isfinite(value) else round(float(value), 4)
            elif isinstance(value, (np.bool_,)):
                clean[key] = bool(value)
            else:
                clean[key] = value
        records.append(clean)

    return {"columns": list(frame.columns), "rows": records}
