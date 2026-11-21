"""Handcrafted design tokens (Paperism & Obsidian Ink palettes) for Jaiho Super Simple Office."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Complete set of semantic design tokens injected into Qt Style Sheets."""

    name: str
    is_dark: bool

    # Surfaces (Layered depth hierarchy)
    bg_canvas: str
    bg_surface: str
    bg_elevated: str
    bg_hover: str
    bg_selected: str
    bg_recessed: str

    # Hairline Borders
    border_subtle: str
    border_strong: str

    # Typography Ink Colors
    text_primary: str
    text_secondary: str
    text_muted: str

    # Brand Accent (Prussian Sapphire / Luminous Cobalt)
    accent_primary: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_subtle: str

    # Semantic Status Colors
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str

    # Scrollbars
    scrollbar_thumb: str
    scrollbar_thumb_hover: str

    # Typography Scale
    font_family: str = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
    font_mono: str = '"Cascadia Code", "JetBrains Mono", "Consolas", monospace'
    font_size_xs: str = "11px"
    font_size_sm: str = "12px"
    font_size_base: str = "13px"
    font_size_md: str = "15px"
    font_size_lg: str = "19px"
    font_size_xl: str = "24px"

    # Tactile Geometry
    radius_sm: str = "5px"
    radius_md: str = "8px"
    spacing_xs: str = "4px"
    spacing_sm: str = "8px"
    spacing_md: str = "12px"
    spacing_lg: str = "16px"
    spacing_xl: str = "24px"

    def to_template_vars(self) -> dict[str, str]:
        """Return string-only key/value pairs suitable for string.Template substitution."""
        raw = asdict(self)
        return {key: str(value) for key, value in raw.items() if isinstance(value, str)}


LIGHT_THEME = ThemeTokens(
    name="light",
    is_dark=False,
    bg_canvas="#F6F5F0",      # Warm archival paper canvas
    bg_surface="#FFFFFF",     # Crisp sheet surface
    bg_elevated="#EDEBE4",    # Tactile warm stone keycap / header
    bg_hover="#E4E1D8",       # Subtle pressed-paper hover
    bg_selected="#E7EFFD",    # Soft sapphire selection wash
    bg_recessed="#F0EFE9",    # Recessed LCD / readout well
    border_subtle="#DFDDD4",  # Hairline warm rule
    border_strong="#C3C0B4",  # Crisp control border
    text_primary="#191918",   # Deep carbon ink
    text_secondary="#52514C", # Graphite secondary ink
    text_muted="#83817A",     # Muted pencil annotation
    accent_primary="#1B56C6", # Prussian sapphire
    accent_hover="#1546A3",
    accent_pressed="#103782",
    accent_text="#FFFFFF",
    accent_subtle="#DCE7FC",
    success="#157347",
    success_bg="#E6F4ED",
    warning="#9C5700",
    warning_bg="#FDF3E3",
    danger="#BE2A2A",
    danger_bg="#FDECEC",
    scrollbar_thumb="#CBC8BC",
    scrollbar_thumb_hover="#A6A294",
)

DARK_THEME = ThemeTokens(
    name="dark",
    is_dark=True,
    bg_canvas="#111316",      # Deep obsidian ink canvas
    bg_surface="#181B20",     # Elevated charcoal sheet
    bg_elevated="#21252C",    # Tactile keycap / header surface
    bg_hover="#2A2F38",       # Refined slate hover
    bg_selected="#1E304F",    # Deep sapphire selection wash
    bg_recessed="#13161A",    # Recessed dark readout well
    border_subtle="#292E37",  # Hairline slate rule
    border_strong="#3D4452",  # Crisp control border
    text_primary="#ECEBE6",   # Warm ivory ink
    text_secondary="#A8A7A0", # Muted silver-stone ink
    text_muted="#73736D",     # Subtle slate annotation
    accent_primary="#5892F8", # Luminous cobalt
    accent_hover="#74A5FA",
    accent_pressed="#3F7CEB",
    accent_text="#0D121A",
    accent_subtle="#1D2E4A",
    success="#3DB87A",
    success_bg="#153024",
    warning="#E3A539",
    warning_bg="#362813",
    danger="#EE6363",
    danger_bg="#38191C",
    scrollbar_thumb="#363D49",
    scrollbar_thumb_hover="#4E5768",
)

THEMES: dict[str, ThemeTokens] = {
    "light": LIGHT_THEME,
    "dark": DARK_THEME,
}


def get_theme_tokens(theme_name: str) -> ThemeTokens:
    """Return the ThemeTokens object for 'light' or 'dark' (defaults to LIGHT_THEME)."""
    return THEMES.get(theme_name.strip().lower(), LIGHT_THEME)