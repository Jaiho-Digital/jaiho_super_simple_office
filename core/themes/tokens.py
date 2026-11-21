"""Soft Modern Pastel design tokens and 4 built-in themes for Jaiho Super Simple Office."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

# ----------------------------------------------------------------------
# Canonical Pastel & Warm Neutral Palette Constants
# ----------------------------------------------------------------------
PASTEL_LAVENDER = "#E8E0F7"
PASTEL_BLUSH = "#F7DFE7"
PASTEL_SAGE = "#DDEFE8"
PASTEL_POWDER_BLUE = "#DFEAF8"
PASTEL_BUTTER = "#F8EBCF"
PASTEL_PEACH = "#F4E5D9"
PASTEL_WARM_CREAM = "#F5F0E8"
PASTEL_MIST = "#E8E8ED"

NEUTRAL_SNOW = "#FFFEFC"
NEUTRAL_CANVAS = "#F8F7F4"
NEUTRAL_BORDER = "#E9E7E3"
NEUTRAL_CHARCOAL = "#35343B"
NEUTRAL_MUTED = "#77747E"


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Complete set of semantic design tokens injected into Qt Style Sheets."""

    theme_id: str
    display_name: str
    name: str  # 'light' or 'dark' compatibility flag
    is_dark: bool

    # Surfaces (Layered depth hierarchy)
    bg_canvas: str
    bg_surface: str
    bg_elevated: str
    bg_hover: str
    bg_selected: str
    bg_recessed: str

    # Delicate Hairline Borders
    border_subtle: str
    border_strong: str

    # Typography Ink Colors
    text_primary: str
    text_secondary: str
    text_muted: str

    # Primary Accent & Pastel Tint
    accent_primary: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_subtle: str
    accent_soft_border: str

    # Supporting Pastel Accents (for restrained semantic highlights)
    pastel_lavender: str
    pastel_blush: str
    pastel_sage: str
    pastel_blue: str
    pastel_butter: str
    pastel_peach: str

    # Semantic Status Colors
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str

    # Minimal Scrollbars
    scrollbar_thumb: str
    scrollbar_thumb_hover: str

    # Typography Stack (Inter / Manrope with clean Windows variable fallback)
    font_family: str = '"Inter", "Manrope", "Segoe UI Variable Text", "Segoe UI", sans-serif'
    font_mono: str = '"JetBrains Mono", "Cascadia Code", "Consolas", monospace'
    font_size_xs: str = "11px"
    font_size_sm: str = "12px"
    font_size_base: str = "13px"
    font_size_md: str = "15px"
    font_size_lg: str = "19px"
    font_size_xl: str = "24px"

    # Restrained Modern Corner Radii (10–16px) & Generous Spacing
    radius_sm: str = "10px"
    radius_md: str = "14px"
    radius_lg: str = "16px"
    spacing_xs: str = "6px"
    spacing_sm: str = "10px"
    spacing_md: str = "14px"
    spacing_lg: str = "20px"
    spacing_xl: str = "28px"

    def to_template_vars(self) -> dict[str, str]:
        """Return string-only key/value pairs suitable for string.Template substitution."""
        raw = asdict(self)
        return {key: str(value) for key, value in raw.items() if isinstance(value, str)}

    def with_custom_accent(self, hex_color: str) -> ThemeTokens:
        """Return a copy of this theme with a custom primary accent color and derived states."""
        cleaned = hex_color.strip()
        if not cleaned.startswith("#") or len(cleaned) != 7:
            return self
        try:
            r = int(cleaned[1:3], 16)
            g = int(cleaned[3:5], 16)
            b = int(cleaned[5:7], 16)
        except ValueError:
            return self

        def _mix(target_r: int, target_g: int, target_b: int, weight: float) -> str:
            nr = round(r * (1.0 - weight) + target_r * weight)
            ng = round(g * (1.0 - weight) + target_g * weight)
            nb = round(b * (1.0 - weight) + target_b * weight)
            return f"#{max(0, min(255, nr)):02X}{max(0, min(255, ng)):02X}{max(0, min(255, nb)):02X}"

        # Perceived luminance check to pick crisp text color on primary button
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        on_accent = "#1E1D24" if luminance > 0.64 else "#FFFEFC"

        if self.is_dark:
            hover = _mix(255, 255, 255, 0.14)
            pressed = _mix(0, 0, 0, 0.16)
            subtle = _mix(26, 25, 32, 0.72)
            soft_border = _mix(26, 25, 32, 0.45)
        else:
            hover = _mix(25, 24, 32, 0.12)
            pressed = _mix(25, 24, 32, 0.22)
            subtle = _mix(255, 254, 252, 0.84)
            soft_border = _mix(255, 254, 252, 0.58)

        return replace(
            self,
            accent_primary=cleaned.upper(),
            accent_hover=hover,
            accent_pressed=pressed,
            accent_text=on_accent,
            accent_subtle=subtle,
            accent_soft_border=soft_border,
            bg_selected=subtle,
        )


# ----------------------------------------------------------------------
# 1. Lavender Dream (Default Theme)
# ----------------------------------------------------------------------
LAVENDER_DREAM = ThemeTokens(
    theme_id="lavender_dream",
    display_name="Lavender Dream",
    name="light",
    is_dark=False,
    bg_canvas="#F8F7F4",        # Warm neutral Canvas
    bg_surface="#FFFEFC",       # Snow elevated card surface
    bg_elevated="#F5F0E8",      # Warm Cream secondary pill/header
    bg_hover="#EFE9F8",         # Whisper-soft lavender hover
    bg_selected="#E8E0F7",      # Soft Lavender selection tint
    bg_recessed="#F4F2EE",      # Subtle warm recessed well
    border_subtle="#E9E7E3",    # Delicate 1px hairline border
    border_strong="#DCD8D0",    # Refined control border
    text_primary="#35343B",     # Soft Charcoal primary ink
    text_secondary="#77747E",   # Muted plum-gray secondary ink
    text_muted="#9E9BA4",       # Subtle caption ink
    accent_primary="#6F59C9",   # Muted architectural purple
    accent_hover="#5F49B8",
    accent_pressed="#503CA3",
    accent_text="#FFFEFC",
    accent_subtle="#E8E0F7",    # Lavender #E8E0F7
    accent_soft_border="#D5C8F0",
    pastel_lavender="#E8E0F7",
    pastel_blush="#F7DFE7",
    pastel_sage="#DDEFE8",
    pastel_blue="#DFEAF8",
    pastel_butter="#F8EBCF",
    pastel_peach="#F4E5D9",
    success="#3E7B67",
    success_bg="#DDEFE8",
    warning="#8E6828",
    warning_bg="#F8EBCF",
    danger="#B5485D",
    danger_bg="#F7DFE7",
    scrollbar_thumb="#DDD9D2",
    scrollbar_thumb_hover="#C6C1B8",
)

# ----------------------------------------------------------------------
# 2. Sage Garden
# ----------------------------------------------------------------------
SAGE_GARDEN = ThemeTokens(
    theme_id="sage_garden",
    display_name="Sage Garden",
    name="light",
    is_dark=False,
    bg_canvas="#F7F7F3",        # Soft botanical ivory canvas
    bg_surface="#FFFEFC",       # Snow card surface
    bg_elevated="#F2EFE7",      # Warm beige secondary surface
    bg_hover="#E8F2EC",         # Soft sage whisper hover
    bg_selected="#DDEFE8",      # Sage #DDEFE8 selection
    bg_recessed="#F1F1EC",      # Quiet ivory well
    border_subtle="#E6E6DF",
    border_strong="#D6D6CC",
    text_primary="#323734",     # Deep botanical charcoal
    text_secondary="#6E7570",   # Muted olive-slate
    text_muted="#979E99",
    accent_primary="#477A63",   # Muted botanical sage-olive
    accent_hover="#3B6954",
    accent_pressed="#305745",
    accent_text="#FFFEFC",
    accent_subtle="#DDEFE8",    # Sage #DDEFE8
    accent_soft_border="#C3E0D4",
    pastel_lavender="#E8E0F7",
    pastel_blush="#F7DFE7",
    pastel_sage="#DDEFE8",
    pastel_blue="#DFEAF8",
    pastel_butter="#F8EBCF",
    pastel_peach="#F4E5D9",
    success="#3D7A5E",
    success_bg="#DDEFE8",
    warning="#8C6A2B",
    warning_bg="#F8EBCF",
    danger="#B24B58",
    danger_bg="#F7DFE7",
    scrollbar_thumb="#D6D8D0",
    scrollbar_thumb_hover="#BFC3B8",
)

# ----------------------------------------------------------------------
# 3. Peach Blossom
# ----------------------------------------------------------------------
PEACH_BLOSSOM = ThemeTokens(
    theme_id="peach_blossom",
    display_name="Peach Blossom",
    name="light",
    is_dark=False,
    bg_canvas="#F9F6F3",        # Warm cream canvas
    bg_surface="#FFFEFC",       # Snow card surface
    bg_elevated="#F5EDE6",      # Soft warm cream-peach
    bg_hover="#F7EAE4",         # Gentle peach hover
    bg_selected="#F4E5D9",      # Peach #F4E5D9 selection
    bg_recessed="#F4EFEA",      # Warm recessed well
    border_subtle="#EBE4DE",
    border_strong="#DDD3CB",
    text_primary="#3A3335",     # Warm espresso charcoal
    text_secondary="#7B6F73",   # Dusty rose-gray
    text_muted="#A3979B",
    accent_primary="#BA5B6E",   # Muted dusty rose
    accent_hover="#A64B5E",
    accent_pressed="#903E4F",
    accent_text="#FFFEFC",
    accent_subtle="#F7DFE7",    # Blush Pink #F7DFE7
    accent_soft_border="#ECC6D3",
    pastel_lavender="#E8E0F7",
    pastel_blush="#F7DFE7",
    pastel_sage="#DDEFE8",
    pastel_blue="#DFEAF8",
    pastel_butter="#F8EBCF",
    pastel_peach="#F4E5D9",
    success="#3E7B67",
    success_bg="#DDEFE8",
    warning="#946529",
    warning_bg="#F8EBCF",
    danger="#B54558",
    danger_bg="#F7DFE7",
    scrollbar_thumb="#DED5CE",
    scrollbar_thumb_hover="#C8BDB4",
)

# ----------------------------------------------------------------------
# 4. Midnight Orchid (Dark Theme)
# ----------------------------------------------------------------------
MIDNIGHT_ORCHID = ThemeTokens(
    theme_id="midnight_orchid",
    display_name="Midnight Orchid",
    name="dark",
    is_dark=True,
    bg_canvas="#151519",        # Deep velvety charcoal canvas
    bg_surface="#1D1D23",       # Elevated dark card surface
    bg_elevated="#25252D",      # Soft orchid-slate surface
    bg_hover="#2D2C37",         # Muted violet-charcoal hover
    bg_selected="#302A44",      # Deep muted lavender tint
    bg_recessed="#18181E",      # Recessed dark display well
    border_subtle="#2B2B34",    # Delicate 1px dark hairline
    border_strong="#393845",    # Refined control border
    text_primary="#F2F0F5",     # Soft pearl ivory text
    text_secondary="#A5A2B0",   # Muted lavender-gray text
    text_muted="#73707E",
    accent_primary="#A992F7",   # Soft luminous pastel violet
    accent_hover="#B9A5FA",
    accent_pressed="#9379EB",
    accent_text="#16151C",
    accent_subtle="#2D2742",
    accent_soft_border="#433A63",
    pastel_lavender="#322B47",
    pastel_blush="#422933",
    pastel_sage="#233831",
    pastel_blue="#243245",
    pastel_butter="#3E3523",
    pastel_peach="#3F3027",
    success="#6AC49E",
    success_bg="#213830",
    warning="#E2BE74",
    warning_bg="#3A3120",
    danger="#E58093",
    danger_bg="#3E252C",
    scrollbar_thumb="#33323E",
    scrollbar_thumb_hover="#484657",
)

# Public aliases for backwards compatibility & theme registry
LIGHT_THEME = LAVENDER_DREAM
DARK_THEME = MIDNIGHT_ORCHID

BUILT_IN_THEMES: dict[str, ThemeTokens] = {
    "lavender_dream": LAVENDER_DREAM,
    "sage_garden": SAGE_GARDEN,
    "peach_blossom": PEACH_BLOSSOM,
    "midnight_orchid": MIDNIGHT_ORCHID,
}

THEME_ALIASES: dict[str, str] = {
    "light": "lavender_dream",
    "dark": "midnight_orchid",
    "lavender": "lavender_dream",
    "sage": "sage_garden",
    "peach": "peach_blossom",
    "midnight": "midnight_orchid",
}

THEMES: dict[str, ThemeTokens] = {
    **BUILT_IN_THEMES,
    "light": LAVENDER_DREAM,
    "dark": MIDNIGHT_ORCHID,
}


def resolve_theme_id(theme_name: str) -> str:
    """Resolve any theme name or alias ('light', 'dark', 'sage_garden') to a canonical theme_id."""
    cleaned = theme_name.strip().lower().replace(" ", "_")
    if cleaned in BUILT_IN_THEMES:
        return cleaned
    return THEME_ALIASES.get(cleaned, "lavender_dream")


def get_theme_tokens(theme_name: str, custom_accent: str | None = None) -> ThemeTokens:
    """Return ThemeTokens for any built-in theme (optionally tinted with `custom_accent`)."""
    canonical = resolve_theme_id(theme_name)
    base = BUILT_IN_THEMES.get(canonical, LAVENDER_DREAM)
    if custom_accent:
        return base.with_custom_accent(custom_accent)
    return base