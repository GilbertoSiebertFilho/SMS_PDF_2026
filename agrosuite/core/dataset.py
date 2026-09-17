"""Modelo de dados canônico do AgroSuite.

Um :class:`Dataset` é a representação única para a qual convergem todos os
formatos de entrada (shapefile, CSV de monitor, ISOXML, GeoJSON do Augmenta).
Internamente ele guarda um ``pandas.DataFrame`` — e não um ``GeoDataFrame`` —
porque as operações pesadas do app (filtros de limpeza, vizinhança, junções
por grade) são vetoriais sobre colunas ``x``/``y`` projetadas; a geometria
shapely só é materializada quando realmente necessária (recorte por
contorno, exportação, cálculo de sobreposição).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np
import pandas as pd

from . import crs as crs_mod
from . import schema as sch
from . import units as units_mod

#: Operações reconhecidas pelo app.
OPERATIONS = (
    "harvest",       # colheita / mapa de rendimento
    "application",   # aplicação (as-applied) de fertilizante/defensivo
    "planting",      # plantio / semeadura
    "prescription",  # mapa de prescrição (Rx)
    "boundary",      # contorno de talhão
    "guidance",      # linhas de orientação
    "vigor",         # índice de vegetação / Augmenta
    "soil",          # amostragem de solo
    "unknown",
)

OPERATION_LABELS = {
    "harvest": "Colheita (rendimento)",
    "application": "Aplicação (as-applied)",
    "planting": "Plantio / semeadura",
    "prescription": "Prescrição (Rx)",
    "boundary": "Contorno do talhão",
    "guidance": "Linhas de orientação",
    "vigor": "Vigor / índice vegetativo",
    "soil": "Amostragem de solo",
    "unknown": "Não identificado",
}


@dataclass
class DatasetMeta:
    """Metadados de procedência e interpretação de um dataset."""

    name: str = "dataset"
    source_path: str = ""
    source_format: str = "unknown"
    brand: str = "unknown"
    brand_label: str = "Desconhecido"
    operation: str = "unknown"
    crop: str | None = None
    field_name: str | None = None
    value_label: str = "Valor"
    value_unit: str = ""
    source_value_unit: str = ""
    geometry_type: str = "point"
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["operation_label"] = OPERATION_LABELS.get(self.operation, self.operation)
        return data


class Dataset:
    """Conjunto de pontos (ou polígonos) georreferenciados já normalizado.

    Parameters
    ----------
    df:
        Tabela com, no mínimo, as colunas ``lon`` e ``lat`` em WGS84.
    meta:
        Metadados de procedência.
    geometry:
        Lista opcional de geometrias shapely alinhada com ``df`` — usada
        quando a fonte é poligonal (contorno, grade de prescrição).
    """

    def __init__(
        self,
        df: pd.DataFrame,
        meta: DatasetMeta | None = None,
        geometry: list | None = None,
    ) -> None:
        self.df = df.reset_index(drop=True)
        self.meta = meta or DatasetMeta()
        self.geometry = list(geometry) if geometry is not None else None
        self.metric_crs: str | None = None
        self._prepare()

    # ------------------------------------------------------------------
    # Preparação
    # ------------------------------------------------------------------
    def _prepare(self) -> None:
        """Garante tipos, coordenadas projetadas e campos derivados."""
        for col in sch.NUMERIC_COLUMNS:
            if col in self.df.columns and not pd.api.types.is_numeric_dtype(self.df[col]):
                self.df[col] = pd.to_numeric(self.df[col], errors="coerce")

        if sch.TIMESTAMP in self.df.columns:
            if not pd.api.types.is_datetime64_any_dtype(self.df[sch.TIMESTAMP]):
                self.df[sch.TIMESTAMP] = pd.to_datetime(
                    self.df[sch.TIMESTAMP], errors="coerce", format="mixed"
                )

        if sch.LON in self.df.columns and sch.LAT in self.df.columns:
            self.project()

    def project(self, target: str | None = None) -> None:
        """Preenche ``x``/``y`` num CRS métrico (UTM automático por padrão)."""
        from pyproj import Transformer

        lon = self.df[sch.LON].to_numpy(dtype="float64", na_value=np.nan)
        lat = self.df[sch.LAT].to_numpy(dtype="float64", na_value=np.nan)
        if not np.isfinite(lon).any():
            return
        self.metric_crs = target or crs_mod.pick_metric_crs(lon, lat)
        transformer = Transformer.from_crs(crs_mod.WGS84, self.metric_crs, always_xy=True)
        x, y = transformer.transform(lon, lat)
        self.df[sch.X] = x
        self.df[sch.Y] = y

    # ------------------------------------------------------------------
    # Campos derivados
    # ------------------------------------------------------------------
    def ensure_derived(self, default_swath_m: float | None = None) -> None:
        """Calcula distância, velocidade, rumo e passadas quando ausentes.

        Muitos CSVs de monitor trazem apenas posição e valor. Sem velocidade
        e largura de faixa nenhum filtro de limpeza sério funciona, então
        essas grandezas são reconstruídas a partir da própria trajetória.
        """
        self.sort_by_time()
        n = len(self.df)
        if n == 0:
            return

        has_xy = sch.X in self.df.columns and sch.Y in self.df.columns
        if has_xy:
            x = self.df[sch.X].to_numpy(dtype="float64", na_value=np.nan)
            y = self.df[sch.Y].to_numpy(dtype="float64", na_value=np.nan)
            dx = np.diff(x, prepend=x[0])
            dy = np.diff(y, prepend=y[0])
            step = np.hypot(dx, dy)
            step[0] = step[1] if n > 1 else 0.0

            if sch.DISTANCE not in self.df.columns:
                self.df[sch.DISTANCE] = step

            if sch.HEADING not in self.df.columns:
                heading = (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0
                if n > 1:
                    heading[0] = heading[1]
                self.df[sch.HEADING] = heading

            dt = self._time_delta_seconds()
            if sch.SPEED not in self.df.columns and dt is not None:
                with np.errstate(divide="ignore", invalid="ignore"):
                    speed = np.where(dt > 0, step / dt * 3.6, np.nan)
                self.df[sch.SPEED] = pd.Series(speed).ffill().bfill().to_numpy()
                self.meta.notes.append(
                    "Velocidade reconstruída a partir da trajetória e do tempo."
                )

        if sch.SPEED in self.df.columns:
            unit = units_mod.guess_speed_unit(self.df[sch.SPEED])
            if unit != "km/h":
                self.df[sch.SPEED] = units_mod.speed_to_kmh(self.df[sch.SPEED], unit)
                self.meta.notes.append(f"Velocidade convertida de {unit} para km/h.")

        if sch.SWATH not in self.df.columns and default_swath_m:
            self.df[sch.SWATH] = float(default_swath_m)
            self.meta.notes.append(
                f"Largura de faixa ausente no arquivo; adotado {default_swath_m:g} m."
            )

        if sch.PASS not in self.df.columns and sch.HEADING in self.df.columns:
            self.df[sch.PASS] = self._detect_passes()

    def _time_delta_seconds(self) -> np.ndarray | None:
        """Intervalo entre registros consecutivos, em segundos."""
        if sch.TIMESTAMP in self.df.columns:
            ts = self.df[sch.TIMESTAMP]
            if pd.api.types.is_datetime64_any_dtype(ts) and ts.notna().any():
                seconds = ts.astype("int64").to_numpy(dtype="float64") / 1e9
                seconds[ts.isna().to_numpy()] = np.nan
                dt = np.diff(seconds, prepend=seconds[0])
                if len(dt) > 1:
                    dt[0] = dt[1]
                # Intervalos absurdos indicam salto entre operações distintas.
                dt[(dt <= 0) | (dt > 60)] = np.nan
                return dt
        if sch.ELAPSED in self.df.columns:
            seconds = self.df[sch.ELAPSED].to_numpy(dtype="float64", na_value=np.nan)
            dt = np.diff(seconds, prepend=seconds[0])
            if len(dt) > 1:
                dt[0] = dt[1]
            dt[(dt <= 0) | (dt > 60)] = np.nan
            return dt
        return None

    def _detect_passes(self, angle_tol_deg: float = 35.0) -> np.ndarray:
        """Numera as passadas agrupando registros com rumo estável.

        Uma passada termina quando a máquina gira mais do que ``angle_tol_deg``
        em relação ao rumo médio corrente — o que corresponde à manobra de
        cabeceira.
        """
        heading = self.df[sch.HEADING].to_numpy(dtype="float64", na_value=np.nan)
        n = len(heading)
        pass_id = np.zeros(n, dtype="int32")
        if n == 0:
            return pass_id

        current = 0
        reference = heading[0] if math.isfinite(heading[0]) else 0.0
        for i in range(1, n):
            h = heading[i]
            if not math.isfinite(h):
                pass_id[i] = current
                continue
            diff = abs((h - reference + 180.0) % 360.0 - 180.0)
            if diff > angle_tol_deg:
                current += 1
                reference = h
            else:
                # Média circular amortecida mantém a referência estável em curvas suaves.
                reference = reference + 0.15 * ((h - reference + 180.0) % 360.0 - 180.0)
                reference %= 360.0
            pass_id[i] = current
        return pass_id

    def sort_by_time(self) -> None:
        """Ordena cronologicamente — pré-requisito de todos os filtros sequenciais."""
        if sch.TIMESTAMP in self.df.columns and self.df[sch.TIMESTAMP].notna().any():
            self.df = self.df.sort_values(sch.TIMESTAMP, kind="stable").reset_index(drop=True)
        elif sch.ELAPSED in self.df.columns and self.df[sch.ELAPSED].notna().any():
            self.df = self.df.sort_values(sch.ELAPSED, kind="stable").reset_index(drop=True)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.df)

    @property
    def columns(self) -> list[str]:
        return list(self.df.columns)

    def numeric_columns(self) -> list[str]:
        """Colunas numéricas candidatas a virar a variável analisada."""
        skip = {sch.LON, sch.LAT, sch.X, sch.Y, sch.ELAPSED}
        return [
            c for c in self.df.columns
            if c not in skip and pd.api.types.is_numeric_dtype(self.df[c])
        ]

    def bounds(self) -> list[float] | None:
        """Retângulo envolvente em WGS84: ``[oeste, sul, leste, norte]``."""
        if sch.LON not in self.df.columns or self.df.empty:
            return None
        lon = self.df[sch.LON].to_numpy(dtype="float64", na_value=np.nan)
        lat = self.df[sch.LAT].to_numpy(dtype="float64", na_value=np.nan)
        mask = np.isfinite(lon) & np.isfinite(lat)
        if not mask.any():
            return None
        return [
            float(np.min(lon[mask])), float(np.min(lat[mask])),
            float(np.max(lon[mask])), float(np.max(lat[mask])),
        ]

    def stats(self, column: str = sch.VALUE) -> dict[str, float | int]:
        """Estatísticas descritivas da coluna informada."""
        if column not in self.df.columns:
            return {}
        s = pd.to_numeric(self.df[column], errors="coerce").dropna()
        if s.empty:
            return {"n": 0}
        mean = float(s.mean())
        std = float(s.std(ddof=1)) if len(s) > 1 else 0.0
        return {
            "n": int(s.size),
            "mean": mean,
            "std": std,
            "cv": float(std / mean * 100.0) if mean else 0.0,
            "min": float(s.min()),
            "p05": float(s.quantile(0.05)),
            "q1": float(s.quantile(0.25)),
            "median": float(s.median()),
            "q3": float(s.quantile(0.75)),
            "p95": float(s.quantile(0.95)),
            "max": float(s.max()),
            "skew": float(s.skew()) if len(s) > 2 else 0.0,
        }

    def area_ha(self) -> float:
        """Área trabalhada estimada pela soma das faixas (largura × avanço)."""
        if sch.SWATH not in self.df.columns or sch.DISTANCE not in self.df.columns:
            return 0.0
        swath = self.df[sch.SWATH].to_numpy(dtype="float64", na_value=np.nan)
        dist = self.df[sch.DISTANCE].to_numpy(dtype="float64", na_value=np.nan)
        area = np.nansum(swath * dist)
        return float(area / 10_000.0)

    # ------------------------------------------------------------------
    # Interoperabilidade
    # ------------------------------------------------------------------
    def to_geodataframe(self, metric: bool = False):
        """Converte para ``GeoDataFrame`` (WGS84 ou no CRS métrico)."""
        import geopandas as gpd
        from shapely.geometry import Point

        if self.geometry is not None:
            gdf = gpd.GeoDataFrame(self.df.copy(), geometry=self.geometry, crs=crs_mod.WGS84)
        else:
            geom = gpd.points_from_xy(self.df[sch.LON], self.df[sch.LAT])
            gdf = gpd.GeoDataFrame(self.df.copy(), geometry=geom, crs=crs_mod.WGS84)
        if metric and self.metric_crs:
            gdf = gdf.to_crs(self.metric_crs)
        return gdf

    def copy(self) -> "Dataset":
        """Cópia independente, preservando metadados e CRS métrico."""
        import copy as _copy

        clone = Dataset.__new__(Dataset)
        clone.df = self.df.copy()
        clone.meta = _copy.deepcopy(self.meta)
        clone.geometry = list(self.geometry) if self.geometry is not None else None
        clone.metric_crs = self.metric_crs
        return clone

    def subset(self, mask) -> "Dataset":
        """Novo dataset contendo apenas as linhas onde ``mask`` é verdadeiro."""
        mask = np.asarray(mask, dtype=bool)
        clone = self.copy()
        clone.df = self.df.loc[mask].reset_index(drop=True)
        if self.geometry is not None:
            clone.geometry = [g for g, keep in zip(self.geometry, mask) if keep]
        return clone

    def summary(self) -> dict[str, Any]:
        """Resumo serializável usado pela interface."""
        return {
            "meta": self.meta.to_dict(),
            "rows": len(self.df),
            "columns": self.columns,
            "numeric_columns": self.numeric_columns(),
            "bounds": self.bounds(),
            "metric_crs": self.metric_crs,
            "area_ha": round(self.area_ha(), 2),
            "stats": self.stats(),
        }
