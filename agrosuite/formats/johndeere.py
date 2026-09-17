"""Estruturas de cartão John Deere.

Quando o SMS exporta pela opção **GreenStar**, ele não escreve um arquivo
solto: escreve a árvore de pastas que o display espera encontrar no cartão
ou no pen drive. Reconhecer essa árvore é o que permite ao AgroSuite abrir
o que o usuário já tem em mãos, em vez de exigir que ele reexporte tudo em
shapefile.

As três gerações têm raízes diferentes::

    GS2_2600/   GreenStar 2 (display 2600)
    GS3_2630/   GreenStar 3 (display 2630)
    JD-Data/    Gen 4 (displays 4200, 4600, 4640) e Gen 5

e, dentro delas, a mesma divisão de papéis::

    SETUP/      dados que vão PARA o display: clientes, fazendas, talhões,
                contornos, linhas de orientação, prescrições
    RCD/        dados que VÊM do display: registros de operação

Sobre o que este módulo faz e o que não faz
-------------------------------------------
Os arquivos de setup que o SMS grava dentro de ``SETUP/`` são de formato
proprietário da John Deere, e o AgroSuite **não** tenta reconstruí-los. O
que ele faz é inventariar o cartão, ler tudo que estiver em formato aberto
(shapefile de contorno, de linha de orientação ou de prescrição, que é como
boa parte das exportações do SMS grava a geometria) e dizer com clareza o
que encontrou e o que não consegue interpretar — em vez de falhar em
silêncio ou, pior, produzir um arquivo que o display recusa na lavoura.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Raízes de cartão reconhecidas, da mais nova para a mais antiga.
CARD_ROOTS = {
    "JD-Data": "Gen 4 / Gen 5 (4200, 4600, 4640)",
    "GS3_2630": "GreenStar 3 (display 2630)",
    "GS3_2600": "GreenStar 3 (display 2600)",
    "GS2_2600": "GreenStar 2 (display 2600)",
    "GS2_1800": "GreenStar 2 (display 1800)",
}

#: Subpastas com significado definido dentro da raiz do cartão.
CARD_SUBFOLDERS = {
    "SETUP": "Dados que vão para o display (talhões, contornos, linhas, prescrições)",
    "RCD": "Dados gravados pelo display durante a operação",
    "DOCUMENTATION": "Documentação de operação exportada",
    "BOUNDARIES": "Contornos de talhão",
    "GUIDANCE": "Linhas de orientação",
    "RX": "Prescrições de taxa variável",
    "SHAPEFILES": "Camadas em shapefile",
}

#: Extensões de formato aberto que conseguimos interpretar de dentro do cartão.
READABLE_EXT = {".shp", ".geojson", ".json", ".kml", ".kmz", ".csv", ".txt", ".xml"}

#: Extensões proprietárias da John Deere. Listadas para que o inventário
#: diga o que são, em vez de chamá-las de "arquivo desconhecido".
PROPRIETARY_EXT = {
    ".gsd": "Documentação GreenStar (binário proprietário)",
    ".fdd": "Field Doc Data (binário proprietário)",
    ".fdl": "Field Doc Log (binário proprietário)",
    ".jdp": "Pacote de dados John Deere",
    ".jdf": "Arquivo de setup John Deere",
    ".ver": "Controle de versão do cartão",
    ".dat": "Dado binário do display",
    ".bin": "Dado binário do display",
}


@dataclass
class CardInventory:
    """O que foi encontrado num cartão John Deere."""

    root: Path
    card_root: Path | None = None
    generation: str = "desconhecida"
    readable: list[dict[str, Any]] = field(default_factory=list)
    proprietary: list[dict[str, Any]] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "card_root": str(self.card_root) if self.card_root else None,
            "generation": self.generation,
            "folders": self.folders,
            "readable": self.readable,
            "proprietary": self.proprietary,
            "summary": self.summary(),
        }

    def summary(self) -> str:
        if not self.card_root:
            return "Nenhuma estrutura de cartão John Deere encontrada."
        parts = [f"Cartão {self.generation}."]
        if self.readable:
            parts.append(f"{len(self.readable)} arquivo(s) em formato aberto, legíveis aqui.")
        if self.proprietary:
            parts.append(
                f"{len(self.proprietary)} arquivo(s) em formato proprietário da John Deere, "
                "que só o software dela ou o próprio display convertem."
            )
        if not self.readable and self.proprietary:
            parts.append(
                "Para trazer esse conteúdo ao AgroSuite, reexporte no SMS escolhendo "
                "shapefile em vez de GreenStar."
            )
        return " ".join(parts)


def find_card_root(path: Path) -> tuple[Path | None, str]:
    """Localiza a raiz do cartão e diz a que geração ela pertence."""
    path = Path(path)
    candidates = [path, *[p for p in path.iterdir() if p.is_dir()]] if path.is_dir() else [path]

    for candidate in candidates:
        if candidate.name in CARD_ROOTS:
            return candidate, CARD_ROOTS[candidate.name]

    # O cartão pode estar mais fundo, dentro de uma pasta de backup.
    if path.is_dir():
        for name, label in CARD_ROOTS.items():
            for found in path.rglob(name):
                if found.is_dir():
                    return found, label

    # Estrutura sem a pasta-raiz nomeada, mas com SETUP/RCD lado a lado.
    if path.is_dir():
        children = {p.name.upper() for p in path.iterdir() if p.is_dir()}
        if {"SETUP", "RCD"} & children:
            return path, "GreenStar (raiz não nomeada)"

    return None, "desconhecida"


def inventory(path: Path) -> CardInventory:
    """Inventaria um cartão: o que dá para ler e o que é proprietário."""
    path = Path(path)
    card_root, generation = find_card_root(path)
    result = CardInventory(root=path, card_root=card_root, generation=generation)
    if card_root is None:
        return result

    result.folders = sorted(
        p.name for p in card_root.iterdir() if p.is_dir()
    )

    for item in sorted(card_root.rglob("*")):
        if not item.is_file():
            continue
        suffix = item.suffix.lower()
        relative = str(item.relative_to(card_root))
        entry = {
            "path": str(item),
            "relative": relative,
            "name": item.name,
            "size": item.stat().st_size,
            "folder_role": CARD_SUBFOLDERS.get(item.parent.name.upper(), ""),
        }
        if suffix in READABLE_EXT:
            result.readable.append({**entry, "kind": suffix.lstrip(".")})
        elif suffix in PROPRIETARY_EXT:
            result.proprietary.append({**entry, "kind": PROPRIETARY_EXT[suffix]})

    return result


def readable_layers(inv: CardInventory) -> list[dict[str, Any]]:
    """Camadas do cartão que o AgroSuite consegue importar, já classificadas.

    A classificação por palavra-chave no caminho é o que permite dizer
    "este é o contorno" sem abrir todos os arquivos: as exportações do SMS
    e do Operations Center nomeiam as pastas e os arquivos de forma
    consistente.
    """
    layers = []
    for entry in inv.readable:
        if entry["kind"] not in ("shp", "geojson", "json", "kml", "kmz"):
            continue
        text = entry["relative"].lower()
        if any(k in text for k in ("boundary", "bound", "contorno", "limite", "field_border")):
            role = "boundary"
        elif any(k in text for k in ("guidance", "abline", "ab_line", "track", "swath", "linha")):
            role = "guidance"
        elif any(k in text for k in ("rx", "prescription", "prescricao", "target")):
            role = "prescription"
        else:
            role = "data"
        layers.append({**entry, "role": role})
    return layers


def describe_export_paths(generation_key: str = "gen4") -> dict[str, Any]:
    """Onde cada geração de display procura os arquivos no pen drive.

    Estes caminhos descrevem a estrutura de cartão da plataforma; o menu de
    importação muda entre versões de firmware, então o pacote gerado sempre
    vem com instruções pedindo que o operador confirme na tela.
    """
    paths = {
        "gen4": {
            "label": "Gen 4 / Gen 5 (4200, 4600, 4640)",
            "card_root": "JD-Data",
            "notes": (
                "Prescrição em shapefile é importada pelo próprio display, no "
                "gerenciador de arquivos do pen drive. Contorno e linhas de "
                "orientação chegam de forma mais confiável pelo Operations Center, "
                "sincronizados para a máquina."
            ),
        },
        "gs3": {
            "label": "GreenStar 3 (2630)",
            "card_root": "GS3_2630",
            "notes": (
                "O cartão separa SETUP (o que vai para o display) de RCD (o que o "
                "display gravou). Os arquivos de setup são proprietários e saem do "
                "SMS ou do Apex, não do AgroSuite."
            ),
        },
        "gs2": {
            "label": "GreenStar 2 (2600)",
            "card_root": "GS2_2600",
            "notes": "Mesma divisão SETUP/RCD do GreenStar 3.",
        },
    }
    return paths.get(generation_key, paths["gen4"])
