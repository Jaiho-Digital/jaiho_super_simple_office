"""Widescreen presentation model, 12 pastel slide themes, and native .pptx I/O for SimpleSlides."""

from __future__ import annotations

import io
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_bytes,
    validate_input_file,
)

APP_ID = "simple_slides"

DEFAULT_PRESENTATION_EXTENSION = ".pptx"
SUPPORTED_OPEN_EXTENSIONS: frozenset[str] = frozenset({".pptx"})

# Standard 16:9 Widescreen dimensions in OpenXML EMUs (13.333 in x 7.5 in)
_SLIDE_CX = 12192000
_SLIDE_CY = 6858000

_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

_P = f"{{{_P_NS}}}"
_A = f"{{{_A_NS}}}"
_R = f"{{{_R_NS}}}"
_PR = f"{{{_PR_NS}}}"


@dataclass(frozen=True, slots=True)
class SlideThemePalette:
    """Color specification for rendering a slide canvas and exporting to .pptx."""

    theme_id: str
    display_name: str
    bg_hex: str
    card_hex: str
    title_hex: str
    body_hex: str
    muted_hex: str
    accent_hex: str
    border_hex: str


SLIDE_THEMES: dict[str, SlideThemePalette] = {
    "lavender_snow": SlideThemePalette(
        theme_id="lavender_snow",
        display_name="Snow & Lavender",
        bg_hex="#FFFEFC",
        card_hex="#E8E0F7",
        title_hex="#35343B",
        body_hex="#4A4852",
        muted_hex="#77747E",
        accent_hex="#6F59C9",
        border_hex="#E9E7E3",
    ),
    "sage_ivory": SlideThemePalette(
        theme_id="sage_ivory",
        display_name="Ivory & Sage",
        bg_hex="#FAF9F5",
        card_hex="#DDEFE8",
        title_hex="#2E3532",
        body_hex="#434D48",
        muted_hex="#6E7570",
        accent_hex="#477A63",
        border_hex="#E4E4DC",
    ),
    "peach_cream": SlideThemePalette(
        theme_id="peach_cream",
        display_name="Cream & Peach",
        bg_hex="#FBF7F3",
        card_hex="#F4E5D9",
        title_hex="#3A3335",
        body_hex="#52484B",
        muted_hex="#7B6F73",
        accent_hex="#BA5B6E",
        border_hex="#EAE2DA",
    ),
    "powder_mist": SlideThemePalette(
        theme_id="powder_mist",
        display_name="Snow & Powder Blue",
        bg_hex="#FFFEFC",
        card_hex="#DFEAF8",
        title_hex="#2F3640",
        body_hex="#454E5B",
        muted_hex="#717B8A",
        accent_hex="#3E6FA8",
        border_hex="#E5E8EE",
    ),
    "blush_rose": SlideThemePalette(
        theme_id="blush_rose",
        display_name="Blush Rose Studio",
        bg_hex="#FDF8FA",
        card_hex="#F7DFE7",
        title_hex="#3B2F34",
        body_hex="#54444B",
        muted_hex="#7F6D75",
        accent_hex="#B84D6B",
        border_hex="#EDE0E5",
    ),
    "butter_silk": SlideThemePalette(
        theme_id="butter_silk",
        display_name="Warm Butter Silk",
        bg_hex="#FCFBF6",
        card_hex="#F8EBCF",
        title_hex="#3A3429",
        body_hex="#524A3C",
        muted_hex="#7C7362",
        accent_hex="#9A6B21",
        border_hex="#ECE6D8",
    ),
    "mint_breeze": SlideThemePalette(
        theme_id="mint_breeze",
        display_name="Soft Mint Breeze",
        bg_hex="#F7FBF9",
        card_hex="#DDF2EC",
        title_hex="#293834",
        body_hex="#3E524C",
        muted_hex="#687D77",
        accent_hex="#2E7D6B",
        border_hex="#DFECE8",
    ),
    "periwinkle_air": SlideThemePalette(
        theme_id="periwinkle_air",
        display_name="Periwinkle Mist",
        bg_hex="#F8F8FC",
        card_hex="#E3E6FA",
        title_hex="#2F3142",
        body_hex="#45485E",
        muted_hex="#70738A",
        accent_hex="#525DC2",
        border_hex="#E3E4F0",
    ),
    "warm_linen": SlideThemePalette(
        theme_id="warm_linen",
        display_name="Warm Linen Paper",
        bg_hex="#F8F5F0",
        card_hex="#EFE8DD",
        title_hex="#38322C",
        body_hex="#504840",
        muted_hex="#7A7066",
        accent_hex="#7C5E42",
        border_hex="#E6DFD5",
    ),
    "lilac_velvet": SlideThemePalette(
        theme_id="lilac_velvet",
        display_name="Soft Lilac Haze",
        bg_hex="#FAF7FC",
        card_hex="#EFE3F7",
        title_hex="#362E3D",
        body_hex="#4D4356",
        muted_hex="#776B82",
        accent_hex="#884EA0",
        border_hex="#EAE1F0",
    ),
    "midnight_velvet": SlideThemePalette(
        theme_id="midnight_velvet",
        display_name="Midnight Orchid",
        bg_hex="#1D1D23",
        card_hex="#2C2740",
        title_hex="#F2F0F5",
        body_hex="#D6D3E0",
        muted_hex="#9E9AA8",
        accent_hex="#A992F7",
        border_hex="#32313D",
    ),
    "slate_emerald": SlideThemePalette(
        theme_id="slate_emerald",
        display_name="Deep Slate & Sage",
        bg_hex="#19201E",
        card_hex="#24332E",
        title_hex="#EFF5F2",
        body_hex="#CEE0D8",
        muted_hex="#8FA69C",
        accent_hex="#6AC49E",
        border_hex="#2C3B36",
    ),
}

SLIDE_LAYOUTS: tuple[tuple[str, str], ...] = (
    ("title", "Title Slide"),
    ("title_body", "Title & Content"),
    ("two_column", "Two Columns"),
    ("section", "Section Header"),
    ("quote", "Quote / Takeaway"),
)

_VALID_LAYOUT_IDS: frozenset[str] = frozenset(lid for lid, _ in SLIDE_LAYOUTS)


def get_slide_theme(theme_id: str) -> SlideThemePalette:
    """Resolve a slide theme ID to its SlideThemePalette (defaults to Snow & Lavender)."""
    return SLIDE_THEMES.get(theme_id.strip().lower(), SLIDE_THEMES["lavender_snow"])


def _new_slide_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(slots=True)
class SlideRecord:
    """Mutable state and typography/color overrides for a single slide."""

    slide_id: str = field(default_factory=_new_slide_id)
    layout: str = "title_body"
    title: str = ""
    subtitle: str = ""
    body_lines: list[str] = field(default_factory=list)
    right_lines: list[str] = field(default_factory=list)
    speaker_notes: str = ""

    # Per-slide typography & color overrides
    font_family: str = "Inter"
    title_size: int = 32
    body_size: int = 16
    text_align: str = "auto"  # 'auto' | 'left' | 'center' | 'right'
    bold: bool = False
    italic: bool = False
    underline: bool = False
    custom_bg_hex: str = ""
    custom_text_hex: str = ""
    custom_accent_hex: str = ""

    def __post_init__(self) -> None:
        if self.layout not in _VALID_LAYOUT_IDS:
            self.layout = "title_body"

    def clone(self) -> SlideRecord:
        """Return an independent deep copy of this slide with a fresh slide_id."""
        return SlideRecord(
            slide_id=_new_slide_id(),
            layout=self.layout,
            title=self.title,
            subtitle=self.subtitle,
            body_lines=list(self.body_lines),
            right_lines=list(self.right_lines),
            speaker_notes=self.speaker_notes,
            font_family=self.font_family,
            title_size=self.title_size,
            body_size=self.body_size,
            text_align=self.text_align,
            bold=self.bold,
            italic=self.italic,
            underline=self.underline,
            custom_bg_hex=self.custom_bg_hex,
            custom_text_hex=self.custom_text_hex,
            custom_accent_hex=self.custom_accent_hex,
        )


class PresentationModel:
    """Ordered collection of slides with deck-level theme and slide operations."""

    def __init__(
        self,
        slides: list[SlideRecord] | None = None,
        theme_id: str = "lavender_snow",
    ) -> None:
        self.theme_id: str = get_slide_theme(theme_id).theme_id
        self.slides: list[SlideRecord] = (
            slides
            if slides
            else [
                SlideRecord(
                    layout="title",
                    title="Presentation Title",
                    subtitle="Click anywhere on the slide to edit directly",
                )
            ]
        )
        self.active_index: int = 0

    @property
    def active_slide(self) -> SlideRecord:
        self.active_index = max(0, min(len(self.slides) - 1, self.active_index))
        return self.slides[self.active_index]

    @property
    def palette(self) -> SlideThemePalette:
        return get_slide_theme(self.theme_id)

    def set_active_index(self, index: int) -> SlideRecord:
        if not (0 <= index < len(self.slides)):
            raise IndexError(f"Slide index {index} out of range.")
        self.active_index = index
        return self.slides[index]

    def add_slide(
        self,
        layout: str = "title_body",
        insert_after_active: bool = True,
    ) -> SlideRecord:
        """Create and insert a new slide with starter placeholders."""
        clean_layout = layout if layout in _VALID_LAYOUT_IDS else "title_body"
        defaults: dict[str, tuple[str, str, list[str], list[str]]] = {
            "title": ("New Presentation Title", "Add a concise subtitle", [], []),
            "title_body": (
                "Slide Heading",
                "",
                ["First key point", "Second supporting detail", "Third takeaway"],
                [],
            ),
            "two_column": (
                "Comparison Overview",
                "",
                ["Left point one", "Left point two"],
                ["Right point one", "Right point two"],
            ),
            "section": ("Section Title", "Brief context for this section", [], []),
            "quote": (
                "“Simplicity is about subtracting the obvious and adding the meaningful.”",
                "— John Maeda",
                [],
                [],
            ),
        }
        title, subtitle, left_pts, right_pts = defaults[clean_layout]
        slide = SlideRecord(
            layout=clean_layout,
            title=title,
            subtitle=subtitle,
            body_lines=left_pts,
            right_lines=right_pts,
        )

        if insert_after_active and self.slides:
            insert_pos = min(len(self.slides), self.active_index + 1)
        else:
            insert_pos = len(self.slides)

        self.slides.insert(insert_pos, slide)
        self.active_index = insert_pos
        return slide

    def duplicate_slide(self, index: int | None = None) -> SlideRecord:
        """Duplicate the slide at `index` (defaults to active slide) right after it."""
        target_idx = self.active_index if index is None else index
        if not (0 <= target_idx < len(self.slides)):
            raise IndexError(f"Slide index {target_idx} out of range.")
        copy_slide = self.slides[target_idx].clone()
        insert_pos = target_idx + 1
        self.slides.insert(insert_pos, copy_slide)
        self.active_index = insert_pos
        return copy_slide

    def delete_slide(self, index: int | None = None) -> bool:
        """Delete slide at `index` while ensuring at least one slide always remains."""
        if len(self.slides) <= 1:
            return False
        target_idx = self.active_index if index is None else index
        if not (0 <= target_idx < len(self.slides)):
            return False
        self.slides.pop(target_idx)
        self.active_index = max(0, min(len(self.slides) - 1, self.active_index))
        return True

    def move_slide(self, from_index: int, to_index: int) -> bool:
        """Reorder a slide from `from_index` to `to_index`."""
        if not (0 <= from_index < len(self.slides) and 0 <= to_index < len(self.slides)):
            return False
        if from_index == to_index:
            return False
        slide = self.slides.pop(from_index)
        self.slides.insert(to_index, slide)
        self.active_index = to_index
        return True


# ----------------------------------------------------------------------
# Native PowerPoint OpenXML (.pptx) Export & Import
# ----------------------------------------------------------------------


def _ooxml_align(slide_align: str, default_code: str) -> str:
    mapping = {"left": "l", "center": "ctr", "right": "r"}
    return mapping.get(slide_align, default_code)


def _build_shape_xml(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    cx: int,
    cy: int,
    paragraphs: list[tuple[str, int, bool, bool, bool, str, str, str]],
    fill_hex: str | None = None,
) -> str:
    """Build a DrawingML/PresentationML `<p:sp>` text shape element."""
    if fill_hex:
        clean_fill = fill_hex.lstrip("#").upper()
        fill_xml = f'<a:solidFill><a:srgbClr val="{clean_fill}"/></a:solidFill>'
    else:
        fill_xml = "<a:noFill/>"

    para_parts: list[str] = []
    for text_line, font_pt, is_bold, is_italic, is_under, color_hex, align_code, font_name in paragraphs:
        sz_hundredths = max(1000, int(round(font_pt * 100)))
        bold_attr = ' b="1"' if is_bold else ""
        italic_attr = ' i="1"' if is_italic else ""
        under_attr = ' u="sng"' if is_under else ""
        clean_hex = color_hex.lstrip("#").upper()
        escaped_txt = xml_escape(text_line)
        escaped_font = xml_escape(font_name or "Calibri")
        para_parts.append(
            f'<a:p><a:pPr algn="{align_code}"/>'
            f'<a:r><a:rPr lang="en-US" sz="{sz_hundredths}"{bold_attr}{italic_attr}{under_attr}>'
            f'<a:solidFill><a:srgbClr val="{clean_hex}"/></a:solidFill>'
            f'<a:latin typeface="{escaped_font}"/>'
            f"</a:rPr><a:t>{escaped_txt}</a:t></a:r>"
            "</a:p>"
        )

    if not para_parts:
        para_parts.append("<a:p/>")

    return (
        "<p:sp>"
        "<p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{xml_escape(name)}"/>'
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
        "<p:nvPr/>"
        "</p:nvSpPr>"
        "<p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f"{fill_xml}"
        "</p:spPr>"
        "<p:txBody>"
        '<a:bodyPr wrap="square" lIns="120000" tIns="80000" rIns="120000" bIns="80000"/>'
        "<a:lstStyle/>"
        f"{''.join(para_parts)}"
        "</p:txBody>"
        "</p:sp>"
    )


def _build_slide_xml(slide: SlideRecord, palette: SlideThemePalette) -> str:
    """Serialize a single SlideRecord into a complete `ppt/slides/slideN.xml` document."""
    default_bg = palette.card_hex if slide.layout == "section" else palette.bg_hex
    bg_hex = slide.custom_bg_hex.strip() or default_bg
    clean_bg = bg_hex.lstrip("#").upper()

    title_color = slide.custom_text_hex.strip() or palette.title_hex
    body_color = slide.custom_text_hex.strip() or palette.body_hex
    accent_color = slide.custom_accent_hex.strip() or palette.accent_hex
    font_name = slide.font_family or "Inter"

    shapes: list[str] = []
    next_id = 2

    if slide.layout in {"title", "section"}:
        al = _ooxml_align(slide.text_align, "ctr")
        title_paras = [
            (
                slide.title or "Untitled Slide",
                max(24, slide.title_size + 6),
                True,
                slide.italic,
                slide.underline,
                title_color,
                al,
                font_name,
            )
        ]
        shapes.append(
            _build_shape_xml(
                shape_id=next_id,
                name="Title",
                x=914400,
                y=2100000,
                cx=10363200,
                cy=1350000,
                paragraphs=title_paras,
            )
        )
        next_id += 1

        if slide.subtitle.strip():
            sub_paras = [
                (
                    slide.subtitle.strip(),
                    max(14, slide.body_size + 2),
                    slide.bold,
                    slide.italic,
                    slide.underline,
                    palette.muted_hex,
                    al,
                    font_name,
                )
            ]
            shapes.append(
                _build_shape_xml(
                    shape_id=next_id,
                    name="Subtitle",
                    x=1524000,
                    y=3650000,
                    cx=9144000,
                    cy=950000,
                    paragraphs=sub_paras,
                )
            )
            next_id += 1

    elif slide.layout == "quote":
        al = _ooxml_align(slide.text_align, "ctr")
        quote_paras = [
            (
                slide.title or "",
                max(20, slide.title_size - 4),
                True,
                True,
                slide.underline,
                title_color,
                al,
                font_name,
            )
        ]
        shapes.append(
            _build_shape_xml(
                shape_id=next_id,
                name="Title",
                x=1143000,
                y=1950000,
                cx=9906000,
                cy=2100000,
                paragraphs=quote_paras,
                fill_hex=palette.card_hex,
            )
        )
        next_id += 1

        if slide.subtitle.strip():
            attr_paras = [
                (
                    slide.subtitle.strip(),
                    max(14, slide.body_size + 1),
                    True,
                    slide.italic,
                    slide.underline,
                    accent_color,
                    al,
                    font_name,
                )
            ]
            shapes.append(
                _build_shape_xml(
                    shape_id=next_id,
                    name="Subtitle",
                    x=1828800,
                    y=4300000,
                    cx=8534400,
                    cy=700000,
                    paragraphs=attr_paras,
                )
            )
            next_id += 1

    elif slide.layout == "two_column":
        al = _ooxml_align(slide.text_align, "l")
        title_paras = [
            (
                slide.title or "Untitled Slide",
                slide.title_size,
                True,
                slide.italic,
                slide.underline,
                title_color,
                al,
                font_name,
            )
        ]
        shapes.append(
            _build_shape_xml(
                shape_id=next_id,
                name="Title",
                x=762000,
                y=480000,
                cx=10668000,
                cy=850000,
                paragraphs=title_paras,
            )
        )
        next_id += 1

        left_paras = [
            (
                f"•  {line}",
                slide.body_size,
                slide.bold,
                slide.italic,
                slide.underline,
                body_color,
                al,
                font_name,
            )
            for line in slide.body_lines
            if line.strip()
        ]
        shapes.append(
            _build_shape_xml(
                shape_id=next_id,
                name="LeftColumn",
                x=762000,
                y=1550000,
                cx=5100000,
                cy=4650000,
                paragraphs=left_paras,
                fill_hex=palette.card_hex,
            )
        )
        next_id += 1

        right_paras = [
            (
                f"•  {line}",
                slide.body_size,
                slide.bold,
                slide.italic,
                slide.underline,
                body_color,
                al,
                font_name,
            )
            for line in slide.right_lines
            if line.strip()
        ]
        shapes.append(
            _build_shape_xml(
                shape_id=next_id,
                name="RightColumn",
                x=6330000,
                y=1550000,
                cx=5100000,
                cy=4650000,
                paragraphs=right_paras,
                fill_hex=palette.card_hex,
            )
        )
        next_id += 1

    else:  # title_body
        al = _ooxml_align(slide.text_align, "l")
        title_paras = [
            (
                slide.title or "Untitled Slide",
                slide.title_size,
                True,
                slide.italic,
                slide.underline,
                title_color,
                al,
                font_name,
            )
        ]
        shapes.append(
            _build_shape_xml(
                shape_id=next_id,
                name="Title",
                x=762000,
                y=480000,
                cx=10668000,
                cy=850000,
                paragraphs=title_paras,
            )
        )
        next_id += 1

        body_paras: list[tuple[str, int, bool, bool, bool, str, str, str]] = []
        if slide.subtitle.strip():
            body_paras.append(
                (
                    slide.subtitle.strip(),
                    max(13, slide.body_size - 1),
                    slide.bold,
                    True,
                    slide.underline,
                    palette.muted_hex,
                    al,
                    font_name,
                )
            )
        for line in slide.body_lines:
            if line.strip():
                body_paras.append(
                    (
                        f"•  {line.strip()}",
                        slide.body_size + 2,
                        slide.bold,
                        slide.italic,
                        slide.underline,
                        body_color,
                        al,
                        font_name,
                    )
                )

        shapes.append(
            _build_shape_xml(
                shape_id=next_id,
                name="BodyContent",
                x=762000,
                y=1550000,
                cx=10668000,
                cy=4650000,
                paragraphs=body_paras,
            )
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sld xmlns:a="{_A_NS}" xmlns:r="{_R_NS}" xmlns:p="{_P_NS}">'
        "<p:cSld>"
        "<p:bg><p:bgPr>"
        f'<a:solidFill><a:srgbClr val="{clean_bg}"/></a:solidFill>'
        "<a:effectLst/>"
        "</p:bgPr></p:bg>"
        "<p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f"{''.join(shapes)}"
        "</p:spTree>"
        "</p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
        "</p:sld>\n"
    )


def _build_notes_slide_xml(notes_text: str) -> str:
    """Build `ppt/notesSlides/notesSlideN.xml` preserving speaker notes."""
    paras = [
        (line, 12, False, False, False, "#35343B", "l", "Inter")
        for line in notes_text.replace("\r\n", "\n").split("\n")
        if line.strip()
    ]
    notes_shape = _build_shape_xml(
        shape_id=2,
        name="SpeakerNotes",
        x=685800,
        y=4343400,
        cx=5486400,
        cy=4114800,
        paragraphs=paras,
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:notes xmlns:a="{_A_NS}" xmlns:r="{_R_NS}" xmlns:p="{_P_NS}">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f"{notes_shape}"
        "</p:spTree></p:cSld>"
        "</p:notes>\n"
    )


def build_pptx_bytes(deck: PresentationModel) -> bytes:
    """Serialize a PresentationModel into a valid, multi-slide OpenXML .pptx archive."""
    palette = deck.palette
    slide_count = len(deck.slides)

    content_types_overrides: list[str] = [
        '<Override PartName="/ppt/presentation.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
    ]

    pres_rels: list[str] = [
        f'<Relationship Id="rId1" Type="{_R_NS}/slideMaster" Target="slideMasters/slideMaster1.xml"/>',
        f'<Relationship Id="rId2" Type="{_R_NS}/theme" Target="theme/theme1.xml"/>',
    ]
    sld_id_list: list[str] = []

    for idx in range(slide_count):
        s_num = idx + 1
        rid = f"rId{s_num + 2}"
        sld_id = 256 + idx
        sld_id_list.append(f'<p:sldId id="{sld_id}" r:id="{rid}"/>')
        pres_rels.append(
            f'<Relationship Id="{rid}" Type="{_R_NS}/slide" Target="slides/slide{s_num}.xml"/>'
        )
        content_types_overrides.append(
            f'<Override PartName="/ppt/slides/slide{s_num}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        )
        if deck.slides[idx].speaker_notes.strip():
            content_types_overrides.append(
                f'<Override PartName="/ppt/notesSlides/notesSlide{s_num}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
            )

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Types xmlns="{_CT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{''.join(content_types_overrides)}"
        "</Types>\n"
    )

    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_PR_NS}">'
        f'<Relationship Id="rId1" Type="{_R_NS}/officeDocument" Target="ppt/presentation.xml"/>'
        "</Relationships>\n"
    )

    presentation_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:presentation xmlns:a="{_A_NS}" xmlns:r="{_R_NS}" xmlns:p="{_P_NS}">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f"<p:sldIdLst>{''.join(sld_id_list)}</p:sldIdLst>"
        f'<p:sldSz cx="{_SLIDE_CX}" cy="{_SLIDE_CY}" type="screen16x9"/>'
        '<p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>\n"
    )

    presentation_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_PR_NS}">{"".join(pres_rels)}</Relationships>\n'
    )

    slide_master_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sldMaster xmlns:a="{_A_NS}" xmlns:r="{_R_NS}" xmlns:p="{_P_NS}">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        "</p:spTree></p:cSld>"
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        "</p:sldMaster>\n"
    )

    slide_master_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_PR_NS}">'
        f'<Relationship Id="rId1" Type="{_R_NS}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        f'<Relationship Id="rId2" Type="{_R_NS}/theme" Target="../theme/theme1.xml"/>'
        "</Relationships>\n"
    )

    slide_layout_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<p:sldLayout xmlns:a="{_A_NS}" xmlns:r="{_R_NS}" xmlns:p="{_P_NS}" type="blank">'
        "<p:cSld><p:spTree>"
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        "</p:spTree></p:cSld>"
        "</p:sldLayout>\n"
    )

    slide_layout_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_PR_NS}">'
        f'<Relationship Id="rId1" Type="{_R_NS}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
        "</Relationships>\n"
    )

    clean_accent = palette.accent_hex.lstrip("#").upper()
    theme_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<a:theme xmlns:a="{_A_NS}" name="{xml_escape(palette.display_name)}">'
        "<a:themeElements>"
        '<a:clrScheme name="Pastel">'
        '<a:dk1><a:srgbClr val="35343B"/></a:dk1>'
        '<a:lt1><a:srgbClr val="FFFEFC"/></a:lt1>'
        '<a:dk2><a:srgbClr val="4A4852"/></a:dk2>'
        '<a:lt2><a:srgbClr val="F8F7F4"/></a:lt2>'
        f'<a:accent1><a:srgbClr val="{clean_accent}"/></a:accent1>'
        '<a:accent2><a:srgbClr val="477A63"/></a:accent2>'
        '<a:accent3><a:srgbClr val="BA5B6E"/></a:accent3>'
        '<a:accent4><a:srgbClr val="3E6FA8"/></a:accent4>'
        '<a:accent5><a:srgbClr val="8E6828"/></a:accent5>'
        '<a:accent6><a:srgbClr val="8B78E6"/></a:accent6>'
        f'<a:hlink><a:srgbClr val="{clean_accent}"/></a:hlink>'
        '<a:folHlink><a:srgbClr val="77747E"/></a:folHlink>'
        "</a:clrScheme>"
        '<a:fontScheme name="PastelFont">'
        '<a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
        "</a:fontScheme>"
        '<a:fmtScheme name="PastelFmt">'
        '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
        '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
        '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle>"
        "<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
        '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
        "</a:fmtScheme>"
        "</a:themeElements>"
        "</a:theme>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("ppt/presentation.xml", presentation_xml)
        zf.writestr("ppt/_rels/presentation.xml.rels", presentation_rels_xml)
        zf.writestr("ppt/slideMasters/slideMaster1.xml", slide_master_xml)
        zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", slide_master_rels_xml)
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", slide_layout_xml)
        zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", slide_layout_rels_xml)
        zf.writestr("ppt/theme/theme1.xml", theme_xml)

        for idx, slide in enumerate(deck.slides):
            s_num = idx + 1
            zf.writestr(f"ppt/slides/slide{s_num}.xml", _build_slide_xml(slide, palette))
            has_notes = bool(slide.speaker_notes.strip())
            notes_rel = (
                f'<Relationship Id="rId2" Type="{_R_NS}/notesSlide" '
                f'Target="../notesSlides/notesSlide{s_num}.xml"/>'
                if has_notes
                else ""
            )
            slide_rels_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f'<Relationships xmlns="{_PR_NS}">'
                f'<Relationship Id="rId1" Type="{_R_NS}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                f"{notes_rel}"
                "</Relationships>\n"
            )
            zf.writestr(f"ppt/slides/_rels/slide{s_num}.xml.rels", slide_rels_xml)
            if has_notes:
                zf.writestr(
                    f"ppt/notesSlides/notesSlide{s_num}.xml",
                    _build_notes_slide_xml(slide.speaker_notes),
                )

    return buf.getvalue()


def save_presentation(destination: str | Path, deck: PresentationModel) -> Path:
    """Atomically save `deck` to a .pptx file."""
    target = Path(destination)
    if not target.suffix:
        target = target.with_suffix(DEFAULT_PRESENTATION_EXTENSION)
    if target.suffix.lower() != ".pptx":
        raise FileOperationError(
            f"Unsupported presentation extension '{target.suffix}'. Expected .pptx."
        )
    return atomic_write_bytes(target, build_pptx_bytes(deck), backup_app_id=APP_ID)


def load_presentation(source_path: str | Path) -> PresentationModel:
    """Validate and load a .pptx archive into a hydrated PresentationModel."""
    validated = validate_input_file(
        source_path,
        allowed_extensions=SUPPORTED_OPEN_EXTENSIONS,
        max_size_mb=40.0,
    )

    try:
        with zipfile.ZipFile(validated, mode="r") as zf:
            names = set(zf.namelist())
            if "ppt/presentation.xml" not in names:
                raise FileOperationError(f"'{validated.name}' is missing 'ppt/presentation.xml'.")

            rid_to_slide: dict[str, str] = {}
            if "ppt/_rels/presentation.xml.rels" in names:
                rels_root = ET.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
                for rel in rels_root.findall(f"{_PR}Relationship"):
                    rid = rel.attrib.get("Id", "")
                    target = rel.attrib.get("Target", "").lstrip("/")
                    if not target.startswith("ppt/"):
                        target = f"ppt/{target}"
                    rid_to_slide[rid] = target

            pres_root = ET.fromstring(zf.read("ppt/presentation.xml"))
            sld_ids = pres_root.findall(f".//{_P}sldIdLst/{_P}sldId")
            slide_paths: list[str] = []
            for s_el in sld_ids:
                rid = s_el.attrib.get(f"{_R}id", "")
                if rid in rid_to_slide:
                    slide_paths.append(rid_to_slide[rid])

            if not slide_paths:
                slide_paths = sorted(
                    n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                )

            detected_theme = "lavender_snow"
            if "ppt/theme/theme1.xml" in names:
                th_root = ET.fromstring(zf.read("ppt/theme/theme1.xml"))
                th_name = th_root.attrib.get("name", "")
                for tid, pal in SLIDE_THEMES.items():
                    if pal.display_name.lower() == th_name.lower():
                        detected_theme = tid
                        break

            loaded_slides: list[SlideRecord] = []
            for idx, s_path in enumerate(slide_paths):
                if s_path not in names:
                    continue
                s_root = ET.fromstring(zf.read(s_path))

                bg_srgb = s_root.find(f".//{_P}bg//{_A}srgbClr")
                if bg_srgb is not None:
                    bg_val = f"#{bg_srgb.attrib.get('val', '').upper()}"
                    if bg_val == "#1D1D23":
                        detected_theme = "midnight_velvet"
                    elif bg_val == "#19201E":
                        detected_theme = "slate_emerald"

                shapes_by_name: dict[str, list[str]] = {}
                ordered_blocks: list[list[str]] = []

                for sp in s_root.findall(f".//{_P}spTree/{_P}sp"):
                    cnv = sp.find(f".//{_P}nvSpPr/{_P}cNvPr")
                    sp_name = cnv.attrib.get("name", "") if cnv is not None else ""
                    paras: list[str] = []
                    for p_el in sp.findall(f".//{_P}txBody/{_A}p"):
                        runs = [t.text or "" for t in p_el.findall(f".//{_A}t")]
                        line_txt = "".join(runs).strip()
                        if line_txt:
                            paras.append(line_txt)
                    if paras:
                        shapes_by_name[sp_name] = paras
                        ordered_blocks.append(paras)

                title_txt = ""
                subtitle_txt = ""
                left_lines: list[str] = []
                right_lines: list[str] = []
                layout_id = "title_body"

                if "RightColumn" in shapes_by_name:
                    layout_id = "two_column"
                    title_txt = " ".join(shapes_by_name.get("Title", [""]))
                    left_lines = [
                        ln.lstrip("•").strip() for ln in shapes_by_name.get("LeftColumn", [])
                    ]
                    right_lines = [
                        ln.lstrip("•").strip() for ln in shapes_by_name.get("RightColumn", [])
                    ]
                elif "Title" in shapes_by_name and "BodyContent" in shapes_by_name:
                    layout_id = "title_body"
                    title_txt = " ".join(shapes_by_name["Title"])
                    left_lines = [
                        ln.lstrip("•").strip() for ln in shapes_by_name["BodyContent"]
                    ]
                elif "Title" in shapes_by_name:
                    title_txt = " ".join(shapes_by_name["Title"])
                    subtitle_txt = " ".join(shapes_by_name.get("Subtitle", []))
                    if title_txt.startswith("“") or title_txt.startswith('"'):
                        layout_id = "quote"
                    elif idx == 0:
                        layout_id = "title"
                    else:
                        layout_id = "section"
                elif ordered_blocks:
                    title_txt = " ".join(ordered_blocks[0])
                    for blk in ordered_blocks[1:]:
                        left_lines.extend(ln.lstrip("•").strip() for ln in blk)

                notes_txt = ""
                notes_candidate = f"ppt/notesSlides/notesSlide{idx + 1}.xml"
                if notes_candidate in names:
                    n_root = ET.fromstring(zf.read(notes_candidate))
                    n_lines = [
                        "".join(t.text or "" for t in p_el.findall(f".//{_A}t")).strip()
                        for p_el in n_root.findall(f".//{_P}txBody/{_A}p")
                    ]
                    notes_txt = "\n".join(ln for ln in n_lines if ln)

                loaded_slides.append(
                    SlideRecord(
                        layout=layout_id,
                        title=title_txt,
                        subtitle=subtitle_txt,
                        body_lines=left_lines,
                        right_lines=right_lines,
                        speaker_notes=notes_txt,
                    )
                )

            return PresentationModel(slides=loaded_slides or None, theme_id=detected_theme)
    except zipfile.BadZipFile as exc:
        raise FileOperationError(f"'{validated.name}' is not a valid .pptx presentation.") from exc
    except ET.ParseError as exc:
        raise FileOperationError(f"Corrupt XML inside '{validated.name}': {exc}") from exc