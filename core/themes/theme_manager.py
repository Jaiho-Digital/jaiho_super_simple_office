"""Centralized runtime theme manager supporting 4 pastel themes, custom accents, and live QSS."""

from __future__ import annotations

import logging
from string import Template

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from core.settings.settings_manager import SettingsManager
from core.themes.tokens import (
    BUILT_IN_THEMES,
    ThemeTokens,
    get_theme_tokens,
    resolve_theme_id,
)
from core.utilities.paths import get_resource_path

logger = logging.getLogger(__name__)


class ThemeManager(QObject):
    """Manages active pastel theme tokens, custom accents, and QSS compilation."""

    theme_changed = Signal(str)

    def __init__(
        self,
        settings: SettingsManager | None = None,
        initial_theme: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._custom_accent: str | None = None

        if initial_theme is not None:
            raw_theme = initial_theme
        elif self._settings is not None:
            raw_theme = self._settings.get_theme()
            saved_accent = self._settings.get("custom_accent_color", None)
            if isinstance(saved_accent, str) and saved_accent.strip():
                self._custom_accent = saved_accent.strip()
        else:
            raw_theme = "lavender_dream"

        self._active_theme_id: str = resolve_theme_id(raw_theme)
        self._tokens: ThemeTokens = get_theme_tokens(
            self._active_theme_id,
            custom_accent=self._custom_accent,
        )

    @property
    def active_theme_id(self) -> str:
        """Return canonical theme ID ('lavender_dream', 'sage_garden', 'peach_blossom', 'midnight_orchid')."""
        return self._active_theme_id

    @property
    def current_theme(self) -> str:
        """Return 'dark' when Midnight Orchid is active, else 'light' (or active theme state)."""
        return "dark" if self._tokens.is_dark else "light"

    @property
    def tokens(self) -> ThemeTokens:
        """Return the active ThemeTokens dataclass."""
        return self._tokens

    @property
    def custom_accent(self) -> str | None:
        """Return active custom accent hex string, or None when using theme default."""
        return self._custom_accent

    @staticmethod
    def available_themes() -> list[ThemeTokens]:
        """Return all 4 built-in ThemeTokens in presentation order."""
        return list(BUILT_IN_THEMES.values())

    def compile_stylesheet(
        self,
        theme_name: str | None = None,
        extra_qss: str = "",
    ) -> str:
        """Load resources/styles/base.qss and substitute all theme tokens cleanly."""
        target_tokens = (
            get_theme_tokens(theme_name, custom_accent=self._custom_accent)
            if theme_name is not None
            else self._tokens
        )
        qss_path = get_resource_path("styles/base.qss")
        raw_template = qss_path.read_text(encoding="utf-8")
        compiled = Template(raw_template).safe_substitute(target_tokens.to_template_vars())
        if extra_qss.strip():
            compiled = f"{compiled}\n\n/* App-Specific Overrides */\n{extra_qss.strip()}\n"
        return compiled

    def apply_theme(
        self,
        theme_name: str | None = None,
        target: QApplication | QWidget | None = None,
        extra_qss: str = "",
        persist: bool = True,
    ) -> ThemeTokens:
        """Compile and apply the stylesheet to QApplication or a specific QWidget."""
        if theme_name is not None:
            self._active_theme_id = resolve_theme_id(theme_name)

        self._tokens = get_theme_tokens(
            self._active_theme_id,
            custom_accent=self._custom_accent,
        )
        stylesheet = self.compile_stylesheet(extra_qss=extra_qss)

        app_or_widget = target if target is not None else QApplication.instance()
        if isinstance(app_or_widget, (QApplication, QWidget)):
            app_or_widget.setStyleSheet(stylesheet)

        if persist and self._settings is not None:
            self._settings.set_theme(self._active_theme_id)

        self.theme_changed.emit(self.current_theme)
        logger.debug("Applied theme: %s", self._active_theme_id)
        return self._tokens

    def set_theme(self, theme_id: str, persist: bool = True) -> ThemeTokens:
        """Switch directly to any of the 4 built-in themes."""
        return self.apply_theme(theme_name=theme_id, persist=persist)

    def set_custom_accent(self, hex_color: str | None, persist: bool = True) -> ThemeTokens:
        """Apply or clear a custom primary accent hex color across the active theme."""
        cleaned = hex_color.strip() if isinstance(hex_color, str) and hex_color.strip() else None
        self._custom_accent = cleaned
        if persist and self._settings is not None:
            self._settings.set("custom_accent_color", cleaned or "")
        return self.apply_theme(persist=False)

    def toggle_theme(self, persist: bool = True) -> ThemeTokens:
        """Switch between Light (Lavender Dream) and Dark (Midnight Orchid)."""
        next_theme = "lavender_dream" if self._tokens.is_dark else "midnight_orchid"
        return self.apply_theme(theme_name=next_theme, persist=persist)