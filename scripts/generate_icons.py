"""Generate handcrafted Swiss-editorial SVG and multi-resolution ICO icons for all suite apps."""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow headless execution in CI or terminal environments without display popups
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT_DIR = Path(__file__).resolve().parents[1]
ICONS_DIR = ROOT_DIR / "resources" / "icons"

# Standard Windows ICO resolutions (256px uses PNG compression inside ICO per Vista+ spec)
ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

# Archival Paper & Prussian Sapphire design tokens
PAPER_BG = "#F6F5F0"
PAPER_HIGHLIGHT = "#FFFFFF"
STONE_BORDER = "#C3C0B4"
STONE_SUBTLE = "#DFDDD4"
CARBON_INK = "#191918"
GRAPHITE_INK = "#52514C"
SAPPHIRE = "#1B56C6"
SAPPHIRE_SOFT = "#DCE7FC"


@dataclass(frozen=True, slots=True)
class IconSpec:
    """Visual specification for a single application icon."""

    app_id: str
    monogram: str
    glyph_svg: str


def _build_icon_specs() -> tuple[IconSpec, ...]:
    """Define bespoke geometric glyphs and corner monograms for all suite executables."""
    return (
        IconSpec(
            app_id="jaiho_office",
            monogram="JHO",
            glyph_svg=f"""
            <rect x="64" y="88" width="36" height="36" rx="7" fill="{CARBON_INK}"/>
            <rect x="110" y="88" width="36" height="36" rx="7" fill="{CARBON_INK}"/>
            <rect x="156" y="88" width="36" height="36" rx="7" fill="{SAPPHIRE}"/>
            <rect x="64" y="134" width="36" height="36" rx="7" fill="{CARBON_INK}"/>
            <rect x="110" y="134" width="36" height="36" rx="7" fill="{SAPPHIRE}"/>
            <rect x="156" y="134" width="36" height="36" rx="7" fill="{CARBON_INK}"/>
            <rect x="64" y="180" width="82" height="24" rx="6" fill="{GRAPHITE_INK}"/>
            <rect x="156" y="180" width="36" height="24" rx="6" fill="{CARBON_INK}"/>
            """,
        ),
        IconSpec(
            app_id="simple_calculator",
            monogram="JSC",
            glyph_svg=f"""
            <rect x="64" y="84" width="128" height="28" rx="6" fill="{SAPPHIRE_SOFT}" stroke="{CARBON_INK}" stroke-width="4"/>
            <line x1="146" y1="98" x2="180" y2="98" stroke="{SAPPHIRE}" stroke-width="5" stroke-linecap="round"/>
            <circle cx="82" cy="134" r="9" fill="{CARBON_INK}"/>
            <circle cx="114" cy="134" r="9" fill="{CARBON_INK}"/>
            <circle cx="146" cy="134" r="9" fill="{CARBON_INK}"/>
            <circle cx="178" cy="134" r="9" fill="{GRAPHITE_INK}"/>
            <circle cx="82" cy="164" r="9" fill="{CARBON_INK}"/>
            <circle cx="114" cy="164" r="9" fill="{CARBON_INK}"/>
            <circle cx="146" cy="164" r="9" fill="{CARBON_INK}"/>
            <circle cx="178" cy="164" r="9" fill="{GRAPHITE_INK}"/>
            <circle cx="82" cy="194" r="9" fill="{CARBON_INK}"/>
            <circle cx="114" cy="194" r="9" fill="{CARBON_INK}"/>
            <rect x="137" y="184" width="52" height="20" rx="6" fill="{SAPPHIRE}"/>
            """,
        ),
        IconSpec(
            app_id="simple_units",
            monogram="JSU",
            glyph_svg=f"""
            <rect x="60" y="92" width="136" height="34" rx="7" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <line x1="84" y1="92" x2="84" y2="108" stroke="{CARBON_INK}" stroke-width="4"/>
            <line x1="106" y1="92" x2="106" y2="114" stroke="{CARBON_INK}" stroke-width="4"/>
            <line x1="128" y1="92" x2="128" y2="108" stroke="{CARBON_INK}" stroke-width="4"/>
            <line x1="150" y1="92" x2="150" y2="114" stroke="{CARBON_INK}" stroke-width="4"/>
            <line x1="172" y1="92" x2="172" y2="108" stroke="{CARBON_INK}" stroke-width="4"/>
            <path d="M 68 154 H 184 L 166 138" fill="none" stroke="{SAPPHIRE}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 188 186 H 72 L 90 202" fill="none" stroke="{CARBON_INK}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
            """,
        ),
        IconSpec(
            app_id="simple_notes",
            monogram="JSN",
            glyph_svg=f"""
            <rect x="68" y="84" width="120" height="126" rx="10" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <line x1="88" y1="114" x2="168" y2="114" stroke="{SAPPHIRE}" stroke-width="6" stroke-linecap="round"/>
            <line x1="88" y1="140" x2="168" y2="140" stroke="{CARBON_INK}" stroke-width="5" stroke-linecap="round"/>
            <line x1="88" y1="164" x2="152" y2="164" stroke="{GRAPHITE_INK}" stroke-width="5" stroke-linecap="round"/>
            <line x1="88" y1="188" x2="132" y2="188" stroke="{GRAPHITE_INK}" stroke-width="5" stroke-linecap="round"/>
            """,
        ),
        IconSpec(
            app_id="simple_focus",
            monogram="JSF",
            glyph_svg=f"""
            <rect x="114" y="76" width="28" height="10" rx="4" fill="{CARBON_INK}"/>
            <circle cx="128" cy="148" r="60" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="6"/>
            <path d="M 128 148 L 128 100 A 48 48 0 0 1 174 162 Z" fill="{SAPPHIRE_SOFT}"/>
            <line x1="128" y1="148" x2="128" y2="104" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round"/>
            <line x1="128" y1="148" x2="162" y2="160" stroke="{SAPPHIRE}" stroke-width="6" stroke-linecap="round"/>
            <circle cx="128" cy="148" r="7" fill="{CARBON_INK}"/>
            """,
        ),
        IconSpec(
            app_id="simple_tasks",
            monogram="JST",
            glyph_svg=f"""
            <rect x="64" y="92" width="32" height="32" rx="7" fill="{SAPPHIRE}"/>
            <path d="M 72 108 L 78 115 L 90 101" fill="none" stroke="{PAPER_HIGHLIGHT}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
            <line x1="110" y1="108" x2="190" y2="108" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round"/>
            <rect x="64" y="138" width="32" height="32" rx="7" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="4.5"/>
            <line x1="110" y1="154" x2="182" y2="154" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round"/>
            <rect x="64" y="182" width="32" height="32" rx="7" fill="{PAPER_HIGHLIGHT}" stroke="{GRAPHITE_INK}" stroke-width="4.5"/>
            <line x1="110" y1="198" x2="166" y2="198" stroke="{GRAPHITE_INK}" stroke-width="6" stroke-linecap="round"/>
            """,
        ),
        IconSpec(
            app_id="simple_calendar",
            monogram="JSCa",
            glyph_svg=f"""
            <rect x="66" y="92" width="124" height="116" rx="10" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <path d="M 66 102 Q 66 92 76 92 H 180 Q 190 92 190 102 V 122 H 66 Z" fill="{SAPPHIRE}"/>
            <line x1="98" y1="80" x2="98" y2="102" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round"/>
            <line x1="158" y1="80" x2="158" y2="102" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round"/>
            <rect x="86" y="138" width="22" height="22" rx="4" fill="{STONE_SUBTLE}"/>
            <rect x="117" y="138" width="22" height="22" rx="4" fill="{SAPPHIRE}"/>
            <rect x="148" y="138" width="22" height="22" rx="4" fill="{STONE_SUBTLE}"/>
            <rect x="86" y="170" width="22" height="22" rx="4" fill="{STONE_SUBTLE}"/>
            <rect x="117" y="170" width="22" height="22" rx="4" fill="{STONE_SUBTLE}"/>
            """,
        ),
        IconSpec(
            app_id="simple_writer",
            monogram="JSW",
            glyph_svg=f"""
            <rect x="68" y="84" width="120" height="126" rx="10" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <rect x="88" y="106" width="54" height="10" rx="4" fill="{SAPPHIRE}"/>
            <line x1="88" y1="134" x2="168" y2="134" stroke="{CARBON_INK}" stroke-width="5" stroke-linecap="round"/>
            <line x1="88" y1="156" x2="168" y2="156" stroke="{CARBON_INK}" stroke-width="5" stroke-linecap="round"/>
            <line x1="88" y1="178" x2="144" y2="178" stroke="{GRAPHITE_INK}" stroke-width="5" stroke-linecap="round"/>
            <line x1="156" y1="170" x2="156" y2="192" stroke="{SAPPHIRE}" stroke-width="5" stroke-linecap="round"/>
            """,
        ),
        IconSpec(
            app_id="simple_sheets",
            monogram="JSS",
            glyph_svg=f"""
            <rect x="62" y="88" width="132" height="118" rx="9" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <path d="M 62 97 Q 62 88 71 88 H 185 Q 194 88 194 97 V 118 H 62 Z" fill="{SAPPHIRE}"/>
            <line x1="62" y1="148" x2="194" y2="148" stroke="{CARBON_INK}" stroke-width="4"/>
            <line x1="62" y1="178" x2="194" y2="178" stroke="{CARBON_INK}" stroke-width="4"/>
            <line x1="106" y1="88" x2="106" y2="206" stroke="{CARBON_INK}" stroke-width="4"/>
            <line x1="150" y1="88" x2="150" y2="206" stroke="{CARBON_INK}" stroke-width="4"/>
            """,
        ),
        IconSpec(
            app_id="simple_slides",
            monogram="JSSl",
            glyph_svg=f"""
            <rect x="58" y="92" width="140" height="92" rx="9" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <rect x="76" y="110" width="48" height="56" rx="5" fill="{SAPPHIRE}"/>
            <line x1="138" y1="120" x2="180" y2="120" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round"/>
            <line x1="138" y1="140" x2="174" y2="140" stroke="{GRAPHITE_INK}" stroke-width="5" stroke-linecap="round"/>
            <line x1="138" y1="158" x2="166" y2="158" stroke="{GRAPHITE_INK}" stroke-width="5" stroke-linecap="round"/>
            <line x1="128" y1="184" x2="128" y2="210" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round"/>
            <line x1="98" y1="210" x2="158" y2="210" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round"/>
            """,
        ),
        IconSpec(
            app_id="simple_pdf",
            monogram="JSP",
            glyph_svg=f"""
            <rect x="68" y="84" width="120" height="126" rx="10" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <rect x="58" y="124" width="140" height="44" rx="7" fill="{SAPPHIRE}"/>
            <text x="128" y="154" text-anchor="middle" fill="{PAPER_HIGHLIGHT}"
                  font-family="Segoe UI, Inter, Arial, sans-serif" font-size="26" font-weight="700" letter-spacing="2">PDF</text>
            """,
        ),
        IconSpec(
            app_id="simple_scan",
            monogram="JSSc",
            glyph_svg=f"""
            <rect x="72" y="84" width="112" height="126" rx="9" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <line x1="92" y1="112" x2="164" y2="112" stroke="{STONE_BORDER}" stroke-width="5" stroke-linecap="round"/>
            <line x1="92" y1="182" x2="152" y2="182" stroke="{STONE_BORDER}" stroke-width="5" stroke-linecap="round"/>
            <rect x="56" y="138" width="144" height="18" rx="9" fill="{SAPPHIRE_SOFT}"/>
            <line x1="54" y1="147" x2="202" y2="147" stroke="{SAPPHIRE}" stroke-width="6" stroke-linecap="round"/>
            """,
        ),
        IconSpec(
            app_id="simple_convert",
            monogram="JSCv",
            glyph_svg=f"""
            <rect x="62" y="88" width="64" height="72" rx="8" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="5"/>
            <rect x="130" y="134" width="64" height="72" rx="8" fill="{SAPPHIRE_SOFT}" stroke="{SAPPHIRE}" stroke-width="5"/>
            <path d="M 142 96 H 176 V 122" fill="none" stroke="{SAPPHIRE}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 114 198 H 80 V 172" fill="none" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
            """,
        ),
        IconSpec(
            app_id="simple_ocr",
            monogram="JSO",
            glyph_svg=f"""
            <path d="M 66 114 V 92 H 88" fill="none" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 168 92 H 190 V 114" fill="none" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 66 180 V 202 H 88" fill="none" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 168 202 H 190 V 180" fill="none" stroke="{CARBON_INK}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M 102 184 L 128 110 L 154 184" fill="none" stroke="{CARBON_INK}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
            <line x1="109" y1="162" x2="147" y2="162" stroke="{SAPPHIRE}" stroke-width="7" stroke-linecap="round"/>
            """,
        ),
        IconSpec(
            app_id="foundation_demo",
            monogram="JFD",
            glyph_svg=f"""
            <polygon points="128,84 186,116 128,148 70,116" fill="{SAPPHIRE}" stroke="{CARBON_INK}" stroke-width="4" stroke-linejoin="round"/>
            <polygon points="70,116 128,148 128,208 70,176" fill="{PAPER_HIGHLIGHT}" stroke="{CARBON_INK}" stroke-width="4" stroke-linejoin="round"/>
            <polygon points="186,116 128,148 128,208 186,176" fill="{STONE_SUBTLE}" stroke="{CARBON_INK}" stroke-width="4" stroke-linejoin="round"/>
            """,
        ),
    )


def render_svg_document(spec: IconSpec) -> str:
    """Compose a complete 256x256 Swiss-editorial squircle SVG icon for `spec`."""
    pill_width = 58 + max(0, len(spec.monogram) - 3) * 12
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <!-- Outer Archival Paper Squircle Tile -->
  <rect x="14" y="14" width="228" height="228" rx="46" fill="{PAPER_BG}" stroke="{STONE_BORDER}" stroke-width="6"/>
  <rect x="20" y="20" width="216" height="216" rx="41" fill="none" stroke="{PAPER_HIGHLIGHT}" stroke-width="3"/>

  <!-- Top-Left Swiss Monogram Badge -->
  <rect x="36" y="34" width="{pill_width}" height="32" rx="8" fill="{CARBON_INK}"/>
  <text x="{36 + pill_width / 2:.1f}" y="55" text-anchor="middle" fill="{PAPER_BG}"
        font-family="Cascadia Code, Consolas, monospace" font-size="16" font-weight="700" letter-spacing="1">{spec.monogram}</text>

  <!-- Top-Right Prussian Sapphire Precision Indicator -->
  <circle cx="208" cy="50" r="8" fill="{SAPPHIRE}"/>

  <!-- Application Geometric Glyph -->
  {spec.glyph_svg.strip()}
</svg>
"""


def _svg_to_png_bytes(svg_bytes: bytes, size: int) -> bytes:
    """Rasterize SVG bytes into a crisp RGBA PNG buffer of `size x size` pixels."""
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    if not renderer.isValid():
        raise RuntimeError("Generated SVG document failed QSvgRenderer validation.")

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0.0, 0.0, float(size), float(size)))
    painter.end()

    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Failed to encode {size}x{size} PNG layer.")
    buffer.close()
    return bytes(byte_array.data())


def write_multi_size_ico(svg_bytes: bytes, ico_path: Path, sizes: tuple[int, ...] = ICO_SIZES) -> None:
    """Write a multi-resolution Windows .ico file containing PNG-encoded RGBA layers."""
    png_layers: list[tuple[int, bytes]] = [
        (size, _svg_to_png_bytes(svg_bytes, size)) for size in sizes
    ]

    # ICONDIR header: reserved (0), type (1 = icon), count
    header = struct.pack("<HHH", 0, 1, len(png_layers))
    directory_entries: list[bytes] = []
    offset = 6 + (16 * len(png_layers))

    for size, png_data in png_layers:
        dim_byte = 0 if size >= 256 else size
        entry = struct.pack(
            "<BBBBHHII",
            dim_byte,       # bWidth (0 means 256)
            dim_byte,       # bHeight (0 means 256)
            0,              # bColorCount
            0,              # bReserved
            1,              # wPlanes
            32,             # wBitCount (32-bit RGBA)
            len(png_data),  # dwBytesInRes
            offset,         # dwImageOffset
        )
        directory_entries.append(entry)
        offset += len(png_data)

    ico_path.parent.mkdir(parents=True, exist_ok=True)
    with ico_path.open("wb") as fh:
        fh.write(header)
        for entry in directory_entries:
            fh.write(entry)
        for _, png_data in png_layers:
            fh.write(png_data)


def generate_all_icons(output_dir: Path = ICONS_DIR) -> list[tuple[Path, Path]]:
    """Generate .svg and .ico files for all registered applications."""
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    _ = app

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[tuple[Path, Path]] = []

    for spec in _build_icon_specs():
        svg_text = render_svg_document(spec)
        svg_bytes = svg_text.encode("utf-8")

        svg_path = output_dir / f"{spec.app_id}.svg"
        ico_path = output_dir / f"{spec.app_id}.ico"

        svg_path.write_text(svg_text, encoding="utf-8")
        write_multi_size_ico(svg_bytes, ico_path)
        generated.append((svg_path, ico_path))

    return generated


def main() -> int:
    pairs = generate_all_icons()
    print(f"Generated {len(pairs)} SVG + multi-resolution ICO icon pairs in '{ICONS_DIR}':")
    for svg_path, ico_path in pairs:
        print(f"  - {svg_path.name:<24} & {ico_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())