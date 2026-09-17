"""Pacotes de exportação por monitor.

Gerar o arquivo certo é metade do trabalho; a outra metade é entregá-lo na
estrutura de pasta que o terminal procura. Este módulo descreve, para cada
plataforma, o que ela aceita e como o pen drive deve ficar, e monta a pasta
pronta para copiar.

Duas convenções são padronizadas e valem para qualquer terminal ISOBUS:

* a pasta se chama ``TASKDATA`` e fica na **raiz** do pen drive;
* um shapefile só abre com ``.shp``, ``.shx``, ``.dbf`` e ``.prj`` juntos.

O resto varia por fabricante e por versão de firmware, então cada pacote sai
com um arquivo de instruções dizendo o caminho de importação e o que
conferir na tela — em vez de o app fingir certeza sobre menus que mudam a
cada atualização.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Artefatos que um pacote pode conter.
ARTIFACT_LABELS = {
    "prescription": "Prescrição (taxa variável)",
    "boundary": "Contorno do talhão",
    "guidance": "Linhas de orientação (AB)",
    "data": "Dados de campo (pontos)",
}


@dataclass(frozen=True)
class MonitorProfile:
    """Como preparar arquivos para um monitor específico."""

    key: str
    label: str
    #: Artefatos que esta plataforma consegue receber.
    accepts: tuple[str, ...]
    #: Formato preferido para cada artefato.
    preferred: dict[str, str]
    #: Subpasta dentro do pacote, por formato.
    layout: dict[str, str] = field(default_factory=dict)
    #: Nome do campo de dose que o monitor procura no shapefile.
    rate_field: str = "RATE"
    instructions: tuple[str, ...] = ()


ISOBUS_STEPS = (
    "Copie a pasta TASKDATA inteira para a RAIZ do pen drive — não dentro de "
    "outra pasta, e sem renomear.",
    "Use um pen drive formatado em FAT32. Muitos terminais não leem exFAT ou NTFS.",
    "No terminal, entre em Importar / Gerenciador de dados e selecione o pen drive.",
    "Confira na tela se o talhão, as linhas de orientação e a tarefa apareceram "
    "antes de ir para a lavoura.",
)

SHAPEFILE_STEPS = (
    "Copie os quatro arquivos juntos (.shp, .shx, .dbf, .prj). Faltando qualquer "
    "um deles, o monitor não abre o mapa.",
    "Na importação, o monitor pergunta qual coluna contém a dose: escolha a "
    "coluna indicada no arquivo LEIA-ME deste pacote.",
    "Confirme a unidade da dose na tela do monitor — o shapefile guarda o número, "
    "não a unidade.",
)


PROFILES: tuple[MonitorProfile, ...] = (
    MonitorProfile(
        key="raven",
        label="Raven Viper 4",
        accepts=("prescription", "boundary", "guidance"),
        preferred={"prescription": "shapefile", "boundary": "shapefile", "guidance": "isoxml"},
        layout={"shapefile": "Raven", "isoxml": "."},
        rate_field="RATE",
        instructions=SHAPEFILE_STEPS + (
            "O Viper 4 importa a prescrição em File Manager → USB, escolhendo o "
            "shapefile de polígonos e depois a coluna de dose.",
            "Contorno de talhão também entra como shapefile de polígono.",
            "Se o seu Viper 4 estiver com firmware ISOBUS habilitado, a pasta "
            "TASKDATA deste pacote traz as linhas AB e o contorno de uma vez.",
        ),
    ),
    MonitorProfile(
        key="john_deere",
        label="John Deere (Gen 4 / Operations Center)",
        accepts=("prescription", "boundary", "guidance", "data"),
        preferred={"prescription": "shapefile", "boundary": "shapefile", "guidance": "isoxml"},
        layout={"shapefile": "Rx", "isoxml": "."},
        rate_field="RATE",
        instructions=SHAPEFILE_STEPS + (
            "Caminho mais confiável: envie o shapefile ao Operations Center "
            "(Arquivos → Carregar) e mande o mapa ao monitor pelo Data Sync.",
            "Sem conexão, importe o shapefile direto do pen drive pelo próprio "
            "display Gen 4, em Gerenciador de arquivos.",
            "Displays GreenStar antigos (2600/2630) exigem que o mapa passe antes "
            "pelo software de escritório.",
        ),
    ),
    MonitorProfile(
        key="case_ih",
        label="Case IH AFS Pro / AFS Connect",
        accepts=("prescription", "boundary", "guidance"),
        preferred={"prescription": "isoxml", "boundary": "isoxml", "guidance": "isoxml"},
        layout={"isoxml": ".", "shapefile": "Shapefile"},
        instructions=ISOBUS_STEPS + (
            "O AFS Pro 700 e o AFS Connect leem ISOXML nativamente — é o caminho "
            "preferido, porque leva contorno, linhas AB e prescrição num arquivo só.",
            "A pasta Shapefile deste pacote é alternativa, para o caso de o "
            "terminal recusar o TASKDATA.",
        ),
    ),
    MonitorProfile(
        key="new_holland",
        label="New Holland IntelliView / PLM",
        accepts=("prescription", "boundary", "guidance"),
        preferred={"prescription": "isoxml", "boundary": "isoxml", "guidance": "isoxml"},
        layout={"isoxml": ".", "shapefile": "Shapefile"},
        instructions=ISOBUS_STEPS + (
            "IntelliView IV e XCN são ISOBUS: o TASKDATA é o caminho direto.",
        ),
    ),
    MonitorProfile(
        key="bourgault",
        label="Bourgault X30 / X35",
        accepts=("prescription", "boundary", "guidance"),
        preferred={"prescription": "isoxml", "boundary": "isoxml", "guidance": "isoxml"},
        layout={"isoxml": ".", "shapefile": "Shapefile"},
        instructions=ISOBUS_STEPS + (
            "No X35, a prescrição aparece na tarefa importada; associe cada tanque "
            "ao produto correspondente antes de iniciar.",
        ),
    ),
    MonitorProfile(
        key="vaderstad",
        label="Väderstad E-Control",
        accepts=("prescription", "boundary", "guidance"),
        preferred={"prescription": "isoxml", "boundary": "isoxml", "guidance": "isoxml"},
        layout={"isoxml": "."},
        instructions=ISOBUS_STEPS,
    ),
    MonitorProfile(
        key="topcon",
        label="Topcon / Müller",
        accepts=("prescription", "boundary", "guidance"),
        preferred={"prescription": "isoxml", "boundary": "isoxml", "guidance": "isoxml"},
        layout={"isoxml": "."},
        instructions=ISOBUS_STEPS,
    ),
    MonitorProfile(
        key="trimble",
        label="Trimble GFX / TMX / FmX",
        accepts=("prescription", "boundary", "guidance"),
        preferred={"prescription": "shapefile", "boundary": "shapefile", "guidance": "isoxml"},
        layout={"shapefile": "AgData", "isoxml": "."},
        rate_field="TGT_RATE",
        instructions=SHAPEFILE_STEPS + (
            "Nos displays GFX/TMX, importe pelo gerenciador de dados escolhendo o "
            "shapefile e a coluna TGT_RATE.",
            "Modelos com ISOBUS habilitado também aceitam a pasta TASKDATA.",
        ),
    ),
    MonitorProfile(
        key="ag_leader",
        label="Ag Leader InCommand / SMS",
        accepts=("prescription", "boundary", "data"),
        preferred={"prescription": "shapefile", "boundary": "shapefile"},
        layout={"shapefile": "AgLeader"},
        instructions=SHAPEFILE_STEPS + (
            "No InCommand, importe em Configuração → Talhão → Prescrição.",
        ),
    ),
    MonitorProfile(
        key="augmenta",
        label="Augmenta",
        accepts=("prescription", "boundary", "data"),
        preferred={"prescription": "geojson", "boundary": "geojson"},
        layout={"geojson": "Augmenta", "shapefile": "Shapefile"},
        instructions=(
            "O Augmenta trabalha com GeoJSON e shapefile: use o GeoJSON quando "
            "for subir pelo painel web, e o shapefile quando for pelo pen drive.",
            "O sistema decide a dose em tempo real pela câmera; a prescrição entra "
            "como limite ou como mapa base, conforme a configuração da máquina.",
        ),
    ),
    MonitorProfile(
        key="generic",
        label="Genérico (qualquer monitor)",
        accepts=("prescription", "boundary", "guidance", "data"),
        preferred={"prescription": "shapefile", "boundary": "shapefile", "guidance": "isoxml"},
        layout={},
        instructions=SHAPEFILE_STEPS + ISOBUS_STEPS,
    ),
)

PROFILES_BY_KEY = {p.key: p for p in PROFILES}


def get_profile(key: str | None) -> MonitorProfile:
    """Perfil de exportação pela chave, caindo para o genérico."""
    return PROFILES_BY_KEY.get(key or "", PROFILES_BY_KEY["generic"])


def profile_catalog() -> list[dict[str, Any]]:
    """Catálogo serializável para a interface."""
    return [
        {
            "key": p.key,
            "label": p.label,
            "accepts": list(p.accepts),
            "preferred": dict(p.preferred),
            "rate_field": p.rate_field,
            "instructions": list(p.instructions),
        }
        for p in PROFILES
    ]


def write_readme(
    folder: Path,
    profile: MonitorProfile,
    contents: list[dict[str, Any]],
    rate_field: str | None = None,
    rate_unit: str | None = None,
) -> Path:
    """Escreve o LEIA-ME que acompanha o pacote."""
    lines = [
        f"PACOTE PARA {profile.label.upper()}",
        "=" * (12 + len(profile.label)),
        "",
        "Gerado pelo AgroSuite.",
        "",
        "CONTEÚDO",
        "--------",
    ]
    for item in contents:
        detail = item.get("detail", "")
        lines.append(f"  {item['path']}")
        lines.append(f"      {ARTIFACT_LABELS.get(item['artifact'], item['artifact'])}"
                     + (f" — {detail}" if detail else ""))
    lines.append("")

    if rate_field:
        lines += [
            "COLUNA DE DOSE",
            "--------------",
            f"  No shapefile, a dose está no campo: {rate_field}",
            f"  Unidade gravada: {rate_unit or 'conforme escolhido na exportação'}",
            "  O shapefile guarda apenas o número — confirme a unidade no monitor.",
            "",
        ]

    lines += ["COMO CARREGAR", "-------------"]
    for index, step in enumerate(profile.instructions, start=1):
        lines.append(f"  {index}. {step}")
    lines += [
        "",
        "ANTES DE IR PARA A LAVOURA",
        "--------------------------",
        "  - Confirme que o talhão certo foi selecionado no monitor.",
        "  - Confira a dose exibida numa região conhecida do mapa.",
        "  - Verifique se o produto e a unidade batem com o que está no tanque.",
        "",
    ]

    path = folder / "LEIA-ME.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
