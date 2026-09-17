"""Detecção de formato e despacho para o leitor correto.

O usuário joga no app o que tiver em mãos: um ``.shp`` solto, o ZIP inteiro
que o monitor gravou no pen drive, uma pasta ``TASKDATA``, um CSV de log ou
o GeoJSON do Augmenta. Este módulo decide o que é cada coisa e chama o
leitor apropriado, sem exigir que o usuário saiba o nome técnico do formato.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..core.dataset import Dataset
from . import isoxml as isoxml_mod
from . import johndeere as jd_mod
from . import readers

#: Extensões aceitas na importação, agrupadas por família.
VECTOR_EXT = {".shp", ".gpkg", ".geojson", ".json", ".kml", ".kmz", ".gml"}
TABULAR_EXT = {".csv", ".txt", ".dat", ".log", ".tsv"}
EXCEL_EXT = {".xlsx", ".xls", ".xlsm"}
ARCHIVE_EXT = {".zip"}

#: Extensões que compõem um shapefile — usadas ao extrair de um ZIP.
SHAPEFILE_SIDECARS = {".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".qix"}

ALL_IMPORT_EXT = VECTOR_EXT | TABULAR_EXT | EXCEL_EXT | ARCHIVE_EXT | {".xml", ".iso"}


@dataclass
class DetectedSource:
    """Resultado da inspeção de um caminho."""

    kind: str          # 'shapefile' | 'geojson' | 'csv' | 'excel' | 'kml' | 'isoxml' | 'archive'
    path: Path
    label: str
    detail: str = ""


def detect(path: str | Path) -> DetectedSource:
    """Classifica um caminho (arquivo, pasta ou ZIP) sem lê-lo por inteiro."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Caminho não encontrado: {path}")

    if path.is_dir():
        if isoxml_mod.find_taskdata(path):
            return DetectedSource("isoxml", path, "Pasta ISOXML (TASKDATA)")

        # O cartão John Deere vem antes da busca por shapefile solto: a árvore
        # GS2/GS3/JD-Data diz qual camada é contorno e qual é prescrição, o que
        # um .shp avulso na pasta não diria.
        card_root, generation = jd_mod.find_card_root(path)
        if card_root is not None:
            return DetectedSource(
                "jd_card", path, f"Cartão John Deere — {generation}",
                detail=f"raiz em {card_root.name}",
            )

        shapefiles = sorted(path.glob("*.shp"))
        if shapefiles:
            return DetectedSource(
                "shapefile", shapefiles[0], "Pasta com shapefile",
                detail=f"{len(shapefiles)} shapefile(s); usando {shapefiles[0].name}",
            )
        raise ValueError(
            f"A pasta '{path.name}' não contém TASKDATA.XML nem shapefile."
        )

    suffix = path.suffix.lower()
    if suffix in ARCHIVE_EXT:
        return DetectedSource("archive", path, "Arquivo compactado")
    if suffix == ".shp":
        return DetectedSource("shapefile", path, "Shapefile")
    if suffix in (".geojson", ".json"):
        return DetectedSource("geojson", path, "GeoJSON / JSON")
    if suffix in (".kml", ".kmz"):
        return DetectedSource("kml", path, "KML / KMZ")
    if suffix == ".gpkg":
        return DetectedSource("shapefile", path, "GeoPackage")
    if suffix in TABULAR_EXT:
        return DetectedSource("csv", path, "Tabela de texto (CSV/TXT)")
    if suffix in EXCEL_EXT:
        return DetectedSource("excel", path, "Planilha Excel")
    if suffix in (".xml", ".iso"):
        if path.name.upper() == "TASKDATA.XML":
            return DetectedSource("isoxml", path, "TASKDATA.XML (ISOXML)")
        found = isoxml_mod.find_taskdata(path.parent)
        if found:
            return DetectedSource("isoxml", found, "ISOXML na mesma pasta")
        raise ValueError(f"XML não reconhecido como ISOXML: {path.name}")

    raise ValueError(
        f"Extensão '{suffix or path.name}' não suportada na importação. "
        f"Formatos aceitos: {', '.join(sorted(ALL_IMPORT_EXT))}."
    )


def extract_archive(path: Path, workdir: Path | None = None) -> Path:
    """Descompacta um ZIP numa pasta temporária e devolve a raiz útil.

    Se o ZIP tiver uma única pasta na raiz (padrão das exportações de
    monitor), essa pasta é devolvida diretamente, para que a detecção
    seguinte veja o conteúdo e não o invólucro.
    """
    path = Path(path)
    target = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="agrosuite_zip_"))
    target.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            # Proteção contra caminhos absolutos ou com ".." dentro do ZIP.
            name = Path(member.filename)
            parts = [p for p in name.parts if p not in ("", ".", "..") and not p.startswith("/")]
            if not parts:
                continue
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out)

    entries = [p for p in target.iterdir() if not p.name.startswith("__MACOSX")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return target


def read_any(path: str | Path, brand_hint: str | None = None) -> Dataset:
    """Importa qualquer fonte suportada, resolvendo ZIP e pastas."""
    source = detect(path)

    if source.kind == "archive":
        extracted = extract_archive(source.path)
        inner = detect(extracted)
        if inner.kind == "archive":
            raise ValueError("ZIP aninhado não suportado; descompacte antes de importar.")
        dataset = _dispatch(inner, brand_hint)
        # O nome do ZIP é mais informativo que o do arquivo interno.
        dataset.meta.notes.append(f"Extraído de {Path(path).name}.")
        return dataset

    return _dispatch(source, brand_hint)


def _dispatch(source: DetectedSource, brand_hint: str | None) -> Dataset:
    readers_by_kind = {
        "shapefile": readers.read_shapefile,
        "geojson": readers.read_geojson,
        "csv": readers.read_tabular,
        "excel": readers.read_excel,
        "kml": readers.read_kml,
        "isoxml": isoxml_mod.read_isoxml,
        "jd_card": read_jd_card,
    }
    reader = readers_by_kind.get(source.kind)
    if reader is None:
        raise ValueError(f"Sem leitor para o tipo '{source.kind}'.")
    return reader(source.path, brand_hint)


def read_jd_card(path: Path, brand_hint: str | None = None) -> Dataset:
    """Importa a camada mais útil de um cartão John Deere.

    O cartão pode ter várias camadas; a prescrição e os dados de operação
    interessam mais que o contorno, então a escolha segue essa ordem. O
    inventário completo fica nos metadados, para a interface listar o resto.
    """
    inv = jd_mod.inventory(Path(path))
    layers = jd_mod.readable_layers(inv)

    if not layers:
        proprietary = ", ".join(sorted({e["kind"] for e in inv.proprietary}))
        raise ValueError(
            f"O cartão foi reconhecido ({inv.generation}), mas nenhum arquivo em "
            f"formato aberto foi encontrado dentro dele."
            + (f" O que existe é proprietário: {proprietary}." if proprietary else "")
            + " No SMS, reexporte escolhendo shapefile em vez de GreenStar."
        )

    order = {"data": 0, "prescription": 1, "boundary": 2, "guidance": 3}
    layers.sort(key=lambda layer: order.get(layer["role"], 9))
    chosen = layers[0]

    dataset = _dispatch(detect(Path(chosen["path"])), brand_hint or "john_deere")
    dataset.meta.brand = "john_deere"
    dataset.meta.brand_label = "John Deere"
    dataset.meta.notes.append(
        f"Importado de cartão {inv.generation}: {chosen['relative']}."
    )
    if len(layers) > 1:
        dataset.meta.notes.append(
            "Outras camadas no cartão: "
            + ", ".join(f"{layer['relative']} ({layer['role']})" for layer in layers[1:])
            + "."
        )
    dataset.meta.extra["jd_card"] = inv.to_dict()
    dataset.meta.extra["jd_card_layers"] = layers
    return dataset


def inspect(path: str | Path) -> dict:
    """Inspeção leve para a interface mostrar antes de importar de fato."""
    source = detect(path)
    info = {
        "kind": source.kind,
        "label": source.label,
        "detail": source.detail,
        "path": str(source.path),
        "name": Path(path).name,
    }
    if source.kind == "jd_card":
        inv = jd_mod.inventory(source.path)
        info["john_deere_card"] = inv.to_dict()
        info["layers"] = jd_mod.readable_layers(inv)
        info["detail"] = inv.summary()

    if source.kind == "isoxml":
        try:
            catalog = isoxml_mod.parse_taskdata(source.path)
            info["isoxml"] = {
                "version": catalog["version"],
                "software": catalog["software"],
                "fields": [f["name"] for f in catalog["fields"]],
                "tasks": [t["name"] for t in catalog["tasks"]],
                "products": list(catalog["products"].values()),
            }
        except Exception as exc:
            info["detail"] = f"TASKDATA.XML ilegível: {exc}"
    return info
