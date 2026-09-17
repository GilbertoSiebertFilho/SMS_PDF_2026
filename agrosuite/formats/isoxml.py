"""ISOXML — ISO 11783-10 (TASKDATA).

É o único formato verdadeiramente aberto e comum a Väderstad, Bourgault
(X30/X35), Case IH e New Holland ISOBUS, Topcon/Müller e boa parte dos
terminais Trimble. Uma pasta ISOXML tem esta forma::

    TASKDATA/
        TASKDATA.XML      cadastro (cliente, fazenda, talhão, produto, tarefa)
        TLG00001.XML      cabeçalho do log: quais campos existem no binário
        TLG00001.BIN      registros do log, em binário little-endian
        GRD00001.BIN      grade de prescrição, em binário

Este módulo faz as duas pontas:

* **Leitura** do cadastro, dos contornos de talhão e dos logs TLG — o
  cabeçalho TLG diz quais campos estão presentes, e o binário é
  desempacotado conforme essa declaração.
* **Escrita** de prescrição como tarefa com grade do tipo 2, que é o que os
  terminais consomem para taxa variável.

Convenção de sinal do padrão: latitude = *north*, longitude = *east*, ambas
em graus decimais; no binário vêm como inteiros de 32 bits em 1e-7 grau.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..core import schema as sch
from ..core.dataset import Dataset, DatasetMeta

#: Época das datas do padrão: dias contados a partir de 01/01/1980.
ISO_EPOCH = date(1980, 1, 1)

#: Escala das coordenadas inteiras do log binário.
COORD_SCALE = 1e-7


@dataclass(frozen=True)
class DDI:
    """Entrada do dicionário de dados do ISOBUS."""

    code: int
    label: str
    iso_unit: str
    #: Fator que leva o inteiro do padrão à unidade interna do AgroSuite.
    to_internal: float
    internal_unit: str
    canonical: str | None = None


#: Dicionário parcial de DDIs — cobre as grandezas usadas em taxa variável,
#: colheita e telemetria de deslocamento. DDIs fora desta tabela são
#: preservados como colunas ``ddi_<código>`` com o valor bruto, para que
#: nenhum dado do log se perca silenciosamente.
DDI_TABLE: dict[int, DDI] = {
    0x0001: DDI(0x0001, "Dose alvo (volume por área)", "mm³/m²", 0.01, "L/ha", sch.TARGET_RATE),
    0x0002: DDI(0x0002, "Dose aplicada (volume por área)", "mm³/m²", 0.01, "L/ha", sch.APPLIED_RATE),
    0x0003: DDI(0x0003, "Dose alvo (massa por tempo)", "mg/s", 1e-6, "kg/s", None),
    0x0004: DDI(0x0004, "Dose aplicada (massa por tempo)", "mg/s", 1e-6, "kg/s", sch.FLOW),
    0x0005: DDI(0x0005, "Dose alvo (massa por área)", "mg/m²", 0.01, "kg/ha", sch.TARGET_RATE),
    0x0006: DDI(0x0006, "Dose alvo (massa por área)", "mg/m²", 0.01, "kg/ha", sch.TARGET_RATE),
    0x0007: DDI(0x0007, "Dose aplicada (massa por área)", "mg/m²", 0.01, "kg/ha", sch.APPLIED_RATE),
    0x000A: DDI(0x000A, "Dose alvo (contagem por área)", "1/m²", 10.0, "sementes/ha", sch.TARGET_RATE),
    0x000B: DDI(0x000B, "Dose aplicada (contagem por área)", "1/m²", 10.0, "sementes/ha", sch.APPLIED_RATE),
    0x0043: DDI(0x0043, "Velocidade real do solo", "mm/s", 0.0036, "km/h", sch.SPEED),
    0x0046: DDI(0x0046, "Velocidade GNSS", "mm/s", 0.0036, "km/h", sch.SPEED),
    0x0048: DDI(0x0048, "Estado de trabalho", "-", 1.0, "-", None),
    0x0049: DDI(0x0049, "Largura de trabalho efetiva", "mm", 0.001, "m", sch.SWATH),
    0x0074: DDI(0x0074, "Distância percorrida (trabalho)", "mm", 0.001, "m", sch.DISTANCE),
    0x0075: DDI(0x0075, "Distância percorrida (total)", "mm", 0.001, "m", None),
    0x0077: DDI(0x0077, "Área trabalhada", "mm²", 1e-10, "ha", None),
    0x0082: DDI(0x0082, "Rendimento (massa por área)", "mg/m²", 0.01, "kg/ha", sch.VALUE),
    0x0083: DDI(0x0083, "Umidade do grão", "ppm", 1e-4, "%", sch.MOISTURE),
    0x0084: DDI(0x0084, "Massa colhida acumulada", "g", 0.001, "kg", None),
    0x0090: DDI(0x0090, "Largura de seção", "mm", 0.001, "m", None),
}

#: DDIs preferidos ao escrever prescrição, por tipo de insumo.
RX_DDI = {
    "mass": 0x0006,    # kg/ha  -> mg/m²
    "volume": 0x0001,  # L/ha   -> mm³/m²
    "count": 0x000A,   # sem/ha -> 1/m²
}

RX_DDI_LABELS = {
    "mass": "Massa por área (kg/ha) — fertilizante sólido, calcário",
    "volume": "Volume por área (L/ha) — calda, fertilizante líquido",
    "count": "Contagem por área (sementes/ha) — semeadura",
}


def ddi_scale_to_iso(kind: str) -> float:
    """Fator que leva a unidade interna ao inteiro do padrão."""
    return {"mass": 100.0, "volume": 100.0, "count": 0.1}[kind]


# ==========================================================================
# Leitura
# ==========================================================================

def find_taskdata(root: Path) -> Path | None:
    """Localiza o TASKDATA.XML dentro de uma pasta (busca insensível a caixa)."""
    root = Path(root)
    if root.is_file() and root.name.upper() == "TASKDATA.XML":
        return root
    if root.is_dir():
        for candidate in root.rglob("*"):
            if candidate.is_file() and candidate.name.upper() == "TASKDATA.XML":
                return candidate
    return None


def _text(element: ET.Element | None, attr: str) -> str | None:
    if element is None:
        return None
    value = element.get(attr)
    return value if value not in (None, "") else None


def parse_taskdata(taskdata_path: Path) -> dict[str, Any]:
    """Lê o cadastro do TASKDATA.XML (clientes, fazendas, talhões, tarefas)."""
    tree = ET.parse(taskdata_path)
    root = tree.getroot()

    customers = {c.get("A"): c.get("B") for c in root.iter("CTR")}
    farms = {f.get("A"): f.get("B") for f in root.iter("FRM")}
    products = {p.get("A"): p.get("B") for p in root.iter("PDT")}

    fields: list[dict[str, Any]] = []
    for pfd in root.iter("PFD"):
        polygons = []
        for pln in pfd.iter("PLN"):
            for lsg in pln.iter("LSG"):
                ring = [
                    (float(pnt.get("D")), float(pnt.get("C")))  # (lon, lat)
                    for pnt in lsg.iter("PNT")
                    if pnt.get("C") and pnt.get("D")
                ]
                if len(ring) >= 3:
                    polygons.append(ring)
        fields.append({
            "id": pfd.get("A"),
            "name": pfd.get("C") or pfd.get("B"),
            "area_m2": float(pfd.get("D")) if pfd.get("D") else None,
            "customer": customers.get(pfd.get("E")),
            "farm": farms.get(pfd.get("F")),
            "polygons": polygons,
        })

    tasks: list[dict[str, Any]] = []
    for tsk in root.iter("TSK"):
        logs = [tlg.get("A") for tlg in tsk.iter("TLG") if tlg.get("A")]
        grids = [
            {
                "min_north": float(grd.get("A")),
                "min_east": float(grd.get("B")),
                "cell_north": float(grd.get("C")),
                "cell_east": float(grd.get("D")),
                "max_col": int(grd.get("E")),
                "max_row": int(grd.get("F")),
                "filename": grd.get("G"),
                "grid_type": int(grd.get("I") or 2),
            }
            for grd in tsk.iter("GRD")
            if grd.get("A") and grd.get("G")
        ]
        tasks.append({
            "id": tsk.get("A"),
            "name": tsk.get("B"),
            "customer": customers.get(tsk.get("C")),
            "farm": farms.get(tsk.get("D")),
            "field": tsk.get("E"),
            "logs": logs,
            "grids": grids,
        })

    return {
        "version": f"{root.get('VersionMajor', '?')}.{root.get('VersionMinor', '?')}",
        "software": root.get("ManagementSoftwareManufacturer"),
        "customers": customers,
        "farms": farms,
        "products": products,
        "fields": fields,
        "tasks": tasks,
    }


def _tlg_layout(header_path: Path) -> dict[str, Any]:
    """Interpreta o cabeçalho TLGxxxxx.XML.

    No padrão, um atributo **presente e vazio** significa "este campo é
    gravado a cada registro do binário"; um atributo ausente significa que o
    campo não é registrado. É essa distinção que define o layout dos bytes.
    """
    root = ET.parse(header_path).getroot()
    tim = root if root.tag == "TIM" else root.find("TIM")
    if tim is None:
        raise ValueError(f"{header_path.name}: elemento TIM ausente.")

    fields: list[tuple[str, str, int]] = []  # (nome, formato struct, bytes)
    if "A" in tim.attrib and tim.get("A") == "":
        fields.append(("time_ms", "<I", 4))
        fields.append(("date_days", "<H", 2))

    ptn = tim.find("PTN")
    if ptn is not None:
        optional = [
            ("A", "north", "<i", 4), ("B", "east", "<i", 4), ("C", "up", "<i", 4),
            ("D", "pos_status", "<B", 1), ("E", "pdop", "<H", 2), ("F", "hdop", "<H", 2),
            ("G", "n_sats", "<B", 1), ("H", "gps_utc_time", "<I", 4),
            ("I", "gps_utc_date", "<H", 2),
        ]
        for attr, name, fmt, size in optional:
            if attr in ptn.attrib and ptn.get(attr) == "":
                fields.append((name, fmt, size))

    dlvs = []
    for index, dlv in enumerate(tim.findall("DLV")):
        raw_ddi = dlv.get("A") or "0"
        try:
            code = int(raw_ddi, 16)
        except ValueError:
            code = 0
        dlvs.append({
            "index": index,
            "ddi": code,
            "device_element": dlv.get("C"),
        })

    return {"fields": fields, "dlvs": dlvs}


def read_tlg(header_path: Path) -> pd.DataFrame:
    """Lê um par TLGxxxxx.XML + .BIN e devolve os registros como tabela."""
    header_path = Path(header_path)
    bin_path = header_path.with_suffix(".BIN")
    if not bin_path.exists():
        bin_path = header_path.with_suffix(".bin")
    if not bin_path.exists():
        raise FileNotFoundError(f"Binário do log ausente para {header_path.name}.")

    layout = _tlg_layout(header_path)
    fields = layout["fields"]
    dlv_meta = {d["index"]: d for d in layout["dlvs"]}
    data = bin_path.read_bytes()

    records: list[dict[str, Any]] = []
    offset = 0
    total = len(data)
    fixed_size = sum(size for _, _, size in fields)

    while offset + fixed_size + 1 <= total:
        record: dict[str, Any] = {}
        for name, fmt, size in fields:
            record[name] = struct.unpack_from(fmt, data, offset)[0]
            offset += size

        count = data[offset]
        offset += 1
        # Um contador absurdo indica dessincronização: melhor parar do que
        # produzir milhares de linhas de lixo.
        if count > 64 or offset + count * 5 > total:
            break
        for _ in range(count):
            index = data[offset]
            value = struct.unpack_from("<i", data, offset + 1)[0]
            offset += 5
            meta = dlv_meta.get(index)
            ddi_code = meta["ddi"] if meta else index
            record[f"__dlv_{ddi_code}"] = value
        records.append(record)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    if "north" in df.columns and "east" in df.columns:
        df[sch.LAT] = df["north"] * COORD_SCALE
        df[sch.LON] = df["east"] * COORD_SCALE
        df = df.drop(columns=["north", "east"])
    if "up" in df.columns:
        df[sch.ELEVATION] = df["up"] / 1000.0
        df = df.drop(columns=["up"])

    if "date_days" in df.columns and "time_ms" in df.columns:
        base = pd.to_datetime(ISO_EPOCH) + pd.to_timedelta(df["date_days"], unit="D")
        df[sch.TIMESTAMP] = base + pd.to_timedelta(df["time_ms"], unit="ms")
        df = df.drop(columns=["date_days", "time_ms"])

    # Converte cada DLV para a unidade interna, ou preserva o valor bruto.
    for column in [c for c in df.columns if c.startswith("__dlv_")]:
        code = int(column.removeprefix("__dlv_"))
        entry = DDI_TABLE.get(code)
        if entry is None:
            df = df.rename(columns={column: f"ddi_{code:04X}"})
            continue
        converted = df[column] * entry.to_internal
        target = entry.canonical or f"ddi_{code:04X}"
        if target in df.columns:
            target = f"ddi_{code:04X}"
        df[target] = converted
        df = df.drop(columns=[column])

    return df


def read_grid(base_dir: Path, grid_info: dict, rate_kind: str = "mass") -> pd.DataFrame:
    """Lê uma grade de prescrição (tipo 2) como tabela de células.

    O binário guarda ``max_col × max_row`` inteiros de 32 bits sem sinal, em
    varredura por linhas a partir do canto sudoeste. Cada célula vira uma
    linha com o centro em coordenadas geográficas e a dose já convertida
    para a unidade interna.
    """
    base_dir = Path(base_dir)
    name = grid_info["filename"]
    candidates = [
        base_dir / f"{name}.BIN", base_dir / f"{name}.bin", base_dir / name,
    ]
    grid_path = next((c for c in candidates if c.exists()), None)
    if grid_path is None:
        raise FileNotFoundError(f"Binário da grade {name} não encontrado.")

    rows, cols = grid_info["max_row"], grid_info["max_col"]
    if grid_info.get("grid_type", 2) == 1:
        raw = np.frombuffer(grid_path.read_bytes(), dtype="<u1")
        scale = 1.0  # código de zona de tratamento, não é dose
    else:
        raw = np.frombuffer(grid_path.read_bytes(), dtype="<u4")
        scale = 1.0 / ddi_scale_to_iso(rate_kind)

    expected = rows * cols
    if raw.size < expected:
        raise ValueError(
            f"Grade {name}: {raw.size} células no binário para {expected} declaradas."
        )
    values = raw[:expected].reshape(rows, cols).astype("float64") * scale

    row_idx, col_idx = np.meshgrid(np.arange(rows), np.arange(cols), indexing="ij")
    lat = grid_info["min_north"] + (row_idx + 0.5) * grid_info["cell_north"]
    lon = grid_info["min_east"] + (col_idx + 0.5) * grid_info["cell_east"]

    return pd.DataFrame({
        sch.LON: lon.ravel(),
        sch.LAT: lat.ravel(),
        sch.TARGET_RATE: values.ravel(),
        sch.VALUE: values.ravel(),
        "grid_row": row_idx.ravel(),
        "grid_col": col_idx.ravel(),
    })


def read_isoxml(path: Path, brand_hint: str | None = None) -> Dataset:
    """Importa uma pasta (ou TASKDATA.XML) ISOXML como dataset."""
    path = Path(path)
    taskdata = find_taskdata(path)
    if taskdata is None:
        raise ValueError("Nenhum TASKDATA.XML encontrado na pasta informada.")

    catalog = parse_taskdata(taskdata)
    base = taskdata.parent
    notes = [f"ISOXML versão {catalog['version']}."]
    if catalog.get("software"):
        notes.append(f"Gerado por: {catalog['software']}.")

    frames: list[pd.DataFrame] = []
    for task in catalog["tasks"]:
        for log_name in task["logs"]:
            header = next(
                (p for p in base.iterdir()
                 if p.is_file() and p.stem.upper() == log_name.upper()
                 and p.suffix.upper() == ".XML"),
                None,
            )
            if header is None:
                continue
            try:
                frame = read_tlg(header)
            except Exception as exc:  # log corrompido não invalida os demais
                notes.append(f"Log {log_name} ignorado: {exc}")
                continue
            if frame.empty:
                continue
            frame["task"] = task["name"] or task["id"]
            frames.append(frame)

    grid_frames: list[pd.DataFrame] = []
    if not frames:
        for task in catalog["tasks"]:
            for grid_info in task["grids"]:
                try:
                    grid_frame = read_grid(base, grid_info)
                except Exception as exc:
                    notes.append(f"Grade {grid_info['filename']} ignorada: {exc}")
                    continue
                grid_frame["task"] = task["name"] or task["id"]
                grid_frames.append(grid_frame)

    if frames:
        df = pd.concat(frames, ignore_index=True)
        geometry = None
        operation = "harvest" if sch.VALUE in df.columns else "application"
    elif grid_frames:
        df = pd.concat(grid_frames, ignore_index=True)
        geometry = None
        operation = "prescription"
        notes.append("Nenhum log TLG; importadas as grades de prescrição da tarefa.")
    else:
        # Sem logs: importa os contornos de talhão do cadastro.
        from shapely.geometry import Polygon

        rows, geoms = [], []
        for fld in catalog["fields"]:
            for ring in fld["polygons"]:
                poly = Polygon(ring)
                if not poly.is_valid or poly.is_empty:
                    continue
                point = poly.representative_point()
                rows.append({
                    "field_name": fld["name"],
                    "area_ha": (fld["area_m2"] or 0) / 10_000.0,
                    "farm": fld["farm"],
                    "customer": fld["customer"],
                    sch.LON: point.x,
                    sch.LAT: point.y,
                })
                geoms.append(poly)
        if not rows:
            raise ValueError("ISOXML sem logs e sem contornos de talhão utilizáveis.")
        df = pd.DataFrame(rows)
        geometry = geoms
        operation = "boundary"
        notes.append("Nenhum log TLG legível; importados os contornos do cadastro.")

    if sch.VALUE not in df.columns:
        for candidate in (sch.APPLIED_RATE, sch.TARGET_RATE):
            if candidate in df.columns:
                df[sch.VALUE] = df[candidate]
                notes.append(f"Variável principal: {sch.LABELS[candidate]}.")
                break

    from . import brands as brands_mod

    brand = brands_mod.get_brand(brand_hint or "isoxml")
    first_field = catalog["fields"][0]["name"] if catalog["fields"] else None
    meta = DatasetMeta(
        name=first_field or taskdata.parent.name,
        source_path=str(taskdata),
        source_format="isoxml",
        brand=brand.key,
        brand_label=brand.label,
        operation=operation,
        field_name=first_field,
        geometry_type="polygon" if geometry else "point",
        notes=notes,
        extra={"isoxml_catalog": {
            "version": catalog["version"],
            "software": catalog["software"],
            "fields": [f["name"] for f in catalog["fields"]],
            "tasks": [t["name"] for t in catalog["tasks"]],
            "products": list(catalog["products"].values()),
        }},
    )
    return Dataset(df, meta, geometry=geometry)


# ==========================================================================
# Escrita de prescrição
# ==========================================================================

def write_prescription(
    out_dir: Path,
    grid: np.ndarray,
    min_lon: float,
    min_lat: float,
    cell_lon: float,
    cell_lat: float,
    rate_kind: str = "mass",
    task_name: str = "Prescricao",
    field_name: str = "Talhao",
    product_name: str = "Produto",
    customer_name: str = "AgroSuite",
    farm_name: str = "Fazenda",
    boundary: list[tuple[float, float]] | None = None,
) -> Path:
    """Escreve uma pasta TASKDATA com prescrição em grade do tipo 2.

    Parameters
    ----------
    grid:
        Matriz ``(linhas, colunas)`` com as doses na unidade interna
        (kg/ha, L/ha ou sementes/ha conforme ``rate_kind``). Linha 0 é a
        **mais ao sul**, coluna 0 a mais a oeste — a ordem que o padrão
        espera no binário. Células sem dose devem ser ``NaN`` ou 0.
    min_lon, min_lat:
        Canto sudoeste da grade, em graus decimais.
    cell_lon, cell_lat:
        Tamanho da célula em graus.
    rate_kind:
        ``"mass"``, ``"volume"`` ou ``"count"``.

    Returns
    -------
    Path
        A pasta ``TASKDATA`` criada.
    """
    out_dir = Path(out_dir)
    taskdata_dir = out_dir / "TASKDATA"
    taskdata_dir.mkdir(parents=True, exist_ok=True)

    rows, cols = grid.shape
    scale = ddi_scale_to_iso(rate_kind)
    values = np.nan_to_num(np.asarray(grid, dtype="float64"), nan=0.0)
    iso_values = np.rint(np.clip(values * scale, 0, 2**31 - 1)).astype("<u4")

    grid_name = "GRD00001"
    grid_path = taskdata_dir / f"{grid_name}.BIN"
    grid_path.write_bytes(iso_values.tobytes(order="C"))

    ddi = RX_DDI[rate_kind]
    root = ET.Element("ISO11783_TaskData", {
        "VersionMajor": "4",
        "VersionMinor": "0",
        "ManagementSoftwareManufacturer": "AgroSuite",
        "ManagementSoftwareVersion": "1.0",
        "DataTransferOrigin": "1",
    })
    ET.SubElement(root, "CTR", {"A": "CTR1", "B": customer_name})
    ET.SubElement(root, "FRM", {"A": "FRM1", "B": farm_name, "I": "CTR1"})

    pfd = ET.SubElement(root, "PFD", {
        "A": "PFD1", "C": field_name, "E": "CTR1", "F": "FRM1",
    })
    if boundary and len(boundary) >= 3:
        pln = ET.SubElement(pfd, "PLN", {"A": "1"})
        lsg = ET.SubElement(pln, "LSG", {"A": "1"})
        for order, (lon, lat) in enumerate(boundary, start=1):
            ET.SubElement(lsg, "PNT", {
                "A": "2", "C": f"{lat:.9f}", "D": f"{lon:.9f}", "I": str(order),
            })

    ET.SubElement(root, "PDT", {"A": "PDT1", "B": product_name})

    tsk = ET.SubElement(root, "TSK", {
        "A": "TSK1", "B": task_name, "C": "CTR1", "D": "FRM1", "E": "PFD1", "G": "1",
    })
    tzn = ET.SubElement(tsk, "TZN", {"A": "0"})
    ET.SubElement(tzn, "PDV", {"A": f"{ddi:04X}", "B": "0", "C": "PDT1"})
    ET.SubElement(tsk, "GRD", {
        "A": f"{min_lat:.9f}",
        "B": f"{min_lon:.9f}",
        "C": f"{cell_lat:.9f}",
        "D": f"{cell_lon:.9f}",
        "E": str(cols),
        "F": str(rows),
        "G": grid_name,
        "H": str(grid_path.stat().st_size),
        "I": "2",
    })

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(
        taskdata_dir / "TASKDATA.XML", encoding="UTF-8", xml_declaration=True
    )
    return taskdata_dir
