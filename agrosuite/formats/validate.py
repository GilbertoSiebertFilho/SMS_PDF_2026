"""Verificação dos arquivos antes de levá-los ao monitor.

Descobrir que o mapa não abre acontece no pior lugar possível: com a máquina
ligada, o operador esperando e o pen drive na mão. Este módulo confere o que
dá para conferir no computador — as causas conhecidas de recusa, uma a uma —
e devolve um laudo em três níveis:

``ok``
    O item foi conferido e está conforme.
``atencao``
    Funciona na maioria dos casos, mas depende de firmware ou de configuração
    do display; vale confirmar na tela.
``falha``
    O monitor vai recusar o arquivo. Não leve assim.

O que **não** dá para garantir daqui: versão de firmware, menu de importação
e formatos proprietários. Por isso nenhuma verificação afirma "vai funcionar":
ela afirma que a causa conhecida de falha foi eliminada.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np

#: Extensões que precisam acompanhar um .shp para o monitor abri-lo.
REQUIRED_SIDECARS = (".shx", ".dbf", ".prj")

#: Limite de caracteres de um nome de campo no DBF.
DBF_FIELD_LIMIT = 10

#: Acima disto, muitos terminais demoram demais ou recusam o carregamento.
MAX_RX_FEATURES = 20_000
MAX_GRID_CELLS = 2_000_000
MAX_FILE_MB = 32


@dataclass
class Check:
    """Um item verificado."""

    item: str
    status: str          # 'ok' | 'atencao' | 'falha'
    message: str
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(item, message):
    return Check(item, "ok", message)


def _warn(item, message, fix=""):
    return Check(item, "atencao", message, fix)


def _fail(item, message, fix=""):
    return Check(item, "falha", message, fix)


# ==========================================================================
# Shapefile
# ==========================================================================

def validate_shapefile(
    shp_path: Path,
    rate_field: str | None = None,
    expect_polygons: bool = True,
) -> list[Check]:
    """Confere um shapefile contra o que os monitores exigem."""
    import geopandas as gpd

    shp_path = Path(shp_path)
    checks: list[Check] = []

    if not shp_path.exists():
        return [_fail("Arquivo", f"{shp_path.name} não existe.")]

    # --- acompanhantes
    missing = [ext for ext in REQUIRED_SIDECARS if not shp_path.with_suffix(ext).exists()]
    if missing:
        checks.append(_fail(
            "Arquivos do conjunto",
            f"Faltam {', '.join(missing)} ao lado de {shp_path.name}.",
            "Copie sempre .shp, .shx, .dbf e .prj juntos — o monitor não abre sem os quatro.",
        ))
    else:
        checks.append(_ok("Arquivos do conjunto", ".shp, .shx, .dbf e .prj presentes."))

    try:
        gdf = gpd.read_file(shp_path)
    except Exception as exc:
        return checks + [_fail("Leitura", f"O arquivo não pôde ser lido de volta: {exc}")]

    # --- geometria
    if gdf.empty:
        checks.append(_fail("Feições", "O shapefile não tem nenhuma feição.",
                            "Verifique os parâmetros que geraram o mapa."))
        return checks

    geom_types = set(gdf.geom_type.dropna().unique())
    if expect_polygons:
        if geom_types <= {"Polygon", "MultiPolygon"}:
            checks.append(_ok("Tipo de geometria",
                              f"{len(gdf)} polígono(s) — é o que o monitor espera numa prescrição."))
        else:
            checks.append(_fail(
                "Tipo de geometria",
                f"Geometria é {', '.join(sorted(geom_types))}; prescrição precisa ser polígono.",
                "Gere a prescrição a partir do ensaio desenhado ou de uma grade, não de pontos.",
            ))

    invalid = int((~gdf.geometry.is_valid).sum())
    if invalid:
        checks.append(_warn(
            "Validade dos polígonos",
            f"{invalid} polígono(s) com geometria inválida (auto-interseção ou anel aberto).",
            "Alguns monitores ignoram a feição inválida; outros recusam o arquivo inteiro.",
        ))
    else:
        checks.append(_ok("Validade dos polígonos", "Todas as geometrias são válidas."))

    empty = int(gdf.geometry.is_empty.sum())
    if empty:
        checks.append(_fail("Geometrias vazias", f"{empty} feição(ões) sem geometria.",
                            "Remova-as antes de exportar."))

    if len(gdf) > MAX_RX_FEATURES:
        checks.append(_warn(
            "Quantidade de feições",
            f"{len(gdf):,} polígonos. Acima de ~{MAX_RX_FEATURES:,} muitos terminais "
            "demoram minutos para carregar, ou desistem.".replace(",", "."),
            "Aumente o tamanho da célula ou simplifique as zonas.",
        ))
    else:
        checks.append(_ok("Quantidade de feições", f"{len(gdf)} polígono(s)."))

    # --- projeção
    if gdf.crs is None:
        checks.append(_fail("Projeção", "Sem .prj — o monitor não sabe onde o mapa fica.",
                            "Exporte novamente pelo AgroSuite, que sempre grava o .prj."))
    elif gdf.crs.to_epsg() == 4326:
        checks.append(_ok("Projeção", "WGS84 geográfico (EPSG:4326), aceito por todos os monitores."))
    else:
        checks.append(_warn(
            "Projeção",
            f"CRS {gdf.crs.to_string()} em vez de WGS84. Displays antigos costumam "
            "assumir WGS84 e deslocam o mapa.",
            "Reexporte em EPSG:4326 se o mapa aparecer fora de lugar.",
        ))

    # --- coordenadas plausíveis
    bounds = gdf.total_bounds
    if gdf.crs is not None and gdf.crs.to_epsg() == 4326:
        if not (-180 <= bounds[0] <= 180 and -90 <= bounds[1] <= 90
                and -180 <= bounds[2] <= 180 and -90 <= bounds[3] <= 90):
            checks.append(_fail("Coordenadas", "Valores fora da faixa de latitude/longitude."))
        else:
            checks.append(_ok(
                "Localização",
                f"Entre {bounds[1]:.4f}, {bounds[0]:.4f} e {bounds[3]:.4f}, {bounds[2]:.4f}.",
            ))

    # --- nomes de campo
    attributes = [c for c in gdf.columns if c != gdf.geometry.name]
    too_long = [c for c in attributes if len(c) > DBF_FIELD_LIMIT]
    if too_long:
        checks.append(_fail(
            "Nomes de campo",
            f"Campos acima de {DBF_FIELD_LIMIT} caracteres: {', '.join(too_long)}.",
            "O DBF trunca sem avisar e pode colidir dois campos num só.",
        ))
    else:
        checks.append(_ok("Nomes de campo", f"{len(attributes)} campo(s) dentro do limite do DBF."))

    # --- campo de dose
    if rate_field:
        if rate_field not in gdf.columns:
            checks.append(_fail(
                "Campo de dose",
                f"O campo '{rate_field}' não existe no arquivo. "
                f"Campos disponíveis: {', '.join(attributes)}.",
                "Sem ele o monitor não encontra a dose na hora de importar.",
            ))
        else:
            series = gdf[rate_field]
            if not np.issubdtype(series.dtype, np.number):
                checks.append(_fail(
                    "Campo de dose",
                    f"'{rate_field}' é do tipo {series.dtype}, e precisa ser numérico.",
                ))
            else:
                values = series.to_numpy(dtype="float64")
                nulls = int(np.isnan(values).sum())
                negatives = int((values < 0).sum())
                zeros = int((values == 0).sum())
                if nulls:
                    checks.append(_fail(
                        "Doses nulas", f"{nulls} polígono(s) sem valor de dose.",
                        "Preencha com zero explícito onde não se quer aplicar.",
                    ))
                if negatives:
                    checks.append(_fail("Doses negativas", f"{negatives} polígono(s) com dose negativa."))
                if not nulls and not negatives:
                    detail = (f"{np.nanmin(values):.4g} a {np.nanmax(values):.4g}"
                              f" em {len(values)} polígonos")
                    if zeros:
                        detail += f"; {zeros} com dose zero (área sem aplicação)"
                    checks.append(_ok("Campo de dose", f"'{rate_field}': {detail}."))
                if np.nanmax(values) > 1e6:
                    checks.append(_warn(
                        "Magnitude da dose",
                        f"Dose máxima de {np.nanmax(values):.4g} — valor alto demais para a "
                        "maioria dos insumos. Confira se a unidade está certa.",
                    ))

    # --- acentuação no DBF
    text_columns = [c for c in attributes if gdf[c].dtype == object]
    has_accents = any(
        isinstance(v, str) and any(ord(ch) > 127 for ch in v)
        for c in text_columns for v in gdf[c].dropna().head(200)
    )
    cpg = shp_path.with_suffix(".cpg")
    if has_accents and not cpg.exists():
        checks.append(_warn(
            "Acentuação",
            "Há texto com acento e não existe .cpg declarando a codificação.",
            "Displays antigos mostram caracteres trocados; evite acento nos nomes.",
        ))
    elif has_accents:
        checks.append(_ok("Acentuação", f"Texto acentuado com codificação declarada em {cpg.name}."))

    # --- tamanho
    total_mb = sum(
        shp_path.with_suffix(ext).stat().st_size
        for ext in (".shp", ".shx", ".dbf", ".prj")
        if shp_path.with_suffix(ext).exists()
    ) / 1e6
    if total_mb > MAX_FILE_MB:
        checks.append(_warn(
            "Tamanho", f"{total_mb:.1f} MB no conjunto. Terminais antigos travam acima de "
            f"~{MAX_FILE_MB} MB.",
            "Reduza o número de polígonos ou aumente a célula.",
        ))
    else:
        checks.append(_ok("Tamanho", f"{total_mb:.2f} MB no conjunto."))

    return checks


# ==========================================================================
# ISOXML
# ==========================================================================

def validate_taskdata(taskdata_dir: Path) -> list[Check]:
    """Confere uma pasta TASKDATA contra o ISO 11783-10."""
    taskdata_dir = Path(taskdata_dir)
    checks: list[Check] = []

    if taskdata_dir.name.upper() != "TASKDATA":
        checks.append(_fail(
            "Nome da pasta",
            f"A pasta se chama '{taskdata_dir.name}' e precisa se chamar TASKDATA.",
            "O terminal procura exatamente esse nome na raiz do pen drive.",
        ))
    else:
        checks.append(_ok("Nome da pasta", "TASKDATA, como o padrão exige."))

    xml_path = next(
        (p for p in taskdata_dir.iterdir() if p.name.upper() == "TASKDATA.XML"), None
    )
    if xml_path is None:
        return checks + [_fail("TASKDATA.XML", "Arquivo principal ausente.")]
    if xml_path.name != "TASKDATA.XML":
        checks.append(_warn(
            "TASKDATA.XML",
            f"O arquivo está como '{xml_path.name}'. Alguns terminais só reconhecem "
            "o nome todo em maiúsculas.",
            "Renomeie para TASKDATA.XML.",
        ))

    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        return checks + [_fail("TASKDATA.XML", f"XML inválido: {exc}")]

    if root.tag != "ISO11783_TaskData":
        checks.append(_fail("Elemento raiz",
                            f"Raiz é '{root.tag}' em vez de ISO11783_TaskData."))
    else:
        version = f"{root.get('VersionMajor', '?')}.{root.get('VersionMinor', '?')}"
        checks.append(_ok("Estrutura", f"ISO 11783-10 versão {version}."))

    # --- talhão, contorno, linhas
    fields = list(root.iter("PFD"))
    if not fields:
        checks.append(_warn("Talhão", "Nenhum PFD declarado; o terminal não terá campo para associar."))
    for pfd in fields:
        name = pfd.get("C") or pfd.get("A")
        rings = [lsg for pln in pfd.findall("PLN") for lsg in pln.findall("LSG")]
        closed_ok = True
        for lsg in rings:
            points = [(p.get("D"), p.get("C")) for p in lsg.findall("PNT")]
            if len(points) < 3:
                closed_ok = False
            elif points[0] != points[-1]:
                closed_ok = False
        if rings:
            if closed_ok:
                checks.append(_ok("Contorno", f"Talhão '{name}': {len(rings)} anel(éis) fechado(s)."))
            else:
                checks.append(_warn(
                    "Contorno",
                    f"Talhão '{name}': algum anel não fecha no ponto inicial.",
                    "A maioria dos terminais fecha sozinha, mas alguns recusam.",
                ))

        for ggp in pfd.findall("GGP"):
            for gpn in ggp.findall("GPN"):
                types = {
                    p.get("A") for lsg in gpn.findall("LSG") for p in lsg.findall("PNT")
                }
                if gpn.get("C") == "1" and not {"6", "7"} <= types:
                    checks.append(_fail(
                        "Linha AB",
                        f"'{gpn.get('B')}' é do tipo AB mas não traz os dois pontos de "
                        "referência (tipos 6 e 7).",
                        "Sem os dois pontos o terminal não consegue gerar as passadas.",
                    ))
                else:
                    checks.append(_ok("Linha AB", f"'{gpn.get('B')}' com pontos A e B declarados."))

    # --- grade de prescrição
    for grd in root.iter("GRD"):
        name = grd.get("G")
        declared_length = int(grd.get("H") or 0)
        rows = int(grd.get("F") or 0)
        cols = int(grd.get("E") or 0)
        grid_type = grd.get("I")

        bin_path = next(
            (p for p in taskdata_dir.iterdir()
             if p.stem.upper() == (name or "").upper() and p.suffix.upper() == ".BIN"),
            None,
        )
        if bin_path is None:
            checks.append(_fail("Grade", f"O binário {name}.BIN não está na pasta."))
            continue

        actual = bin_path.stat().st_size
        expected = rows * cols * (4 if grid_type == "2" else 1)
        if actual != expected:
            checks.append(_fail(
                "Grade",
                f"{bin_path.name} tem {actual} bytes, mas {cols}×{rows} células do tipo "
                f"{grid_type} exigem {expected}.",
                "O terminal lê a grade deslocada e aplica a dose no lugar errado.",
            ))
        elif declared_length and declared_length != actual:
            checks.append(_warn(
                "Grade",
                f"O atributo de tamanho declara {declared_length} bytes e o arquivo tem {actual}.",
                "Alguns terminais confiam no valor declarado.",
            ))
        else:
            values = np.frombuffer(bin_path.read_bytes(),
                                   dtype="<u4" if grid_type == "2" else "<u1")
            with_rate = int((values > 0).sum())
            checks.append(_ok(
                "Grade",
                f"{cols}×{rows} células, {actual} bytes conferem; "
                f"{with_rate} célula(s) com dose.",
            ))
            if with_rate == 0:
                checks.append(_fail(
                    "Grade sem dose",
                    "Todas as células estão zeradas — a máquina não aplicaria nada.",
                    "Verifique o tamanho da célula: células pequenas demais podem não "
                    "alcançar metade de cobertura em nenhum polígono.",
                ))
            if rows * cols > MAX_GRID_CELLS:
                checks.append(_warn(
                    "Tamanho da grade",
                    f"{rows * cols:,} células.".replace(",", ".") +
                    " Terminais antigos demoram demais para carregar.",
                    "Aumente o tamanho da célula.",
                ))

        # A dose precisa ter um DDI declarado, senão o terminal não sabe o que é.
        task = next((t for t in root.iter("TSK") if grd in list(t.iter("GRD"))), None)
        pdv = list(task.iter("PDV")) if task is not None else []
        if not pdv:
            checks.append(_fail(
                "Unidade da dose",
                "A tarefa não declara um PDV com o DDI da grandeza aplicada.",
                "Sem o DDI, o terminal não sabe se o número é kg/ha, L/ha ou sementes/ha.",
            ))
        else:
            checks.append(_ok("Unidade da dose",
                              f"DDI {pdv[0].get('A')} declarado na zona de tratamento."))

    return checks


# ==========================================================================
# Pacote inteiro
# ==========================================================================

def validate_package(folder: Path, monitor: str = "generic") -> dict[str, Any]:
    """Verifica todos os arquivos de um pacote e resume o resultado."""
    from . import packages as packages_mod

    folder = Path(folder)
    profile = packages_mod.get_profile(monitor)
    groups: list[dict[str, Any]] = []

    for taskdata in sorted(folder.rglob("TASKDATA")):
        if taskdata.is_dir():
            groups.append({
                "arquivo": str(taskdata.relative_to(folder)),
                "tipo": "ISOXML",
                "checks": [c.to_dict() for c in validate_taskdata(taskdata)],
            })

    for shp in sorted(folder.rglob("*.shp")):
        import geopandas as gpd

        # O contorno não tem campo de dose; a prescrição tem.
        is_boundary = "contorno" in shp.stem.lower() or "boundary" in shp.stem.lower()
        groups.append({
            "arquivo": str(shp.relative_to(folder)),
            "tipo": "Contorno (shapefile)" if is_boundary else "Prescrição (shapefile)",
            "checks": [
                c.to_dict() for c in validate_shapefile(
                    shp,
                    rate_field=None if is_boundary else profile.rate_field,
                    expect_polygons=True,
                )
            ],
        })

    all_checks = [c for g in groups for c in g["checks"]]
    failures = sum(1 for c in all_checks if c["status"] == "falha")
    warnings = sum(1 for c in all_checks if c["status"] == "atencao")

    if failures:
        verdict = "falha"
        summary = (
            f"{failures} problema(s) que o {profile.label} recusaria. "
            "Corrija antes de levar o pen drive."
        )
    elif warnings:
        verdict = "atencao"
        summary = (
            f"Nenhum impedimento, mas {warnings} ponto(s) dependem do firmware ou da "
            "configuração do display. Confirme na tela antes de começar."
        )
    elif all_checks:
        verdict = "ok"
        summary = (
            f"Todas as {len(all_checks)} verificações passaram. As causas conhecidas de "
            f"recusa no {profile.label} estão eliminadas."
        )
    else:
        verdict = "atencao"
        summary = "Nenhum arquivo verificável foi encontrado no pacote."

    return {
        "monitor": profile.key,
        "monitor_label": profile.label,
        "verdict": verdict,
        "summary": summary,
        "totals": {
            "ok": sum(1 for c in all_checks if c["status"] == "ok"),
            "atencao": warnings,
            "falha": failures,
        },
        "groups": groups,
        "ressalva": (
            "Esta verificação confere o conteúdo dos arquivos contra o padrão e contra "
            "as causas conhecidas de recusa. Ela não tem como testar a versão de "
            "firmware do seu display nem formatos proprietários — a confirmação final "
            "é carregar o pacote no monitor antes de ir para a lavoura."
        ),
    }
