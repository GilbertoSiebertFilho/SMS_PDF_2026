"""Identificação do monitor de origem.

Não existe cabeçalho universal que diga "este arquivo veio de um John Deere".
O que existe são assinaturas: nomes de coluna característicos que cada
software de exportação produz, marcadores de pasta e extensões próprias.
Este módulo concentra essas assinaturas e pontua cada fabricante contra o
arquivo recebido, devolvendo o mais provável.

As assinaturas foram levantadas a partir das exportações reais de cada
plataforma; quando um fabricante pode exportar em vários formatos (o caso
mais comum), ele aparece com várias entradas de assinatura.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.schema import normalize_name


@dataclass(frozen=True)
class BrandProfile:
    """Perfil de um fabricante/plataforma de monitor."""

    key: str
    label: str
    #: Colunas cuja simples presença é forte indício deste fabricante.
    signature_columns: tuple[str, ...] = ()
    #: Colunas de apoio: somam pontos, mas não decidem sozinhas.
    hint_columns: tuple[str, ...] = ()
    #: Nomes de arquivo/pasta típicos da estrutura de exportação.
    path_markers: tuple[str, ...] = ()
    #: Extensões proprietárias associadas.
    extensions: tuple[str, ...] = ()
    #: Unidades em que a plataforma normalmente exporta.
    default_units: dict[str, str] = field(default_factory=dict)
    #: Formatos que o AgroSuite consegue **escrever** para esta plataforma.
    export_formats: tuple[str, ...] = ()
    notes: str = ""


BRANDS: tuple[BrandProfile, ...] = (
    BrandProfile(
        key="john_deere",
        label="John Deere",
        signature_columns=(
            "vryieldvol", "vryieldmass", "yld_vol_dr", "yld_mass_d",
            "jd_machine", "machine_model", "operation_operator",
        ),
        hint_columns=(
            "client", "farm", "field", "task", "product", "variety",
            "crop", "machine", "operator", "recording_status",
        ),
        path_markers=(
            "rcd", "setup", "documentation", "gs3_2630", "gs2_2600",
            "operations_center", "myjohndeere",
        ),
        extensions=(".gsd", ".fdd", ".jdf"),
        default_units={"yield": "bu/ac", "speed": "mph", "swath": "ft"},
        export_formats=("shapefile_rx", "isoxml", "csv"),
        notes=(
            "Exportações do Operations Center e do SMS saem em shapefile ou CSV. "
            "Arquivos binários .gsd/.fdd do GreenStar 2/3 são proprietários e "
            "precisam ser convertidos no próprio software antes da importação."
        ),
    ),
    BrandProfile(
        key="ag_leader",
        label="Ag Leader / SMS",
        signature_columns=(
            "obj_id", "obj__id", "swth_wdth_", "yld_vol_dr", "yld_mass_w",
            "prod_ac_h", "duration_s", "track_deg_",
        ),
        hint_columns=("distance_f", "elevation_", "time", "crop_flow_v"),
        path_markers=("sms", "agleader", "agdata"),
        extensions=(".agdata", ".ilf"),
        default_units={"yield": "bu/ac", "speed": "mph", "swath": "ft"},
        export_formats=("shapefile_rx", "csv"),
        notes="O SMS Advanced é o formato-ponte mais comum entre plataformas.",
    ),
    BrandProfile(
        key="raven",
        label="Raven / Viper",
        signature_columns=(
            "vipertarget", "viper_rate", "raven_rate", "prod_rate", "boom_section",
            "target_rate_1", "applied_rate_1",
        ),
        hint_columns=("section", "product", "tank_mix", "swath", "coverage"),
        path_markers=("viper", "raven", "slingshot", "vt_files", "cn1"),
        extensions=(".cn1", ".vpr"),
        default_units={"rate": "gal/ac", "speed": "mph", "swath": "ft"},
        export_formats=("shapefile_rx", "csv"),
        notes=(
            "O Viper 4 lê prescrição em shapefile de polígonos com uma coluna "
            "numérica de dose escolhida no próprio monitor."
        ),
    ),
    BrandProfile(
        key="trimble",
        label="Trimble",
        signature_columns=("agdata_id", "tgt_rate_o", "trimble_rate", "fmx_rate"),
        hint_columns=("coverage", "applied", "material", "swath", "vehicle"),
        path_markers=("aggps", "agdata", "fmx", "gfx", "tmx", "trimble", "farmworks"),
        extensions=(".agdata", ".vrt"),
        default_units={"rate": "lb/ac", "speed": "mph", "swath": "ft"},
        export_formats=("shapefile_rx", "isoxml", "csv"),
        notes="FmX/GFX/TMX aceitam Rx em shapefile; os modelos ISOBUS também leem ISOXML.",
    ),
    BrandProfile(
        key="case_ih",
        label="Case IH (AFS)",
        signature_columns=("afs_rate", "afs_field", "voyager_id"),
        hint_columns=("grower", "farm", "field", "load", "operator", "task"),
        path_markers=("afs", "case", "voyager", "cnh", "advanced_farming",
                      "case_ih", "cnh_industrial", "afs_pro", "afs_connect"),
        extensions=(".vy1", ".vy2", ".vy3"),
        default_units={"yield": "bu/ac", "speed": "mph", "swath": "ft"},
        export_formats=("shapefile_rx", "isoxml", "csv"),
        notes="AFS Pro 700 / AFS Vision leem ISOXML; formatos Voyager são proprietários.",
    ),
    BrandProfile(
        key="new_holland",
        label="New Holland (PLM)",
        signature_columns=("plm_rate", "nh_field", "intelliview_id"),
        hint_columns=("grower", "farm", "field", "load", "operator", "task"),
        path_markers=("plm", "newholland", "new_holland", "intelliview",
                      "precision_land_management"),
        extensions=(".vy1", ".vy2", ".vy3"),
        default_units={"yield": "bu/ac", "speed": "mph", "swath": "ft"},
        export_formats=("shapefile_rx", "isoxml", "csv"),
        notes="IntelliView IV/XCN são ISOBUS e consomem TASKDATA.XML diretamente.",
    ),
    BrandProfile(
        key="bourgault",
        label="Bourgault",
        signature_columns=("bourgault_rate", "x35_rate", "tank_1_rate", "tank_2_rate"),
        hint_columns=("tank", "product", "section", "blockage", "shank"),
        path_markers=("bourgault", "x35", "x30", "apollo"),
        extensions=(),
        default_units={"rate": "lb/ac", "speed": "mph", "swath": "ft"},
        export_formats=("isoxml", "shapefile_rx", "csv"),
        notes="Os monitores X30/X35 trabalham com ISOXML e com Rx em shapefile.",
    ),
    BrandProfile(
        key="vaderstad",
        label="Väderstad",
        signature_columns=("vaderstad_rate", "e_control_rate", "econtrol_id"),
        hint_columns=("seed_rate", "fertilizer_rate", "section", "row"),
        path_markers=("vaderstad", "vaderstad", "e_control", "econtrol", "iseed",
                      "e_services", "vaderstad_ab"),
        extensions=(),
        default_units={"rate": "kg/ha", "speed": "km/h", "swath": "m"},
        export_formats=("isoxml", "shapefile_rx"),
        notes="E-Control / E-Services são ISOBUS — a via oficial é TASKDATA.XML.",
    ),
    BrandProfile(
        key="augmenta",
        label="Augmenta",
        signature_columns=(
            "augmenta_id", "session_id", "vigor", "vra_rate", "canopy",
            "crop_coverage", "biomass_index",
        ),
        hint_columns=("ndvi", "ndre", "index", "applied_rate", "machine_id", "nozzle"),
        path_markers=("augmenta", "augmenta_export", "field_analysis"),
        extensions=(".augmenta",),
        default_units={"rate": "l/ha", "speed": "km/h", "swath": "m"},
        export_formats=("geojson", "shapefile_rx", "csv"),
        notes=(
            "O Augmenta exporta GeoJSON/SHP por sessão, com índices de vigor e a "
            "dose efetivamente aplicada pelo sistema de visão."
        ),
    ),
    BrandProfile(
        key="climate_fieldview",
        label="Climate FieldView",
        signature_columns=("fieldview_id", "cfv_rate"),
        hint_columns=("hybrid", "variety", "seed_rate", "yield", "elevation"),
        path_markers=("fieldview", "climate"),
        extensions=(),
        default_units={"yield": "bu/ac", "speed": "mph", "swath": "ft"},
        export_formats=("shapefile_rx", "csv"),
    ),
    BrandProfile(
        key="topcon",
        label="Topcon / Müller",
        signature_columns=("topcon_rate", "mueller_rate"),
        hint_columns=("task", "product", "section", "rate"),
        path_markers=("topcon", "mueller", "muller", "x35_topcon", "tap"),
        extensions=(),
        default_units={"rate": "kg/ha", "speed": "km/h", "swath": "m"},
        export_formats=("isoxml", "shapefile_rx"),
    ),
    BrandProfile(
        key="isoxml",
        label="ISOBUS / ISOXML genérico",
        signature_columns=("ddi", "tlg", "grd", "pdt", "ctp"),
        hint_columns=(),
        path_markers=("taskdata", "taskdata.xml", "grd", "tlg"),
        extensions=(".iso", ".xml"),
        default_units={"rate": "kg/ha", "speed": "km/h", "swath": "m"},
        export_formats=("isoxml",),
        notes="Padrão ISO 11783-10, aceito pela maioria dos terminais ISOBUS.",
    ),
    BrandProfile(
        key="generic",
        label="Genérico / não identificado",
        export_formats=("shapefile_rx", "csv", "geojson", "isoxml"),
    ),
)

BRANDS_BY_KEY = {b.key: b for b in BRANDS}


def get_brand(key: str | None) -> BrandProfile:
    """Perfil do fabricante pela chave, caindo para o genérico."""
    return BRANDS_BY_KEY.get(key or "", BRANDS_BY_KEY["generic"])


def detect_brand(
    columns: list[str] | None = None,
    path: str | None = None,
    extra_text: str | None = None,
) -> tuple[str, float]:
    """Identifica o fabricante mais provável.

    Returns
    -------
    (chave, confiança)
        A confiança vai de 0 a 1; abaixo de ~0.3 o resultado deve ser tratado
        como palpite e confirmado pelo usuário na interface.
    """
    norm_cols = {normalize_name(c) for c in (columns or [])}
    haystack = normalize_name(path or "") + " " + normalize_name(extra_text or "")

    scores: dict[str, float] = {}
    for brand in BRANDS:
        if brand.key == "generic":
            continue
        score = 0.0
        for col in brand.signature_columns:
            if normalize_name(col) in norm_cols:
                score += 3.0
        for col in brand.hint_columns:
            if normalize_name(col) in norm_cols:
                score += 0.5
        for marker in brand.path_markers:
            if normalize_name(marker) in haystack:
                score += 2.5
        for ext in brand.extensions:
            if haystack.endswith(normalize_name(ext)) or normalize_name(ext).strip("_") in haystack:
                score += 2.0
        if score > 0:
            scores[brand.key] = score

    if not scores:
        return "generic", 0.0

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = scores[best] / total if total else 0.0
    # Uma pontuação absoluta baixa não vira certeza só por ser a única.
    confidence *= min(1.0, scores[best] / 5.0)
    return best, round(confidence, 3)


def brand_catalog() -> list[dict]:
    """Catálogo serializável para a interface."""
    return [
        {
            "key": b.key,
            "label": b.label,
            "export_formats": list(b.export_formats),
            "default_units": dict(b.default_units),
            "notes": b.notes,
        }
        for b in BRANDS
    ]
