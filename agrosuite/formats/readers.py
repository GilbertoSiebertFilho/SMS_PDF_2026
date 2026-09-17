"""Leitores de arquivos de campo.

Cobre os formatos que efetivamente circulam entre monitores e escritório:

* **Shapefile** (.shp + .dbf + .shx + .prj) — a moeda corrente entre
  plataformas; todo fabricante da lista consegue ler ou escrever.
* **GeoJSON / JSON** — saída padrão do Augmenta e de serviços web.
* **CSV / TXT** — exportação de log dos monitores, com todas as variações
  de separador, decimal e codificação que aparecem no mundo real.
* **Excel** (.xlsx) — planilhas de amostragem e de ensaios.
* **KML/KMZ** — contornos de talhão.

O ISOXML tem módulo próprio (:mod:`agrosuite.formats.isoxml`) por ser um
formato de pasta com cabeçalho XML e logs binários.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from ..core import schema as sch
from ..core.crs import WGS84, looks_geographic
from ..core.dataset import Dataset, DatasetMeta
from . import brands as brands_mod

#: Codificações testadas em ordem — DBF antigo e CSV brasileiro costumam
#: vir em latin-1, e monitores europeus às vezes em cp1252.
ENCODINGS = ("utf-8-sig", "utf-8", "latin-1", "cp1252")

#: Separadores candidatos para arquivos tabulares.
DELIMITERS = (",", ";", "\t", "|")


# ==========================================================================
# Utilidades
# ==========================================================================

def _read_text(path: Path, limit: int | None = None) -> str:
    """Lê um arquivo de texto tentando as codificações usuais."""
    raw = path.read_bytes() if limit is None else path.open("rb").read(limit)
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _sniff_delimiter(sample: str) -> str:
    """Descobre o separador de colunas de um CSV."""
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        pass
    first_line = sample.splitlines()[0] if sample.splitlines() else ""
    counts = {d: first_line.count(d) for d in DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def _looks_like_unit_row(row: pd.Series) -> bool:
    """Detecta a linha de unidades que alguns monitores inserem sob o cabeçalho.

    Ex.: ``Yield, Moisture, Speed`` seguido de ``bu/ac, %, mph``. Essa linha
    é textual em colunas que deveriam ser numéricas.
    """
    values = [str(v).strip() for v in row.tolist() if str(v).strip() not in ("", "nan")]
    if not values:
        return False
    unit_like = {
        "bu/ac", "kg/ha", "t/ha", "lb/ac", "%", "mph", "km/h", "m/s", "ft", "m",
        "deg", "sec", "s", "ac", "ha", "gal/ac", "l/ha", "sc/ha", "seeds/ac",
    }
    hits = sum(1 for v in values if v.lower() in unit_like)
    numeric = 0
    for v in values:
        try:
            float(v.replace(",", "."))
            numeric += 1
        except ValueError:
            pass
    return hits >= 1 and numeric <= len(values) * 0.3


def _decimal_comma_ratio(sample: str, delimiter: str) -> float:
    """Fração de campos que parecem número com vírgula decimal."""
    lines = [ln for ln in sample.splitlines()[1:40] if ln.strip()]
    if not lines:
        return 0.0
    total = comma_numbers = 0
    for line in lines:
        for cell in line.split(delimiter):
            cell = cell.strip().strip('"')
            if not cell:
                continue
            total += 1
            if "," in cell and cell.replace(",", "").replace("-", "").replace(".", "").isdigit():
                comma_numbers += 1
    return comma_numbers / total if total else 0.0


def _normalize_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Renomeia colunas reconhecidas para o esquema canônico."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    mapping = sch.map_columns(df.columns)
    if mapping:
        df = df.rename(columns=mapping)
    return df, mapping


def _resolve_coordinates(df: pd.DataFrame, notes: list[str]) -> pd.DataFrame:
    """Garante colunas ``lon``/``lat`` a partir do que existir na tabela."""
    if sch.LON in df.columns and sch.LAT in df.columns:
        return df
    # Alguns exports nomeiam as colunas simplesmente X e Y.
    candidates = [(c, sch.normalize_name(c)) for c in df.columns]
    x_col = next((c for c, n in candidates if n in ("x", "coord_x", "este", "easting")), None)
    y_col = next((c for c, n in candidates if n in ("y", "coord_y", "norte", "northing")), None)
    if x_col and y_col:
        if looks_geographic(df[x_col], df[y_col]):
            df = df.rename(columns={x_col: sch.LON, y_col: sch.LAT})
            notes.append("Colunas X/Y interpretadas como longitude/latitude (WGS84).")
        else:
            notes.append(
                "Colunas X/Y em unidades projetadas sem CRS declarado — "
                "informe o EPSG de origem para reprojetar corretamente."
            )
    return df


def _pick_value_column(df: pd.DataFrame, notes: list[str]) -> pd.DataFrame:
    """Elege a variável principal quando o mapeamento não encontrou ``value``."""
    if sch.VALUE in df.columns:
        return df
    priority = (sch.APPLIED_RATE, sch.TARGET_RATE, sch.FLOW)
    for col in priority:
        if col in df.columns:
            df[sch.VALUE] = pd.to_numeric(df[col], errors="coerce")
            notes.append(f"Variável principal assumida a partir de '{sch.LABELS[col]}'.")
            return df
    ignore = {sch.LON, sch.LAT, sch.X, sch.Y, sch.ELEVATION, sch.SPEED, sch.SWATH,
              sch.HEADING, sch.DISTANCE, sch.PASS, sch.SECTION, sch.ELAPSED}
    numeric = [
        c for c in df.columns
        if c not in ignore and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()
    ]
    if numeric:
        # A coluna numérica com maior variabilidade relativa costuma ser a medida.
        best, best_cv = None, -1.0
        for c in numeric:
            s = df[c].dropna()
            if s.empty or s.mean() == 0:
                continue
            cv = abs(float(s.std()) / float(s.mean()))
            if 0 < cv < 5 and cv > best_cv:
                best, best_cv = c, cv
        chosen = best or numeric[0]
        df[sch.VALUE] = pd.to_numeric(df[chosen], errors="coerce")
        notes.append(f"Variável principal assumida a partir da coluna '{chosen}'.")
    return df


def _guess_operation(df: pd.DataFrame, path: Path, brand: str) -> str:
    """Infere o tipo de operação a partir de nomes de coluna e do caminho."""
    cols = {sch.normalize_name(c) for c in df.columns}
    text = sch.normalize_name(str(path))
    if any(k in text for k in ("boundary", "contorno", "limite", "field_border")):
        return "boundary"
    if any(k in text for k in ("prescription", "prescricao", "_rx", "rx_", "target")):
        return "prescription"
    if sch.TARGET_RATE in cols and sch.VALUE not in cols:
        return "prescription"
    if any(k in cols for k in ("yield", "yld", "dry_yield", "moisture_pct", "flow_kgs")):
        return "harvest"
    if any(k in text for k in ("harvest", "colheita", "yield", "rendimento")):
        return "harvest"
    if any(k in cols for k in ("vigor", "ndvi", "ndre", "biomass_index", "canopy")):
        return "vigor"
    if brand == "augmenta":
        return "vigor"
    if any(k in text for k in ("planting", "plantio", "seeding", "semeadura")):
        return "planting"
    if any(k in text for k in ("applied", "aplicacao", "application", "spray", "fert")):
        return "application"
    if sch.APPLIED_RATE in cols:
        return "application"
    return "unknown"


def _build_dataset(
    df: pd.DataFrame,
    path: Path,
    source_format: str,
    notes: list[str],
    geometry: list | None = None,
    brand_hint: str | None = None,
    extra_text: str = "",
) -> Dataset:
    """Monta o :class:`Dataset` final a partir de uma tabela já lida."""
    original_columns = list(df.columns)
    df, mapping = _normalize_frame(df)
    df = _resolve_coordinates(df, notes)
    df = _pick_value_column(df, notes)

    brand_key, confidence = brands_mod.detect_brand(
        original_columns, str(path), extra_text
    )
    if brand_hint:
        brand_key, confidence = brand_hint, 1.0
    brand = brands_mod.get_brand(brand_key)

    meta = DatasetMeta(
        name=path.stem or "dataset",
        source_path=str(path),
        source_format=source_format,
        brand=brand.key,
        brand_label=brand.label,
        operation=_guess_operation(df, path, brand.key),
        geometry_type="polygon" if geometry is not None else "point",
        notes=notes,
        extra={
            "brand_confidence": confidence,
            "column_mapping": mapping,
            "original_columns": original_columns,
        },
    )
    return Dataset(df, meta, geometry=geometry)


# ==========================================================================
# Leitores por formato
# ==========================================================================

def read_shapefile(path: Path, brand_hint: str | None = None) -> Dataset:
    """Lê shapefile (ou qualquer fonte vetorial suportada pelo GDAL)."""
    import geopandas as gpd

    gdf = gpd.read_file(path)
    notes: list[str] = []

    if gdf.crs is None:
        notes.append(
            "Shapefile sem arquivo .prj — coordenadas assumidas em WGS84. "
            "Se o talhão aparecer fora de lugar no mapa, informe o EPSG correto."
        )
        gdf = gdf.set_crs(WGS84, allow_override=True)
    elif gdf.crs.to_string() != WGS84:
        notes.append(f"Reprojetado de {gdf.crs.to_string()} para WGS84.")
        gdf = gdf.to_crs(WGS84)

    geom_types = set(gdf.geom_type.dropna().unique())
    polygonal = bool(geom_types & {"Polygon", "MultiPolygon"})
    linear = bool(geom_types & {"LineString", "MultiLineString"})

    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    centroids = gdf.geometry.representative_point()
    df[sch.LON] = centroids.x.to_numpy()
    df[sch.LAT] = centroids.y.to_numpy()

    geometry = list(gdf.geometry) if (polygonal or linear) else None
    if polygonal:
        notes.append("Camada poligonal: centroides usados para posicionamento e análise.")

    ds = _build_dataset(df, Path(path), "shapefile", notes, geometry, brand_hint)
    if polygonal and ds.meta.operation == "unknown":
        ds.meta.operation = "prescription" if sch.TARGET_RATE in ds.df.columns else "boundary"
    if linear:
        ds.meta.geometry_type = "line"
        if ds.meta.operation == "unknown":
            ds.meta.operation = "guidance"
    return ds


def read_geojson(path: Path, brand_hint: str | None = None) -> Dataset:
    """Lê GeoJSON/JSON — inclusive o formato de sessão do Augmenta."""
    from .augmenta import is_augmenta_payload, read_augmenta

    payload = json.loads(_read_text(Path(path)))
    if is_augmenta_payload(payload):
        return read_augmenta(Path(path), payload)

    import geopandas as gpd

    gdf = gpd.read_file(path)
    notes: list[str] = []
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84, allow_override=True)
    elif gdf.crs.to_string() != WGS84:
        gdf = gdf.to_crs(WGS84)

    polygonal = bool(set(gdf.geom_type.dropna().unique()) & {"Polygon", "MultiPolygon"})
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    pts = gdf.geometry.representative_point()
    df[sch.LON] = pts.x.to_numpy()
    df[sch.LAT] = pts.y.to_numpy()
    geometry = list(gdf.geometry) if polygonal else None
    return _build_dataset(df, Path(path), "geojson", notes, geometry, brand_hint)


def read_tabular(path: Path, brand_hint: str | None = None) -> Dataset:
    """Lê CSV/TXT de monitor, lidando com separador, decimal e codificação."""
    path = Path(path)
    sample = _read_text(path, limit=64_000)
    if not sample.strip():
        raise ValueError(f"Arquivo vazio: {path.name}")

    notes: list[str] = []
    delimiter = _sniff_delimiter(sample)
    decimal = "." 
    if delimiter != "," and _decimal_comma_ratio(sample, delimiter) > 0.25:
        decimal = ","
        notes.append("Vírgula interpretada como separador decimal.")

    last_error: Exception | None = None
    df = None
    for enc in ENCODINGS:
        try:
            df = pd.read_csv(
                path, sep=delimiter, encoding=enc, decimal=decimal,
                engine="python", skip_blank_lines=True, on_bad_lines="skip",
            )
            break
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_error = exc
    if df is None:
        raise ValueError(f"Não foi possível interpretar {path.name}: {last_error}")

    if len(df) and _looks_like_unit_row(df.iloc[0]):
        units_row = {c: str(v).strip() for c, v in df.iloc[0].items()}
        df = df.iloc[1:].reset_index(drop=True)
        notes.append("Linha de unidades sob o cabeçalho descartada.")
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="ignore")
        ds = _build_dataset(df, path, "csv", notes, brand_hint=brand_hint, extra_text=sample[:2000])
        ds.meta.extra["source_units"] = units_row
        return ds

    return _build_dataset(df, path, "csv", notes, brand_hint=brand_hint, extra_text=sample[:2000])


def read_excel(path: Path, brand_hint: str | None = None) -> Dataset:
    """Lê a primeira planilha de um arquivo Excel."""
    df = pd.read_excel(path)
    notes = ["Planilha Excel: primeira aba importada."]
    return _build_dataset(df, Path(path), "excel", notes, brand_hint=brand_hint)


def read_kml(path: Path, brand_hint: str | None = None) -> Dataset:
    """Lê KML/KMZ — tipicamente contornos de talhão."""
    import geopandas as gpd

    path = Path(path)
    notes: list[str] = []
    if path.suffix.lower() == ".kmz":
        with zipfile.ZipFile(path) as zf:
            inner = next((n for n in zf.namelist() if n.lower().endswith(".kml")), None)
            if inner is None:
                raise ValueError("KMZ não contém arquivo .kml.")
            data = zf.read(inner)
        gdf = gpd.read_file(io.BytesIO(data))
        notes.append(f"KMZ descompactado ({inner}).")
    else:
        gdf = gpd.read_file(path)

    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84, allow_override=True)
    elif gdf.crs.to_string() != WGS84:
        gdf = gdf.to_crs(WGS84)

    polygonal = bool(set(gdf.geom_type.dropna().unique()) & {"Polygon", "MultiPolygon"})
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    pts = gdf.geometry.representative_point()
    df[sch.LON] = pts.x.to_numpy()
    df[sch.LAT] = pts.y.to_numpy()
    ds = _build_dataset(df, path, "kml", notes, list(gdf.geometry) if polygonal else None, brand_hint)
    if polygonal:
        ds.meta.operation = "boundary"
    return ds
