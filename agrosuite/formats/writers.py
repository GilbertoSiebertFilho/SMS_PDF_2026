"""Escrita de arquivos para levar de volta ao monitor.

Cada plataforma tem suas manias, e a maioria delas são pequenas — mas
suficientes para o monitor recusar o arquivo no meio da lavoura:

* O DBF do shapefile limita nomes de campo a **10 caracteres**. Nomes mais
  longos são truncados em silêncio pela biblioteca, e dois campos podem
  colidir; aqui o truncamento é feito de forma controlada e reportada.
* Cada monitor procura a dose num nome de campo diferente. O exportador
  grava o campo com o nome que a plataforma escolhida espera e, por
  segurança, repete o valor num campo ``RATE`` genérico.
* Prescrição precisa ser **polígono**, não ponto. Quando a origem é um
  conjunto de pontos, as células são geradas como grade.
* O ISOXML exige a grade completa e retangular, com zeros onde não há
  aplicação.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..core import schema as sch
from ..core.crs import WGS84
from ..core.dataset import Dataset
from . import isoxml as isoxml_mod

#: Nome do campo de dose esperado por cada plataforma no shapefile de Rx.
RATE_FIELD_BY_BRAND = {
    "john_deere": "RATE",
    "ag_leader": "RATE",
    "raven": "RATE",
    "trimble": "TGT_RATE",
    "case_ih": "RATE",
    "new_holland": "RATE",
    "bourgault": "RATE",
    "vaderstad": "RATE",
    "augmenta": "RATE",
    "climate_fieldview": "RATE",
    "topcon": "RATE",
    "generic": "RATE",
}

#: Limite de caracteres de um nome de campo no formato DBF.
DBF_FIELD_LIMIT = 10


def safe_field_names(columns: list[str]) -> tuple[dict[str, str], list[str]]:
    """Trunca nomes para o limite do DBF resolvendo colisões.

    Returns
    -------
    (mapa, avisos)
        ``mapa`` leva do nome original ao nome gravado.
    """
    mapping: dict[str, str] = {}
    used: set[str] = set()
    warnings: list[str] = []

    for column in columns:
        name = str(column)[:DBF_FIELD_LIMIT]
        if name in used:
            for suffix in range(1, 100):
                candidate = f"{str(column)[:DBF_FIELD_LIMIT - len(str(suffix))]}{suffix}"
                if candidate not in used:
                    name = candidate
                    break
        if name != str(column):
            warnings.append(f"Campo '{column}' gravado como '{name}' (limite do DBF).")
        mapping[column] = name
        used.add(name)
    return mapping, warnings


# ==========================================================================
# Saídas genéricas
# ==========================================================================

def write_vector(
    dataset: Dataset,
    path: Path,
    driver: str | None = None,
    columns: list[str] | None = None,
) -> dict[str, Any]:
    """Grava um dataset como shapefile, GeoPackage ou GeoJSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf = dataset.to_geodataframe()

    if columns:
        keep = [c for c in columns if c in gdf.columns]
        gdf = gdf[keep + [gdf.geometry.name]]

    # Datas viram texto: o DBF não tem tipo datetime.
    for column in gdf.columns:
        if pd.api.types.is_datetime64_any_dtype(gdf[column]):
            gdf[column] = gdf[column].dt.strftime("%Y-%m-%d %H:%M:%S")

    warnings: list[str] = []
    suffix = path.suffix.lower()
    if driver is None:
        driver = {".shp": "ESRI Shapefile", ".gpkg": "GPKG", ".geojson": "GeoJSON",
                  ".json": "GeoJSON"}.get(suffix, "ESRI Shapefile")

    if driver == "ESRI Shapefile":
        mapping, warnings = safe_field_names(
            [c for c in gdf.columns if c != gdf.geometry.name]
        )
        gdf = gdf.rename(columns=mapping)

    gdf.to_file(path, driver=driver)
    return {"path": str(path), "driver": driver, "features": len(gdf), "warnings": warnings}


def write_csv(dataset: Dataset, path: Path, columns: list[str] | None = None) -> dict[str, Any]:
    """Grava a tabela em CSV, com latitude e longitude à frente."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = dataset.df
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    lead = [c for c in (sch.LON, sch.LAT) if c in df.columns]
    ordered = lead + [c for c in df.columns if c not in lead]
    df[ordered].to_csv(path, index=False, encoding="utf-8-sig")
    return {"path": str(path), "rows": len(df), "columns": list(ordered)}


def write_geojson(features: dict[str, Any], path: Path) -> dict[str, Any]:
    """Grava uma ``FeatureCollection`` já montada."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(features, ensure_ascii=False), encoding="utf-8")
    return {"path": str(path), "features": len(features.get("features", []))}


# ==========================================================================
# Prescrição
# ==========================================================================

def features_to_geodataframe(features: dict[str, Any], rate_property: str = "dose"):
    """Converte a ``FeatureCollection`` de prescrição em ``GeoDataFrame``."""
    import geopandas as gpd
    from shapely.geometry import shape

    rows, geometries = [], []
    for feature in features.get("features", []):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        properties = dict(feature.get("properties") or {})
        if rate_property not in properties:
            continue
        geometries.append(shape(geometry))
        rows.append(properties)

    if not rows:
        raise ValueError(
            f"Nenhuma feição com a propriedade de dose '{rate_property}' foi encontrada."
        )
    return gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geometries, crs=WGS84)


def write_prescription_shapefile(
    features: dict[str, Any],
    path: Path,
    brand: str = "generic",
    rate_property: str = "dose",
    rate_unit: str = "kg/ha",
    product: str | None = None,
) -> dict[str, Any]:
    """Grava a prescrição como shapefile de polígonos, pronto para o monitor."""
    gdf = features_to_geodataframe(features, rate_property)
    field_name = RATE_FIELD_BY_BRAND.get(brand, "RATE")

    gdf[field_name] = pd.to_numeric(gdf[rate_property], errors="coerce").round(3)
    if "RATE" not in gdf.columns:
        gdf["RATE"] = gdf[field_name]
    gdf["UNIT"] = rate_unit[:10]
    if product:
        gdf["PRODUCT"] = str(product)[:32]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mapping, warnings = safe_field_names([c for c in gdf.columns if c != gdf.geometry.name])
    gdf.rename(columns=mapping).to_file(path, driver="ESRI Shapefile")

    return {
        "path": str(path),
        "features": len(gdf),
        "rate_field": field_name,
        "unit": rate_unit,
        "warnings": warnings,
        "sidecars": [str(path.with_suffix(ext)) for ext in (".shp", ".shx", ".dbf", ".prj")],
    }


def rasterize_prescription(
    features: dict[str, Any],
    rate_property: str = "dose",
    cell_m: float = 10.0,
) -> dict[str, Any]:
    """Converte polígonos de prescrição numa grade regular em graus.

    O ISOXML só aceita grade retangular alinhada aos eixos, em coordenadas
    geográficas. As células fora de qualquer polígono recebem zero, que é
    como o terminal entende "não aplicar aqui".
    """
    from shapely.geometry import box
    from shapely.strtree import STRtree

    gdf = features_to_geodataframe(features, rate_property)
    rates = pd.to_numeric(gdf[rate_property], errors="coerce").to_numpy(dtype="float64")

    min_lon, min_lat, max_lon, max_lat = gdf.total_bounds
    mid_lat = (min_lat + max_lat) / 2.0
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * np.cos(np.radians(mid_lat))

    cell_lat = cell_m / m_per_deg_lat
    cell_lon = cell_m / m_per_deg_lon

    cols = max(1, int(np.ceil((max_lon - min_lon) / cell_lon)))
    rows = max(1, int(np.ceil((max_lat - min_lat) / cell_lat)))

    # Uma grade absurda costuma ser célula pequena demais para o talhão.
    if rows * cols > 4_000_000:
        raise ValueError(
            f"A grade resultaria em {rows * cols:,} células. Aumente o tamanho da "
            "célula para gerar um arquivo que o terminal consiga carregar."
        )

    grid = np.zeros((rows, cols), dtype="float64")
    geometries = list(gdf.geometry)
    tree = STRtree(geometries)

    for row in range(rows):
        cell_min_lat = min_lat + row * cell_lat
        cell_max_lat = cell_min_lat + cell_lat
        for col in range(cols):
            cell_min_lon = min_lon + col * cell_lon
            cell = box(cell_min_lon, cell_min_lat, cell_min_lon + cell_lon, cell_max_lat)
            candidates = tree.query(cell)
            best_rate, best_area = 0.0, 0.0
            for index in candidates:
                intersection = geometries[index].intersection(cell)
                if intersection.is_empty:
                    continue
                area = intersection.area
                if area > best_area:
                    best_area, best_rate = area, float(rates[index])
            # Só aplica onde o polígono cobre a maior parte da célula.
            grid[row, col] = best_rate if best_area > cell.area * 0.5 else 0.0

    return {
        "grid": grid,
        "min_lon": float(min_lon),
        "min_lat": float(min_lat),
        "cell_lon": float(cell_lon),
        "cell_lat": float(cell_lat),
        "rows": rows,
        "cols": cols,
        "cell_m": cell_m,
        "cells_with_rate": int((grid > 0).sum()),
    }


def write_prescription_isoxml(
    features: dict[str, Any],
    out_dir: Path,
    rate_property: str = "dose",
    rate_kind: str = "mass",
    cell_m: float = 10.0,
    task_name: str = "Prescricao",
    field_name: str = "Talhao",
    product_name: str = "Produto",
) -> dict[str, Any]:
    """Grava a prescrição como pasta TASKDATA para terminal ISOBUS."""
    raster = rasterize_prescription(features, rate_property, cell_m)
    boundary = _outer_ring(features)

    taskdata_dir = isoxml_mod.write_prescription(
        out_dir,
        grid=raster["grid"],
        min_lon=raster["min_lon"],
        min_lat=raster["min_lat"],
        cell_lon=raster["cell_lon"],
        cell_lat=raster["cell_lat"],
        rate_kind=rate_kind,
        task_name=task_name,
        field_name=field_name,
        product_name=product_name,
        boundary=boundary,
    )
    return {
        "path": str(taskdata_dir),
        "rows": raster["rows"],
        "cols": raster["cols"],
        "cells_with_rate": raster["cells_with_rate"],
        "cell_m": cell_m,
        "ddi": f"{isoxml_mod.RX_DDI[rate_kind]:04X}",
        "unit": isoxml_mod.RX_DDI_LABELS[rate_kind],
    }


def _outer_ring(features: dict[str, Any]) -> list[tuple[float, float]] | None:
    """Contorno externo do conjunto de polígonos, para o cadastro do talhão."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    geometries = [
        shape(f["geometry"]) for f in features.get("features", []) if f.get("geometry")
    ]
    if not geometries:
        return None
    merged = unary_union(geometries)
    if merged.geom_type == "MultiPolygon":
        merged = max(merged.geoms, key=lambda g: g.area)
    if merged.geom_type != "Polygon":
        return None
    return [(float(x), float(y)) for x, y in merged.exterior.coords]


def write_prescription_csv(
    features: dict[str, Any],
    path: Path,
    rate_property: str = "dose",
) -> dict[str, Any]:
    """Grava a prescrição como CSV de centroides — o formato de recurso.

    Alguns controladores antigos não leem shapefile nem ISOXML, mas
    importam uma tabela de pontos com dose.
    """
    gdf = features_to_geodataframe(features, rate_property)
    centroids = gdf.geometry.representative_point()

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["longitude", "latitude", "rate", "area_ha"])
        for point, (_, row) in zip(centroids, gdf.iterrows()):
            writer.writerow([
                f"{point.x:.8f}", f"{point.y:.8f}",
                row.get(rate_property, ""), row.get("area_ha", ""),
            ])
    return {"path": str(path), "rows": len(gdf)}


def bundle(paths: list[Path], zip_path: Path) -> dict[str, Any]:
    """Empacota os arquivos gerados num ZIP para levar no pen drive."""
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    added: list[str] = []

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in paths:
            item = Path(item)
            if item.is_dir():
                for child in item.rglob("*"):
                    if child.is_file():
                        archive.write(child, child.relative_to(item.parent))
                        added.append(str(child.relative_to(item.parent)))
            elif item.exists():
                archive.write(item, item.name)
                added.append(item.name)
                # Shapefile é um conjunto: sem os acompanhantes ele não abre.
                if item.suffix.lower() == ".shp":
                    for ext in (".shx", ".dbf", ".prj", ".cpg"):
                        sidecar = item.with_suffix(ext)
                        if sidecar.exists():
                            archive.write(sidecar, sidecar.name)
                            added.append(sidecar.name)

    return {"path": str(zip_path), "entries": sorted(set(added))}


# ==========================================================================
# Pacote pronto para o monitor
# ==========================================================================

def build_package(
    out_dir: Path,
    monitor: str = "generic",
    prescription: dict[str, Any] | None = None,
    boundary: list[tuple[float, float]] | None = None,
    guidance_lines: list[dict[str, Any]] | None = None,
    dataset: Dataset | None = None,
    rate_property: str = "dose",
    rate_kind: str = "mass",
    rate_unit: str = "kg/ha",
    cell_m: float = 10.0,
    field_name: str = "Talhao",
    task_name: str = "Prescricao",
    product_name: str = "Produto",
    customer_name: str = "AgroSuite",
    farm_name: str = "Fazenda",
) -> dict[str, Any]:
    """Monta a pasta que vai para o pen drive, no arranjo que o monitor espera.

    Junta num só lugar tudo que a máquina precisa para executar o trabalho:
    contorno do talhão, linhas de orientação e prescrição. O que a plataforma
    escolhida não aceita é simplesmente omitido, e isso vai registrado no
    resultado — é melhor o usuário saber que o Ag Leader não recebeu as linhas
    AB do que descobrir na cabine.

    Returns
    -------
    dict
        ``{"folder", "contents", "skipped", "readme", "monitor"}``.
    """
    from . import packages as packages_mod

    profile = packages_mod.get_profile(monitor)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    contents: list[dict[str, Any]] = []
    skipped: list[str] = []
    rate_field: str | None = None

    def subfolder(fmt: str) -> Path:
        name = profile.layout.get(fmt, ".")
        target = out_dir if name == "." else out_dir / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ---------------------------------------------------------------- ISOXML
    wants_isoxml = any(
        profile.preferred.get(artifact) == "isoxml"
        for artifact in ("prescription", "boundary", "guidance")
        if artifact in profile.accepts
    )
    iso_boundary = boundary if "boundary" in profile.accepts else None
    iso_guidance = guidance_lines if "guidance" in profile.accepts else None
    iso_prescription = None

    if prescription and "prescription" in profile.accepts and wants_isoxml:
        raster = rasterize_prescription(prescription, rate_property, cell_m)
        iso_prescription = {
            "grid": raster["grid"],
            "min_lon": raster["min_lon"], "min_lat": raster["min_lat"],
            "cell_lon": raster["cell_lon"], "cell_lat": raster["cell_lat"],
            "rate_kind": rate_kind,
        }
        if iso_boundary is None:
            iso_boundary = _outer_ring(prescription)

    if wants_isoxml and (iso_boundary or iso_guidance or iso_prescription):
        taskdata = isoxml_mod.write_field_setup(
            subfolder("isoxml"),
            field_name=field_name,
            boundary=iso_boundary,
            guidance_lines=iso_guidance,
            customer_name=customer_name,
            farm_name=farm_name,
            prescription=iso_prescription,
            task_name=task_name,
            product_name=product_name,
        )
        parts = []
        if iso_boundary:
            parts.append("contorno")
        if iso_guidance:
            parts.append(f"{len(iso_guidance)} linha(s) AB")
        if iso_prescription:
            parts.append("prescrição em grade")
        contents.append({
            "artifact": "isoxml",
            "path": str(Path(taskdata).relative_to(out_dir)),
            "detail": ", ".join(parts),
            "absolute": str(taskdata),
        })

    # ------------------------------------------------------------ shapefile
    if prescription and "prescription" in profile.accepts:
        target = subfolder("shapefile")
        info = write_prescription_shapefile(
            prescription, target / f"{_slug(task_name)}.shp",
            brand=profile.key, rate_property=rate_property,
            rate_unit=rate_unit, product=product_name,
        )
        rate_field = info["rate_field"]
        contents.append({
            "artifact": "prescription",
            "path": str(Path(info["path"]).relative_to(out_dir)),
            "detail": f"{info['features']} polígonos, campo {rate_field}",
            "absolute": info["path"],
        })

    if boundary and "boundary" in profile.accepts and \
            profile.preferred.get("boundary") in ("shapefile", "geojson"):
        target = subfolder(profile.preferred["boundary"])
        collection = _boundary_collection(boundary, field_name)
        if profile.preferred["boundary"] == "geojson":
            path = target / f"{_slug(field_name)}_contorno.geojson"
            write_geojson(collection, path)
        else:
            path = target / f"{_slug(field_name)}_contorno.shp"
            _write_boundary_shapefile(collection, path)
        contents.append({
            "artifact": "boundary",
            "path": str(path.relative_to(out_dir)),
            "detail": "polígono do talhão",
            "absolute": str(path),
        })

    if prescription and profile.preferred.get("prescription") == "geojson":
        target = subfolder("geojson")
        path = target / f"{_slug(task_name)}.geojson"
        write_geojson(prescription, path)
        contents.append({
            "artifact": "prescription",
            "path": str(path.relative_to(out_dir)),
            "detail": f"{len(prescription.get('features', []))} feições",
            "absolute": str(path),
        })

    # ----------------------------------------------------------------- dados
    if dataset is not None and "data" in profile.accepts:
        target = subfolder("shapefile")
        path = target / f"{_slug(dataset.meta.name)}.shp"
        info = write_vector(dataset, path)
        contents.append({
            "artifact": "data",
            "path": str(path.relative_to(out_dir)),
            "detail": f"{info['features']} pontos",
            "absolute": info["path"],
        })

    # ----------------------------------------------------- o que ficou de fora
    for artifact, value in (("prescription", prescription), ("boundary", boundary),
                            ("guidance", guidance_lines), ("data", dataset)):
        if value is not None and artifact not in profile.accepts:
            skipped.append(
                f"{packages_mod.ARTIFACT_LABELS[artifact]}: o "
                f"{profile.label} não recebe este tipo de arquivo por esta via."
            )
    if guidance_lines and "guidance" in profile.accepts and not wants_isoxml:
        skipped.append(
            "Linhas de orientação: esta plataforma recebe linhas AB por ISOXML, "
            "que não faz parte do arranjo preferido dela. Gere o pacote ISOBUS "
            "genérico se precisar levar as linhas."
        )

    readme = packages_mod.write_readme(out_dir, profile, contents, rate_field, rate_unit)
    return {
        "folder": str(out_dir),
        "monitor": profile.key,
        "monitor_label": profile.label,
        "contents": contents,
        "skipped": skipped,
        "readme": str(readme),
        "instructions": list(profile.instructions),
    }


def _slug(text: str) -> str:
    """Nome de arquivo seguro: sem acento, espaço nem caractere especial."""
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(text))
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in ascii_only)
    return safe.strip("_") or "arquivo"


def _boundary_collection(ring: list[tuple[float, float]], name: str) -> dict[str, Any]:
    """Empacota um anel de contorno como ``FeatureCollection``."""
    closed = [list(p) for p in ring]
    if closed and closed[0] != closed[-1]:
        closed.append(closed[0])
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [closed]},
            "properties": {"NAME": name[:32], "TYPE": "boundary"},
        }],
    }


def _write_boundary_shapefile(collection: dict[str, Any], path: Path) -> None:
    import geopandas as gpd
    from shapely.geometry import shape

    rows, geometries = [], []
    for feature in collection["features"]:
        geometries.append(shape(feature["geometry"]))
        rows.append(feature.get("properties") or {})
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(pd.DataFrame(rows), geometry=geometries, crs=WGS84).to_file(
        path, driver="ESRI Shapefile"
    )
