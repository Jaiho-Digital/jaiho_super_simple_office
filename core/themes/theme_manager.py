"""Runtime theme manager that compiles token-driven QSS and synchronizes QPalette."""

from __future__ import annotations

import logging
from string import Template
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from core.themes.tokens import ThemeTokens, get_theme_tokens
from core.utilities.paths import get_resource_path

if TYPE_CHECKING:
    from core.settings.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


class ThemeManager(QObject):
    """Manages active Light/Dark theme state, QSS compilation, and QApplication styling."""

    theme_changed = Signal(str)  # Emits 'light' or 'dark' after applying a theme

    def __init__(
        self,
        settings: SettingsManager | None = None,
        initial_theme: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings

        resolved_name = initial_theme
        if resolved_name is None and self._settings is not None:
            resolved_name = self._settings.get_theme()
        if resolved_name not in {"light", "dark"}:
            resolved_name = "light"

        self._tokens: ThemeTokens = get_theme_tokens(resolved_name)
        self._base_qss_template: str = self._load_base_qss()

    @property
    def current_theme(self) -> str:
        """Return the active theme identifier ('light' or 'dark')."""
        return self._tokens.name

    @property
    def tokens(self) -> ThemeTokens:
        """Return the active ThemeTokens instance."""
        return self._tokens

    def _load_base_qss(self) -> str:
        """Read the parameterized base.qss template from bundled resources."""
        qss_path = get_resource_path("styles/base.qss")
        if not qss_path.exists():
            logger.error("Base QSS template not found at '%s'. Using minimal fallback.", qss_path)
            return "QMainWindow, QDialog { background-color: $bg_canvas; color: $text_primary; }"

        try:
            return qss_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to read QSS template '%s': %s", qss_path, exc)
            return "QMainWindow, QDialog { background-color: $bg_canvas; color: $text_primary; }"

    def compile_stylesheet(self, tokens: ThemeTokens | None = None) -> str:
        """Render the complete Qt Style Sheet string for the given tokens."""
        active_tokens = tokens or self._tokens
        template = Template(self._base_qss_template)
        return template.safe_substitute(active_tokens.to_template_vars())

    def build_palette(self, tokens: ThemeTokens | None = None) -> QPalette:
        """Build a matching QPalette so native widgets and dialogs respect the active theme."""
        active = tokens or self._tokens
        palette = QPalette()

        palette.setColor(QPalette.ColorRole.Window, QColor(active.bg_canvas))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(active.text_primary))
        palette.setColor(QPalette.ColorRole.Base, QColor(active.bg_surface))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(active.bg_elevated))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(active.bg_elevated))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(active.text_primary))
        palette.setColor(QPalette.ColorRole.Text, QColor(active.text_primary))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(active.text_muted))
        palette.setColor(QPalette.ColorRole.Button, QColor(active.bg_surface))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(active.text_primary))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(active.accent_primary))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(active.accent_text))
        palette.setColor(QPalette.ColorRole.Link, QColor(active.accent_primary))

        # Disabled state colors
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor(active.text_muted),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor(active.text_muted),
        )
        return palette

    def apply_theme(self, theme_name: str | None = None, persist: bool = True) -> ThemeTokens:
        """Apply 'light' or 'dark' theme globally to the active QApplication instance."""
        target_name = (theme_name or self._tokens.name).strip().lower()
        if target_name not in {"light", "dark"}:
            raise ValueError(f"Unsupported theme '{theme_name}'. Expected 'light' or 'dark'.")

        self._tokens = get_theme_tokens(target_name)
        stylesheet = self.compile_stylesheet(self._tokens)
        palette = self.build_palette(self._tokens)

        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setPalette(palette)
            app.setStyleSheet(stylesheet)

        if persist and self._settings is not None:
            try:
                self._settings.set_theme(target_name)
            except Exception as exc:
                logger.warning("Could not persist theme preference '%s': %s", target_name, exc)

        logger.debug("Applied '%s' theme.", target_name)
        self.theme_changed.emit(target_name)
        return self._tokens

    def toggle_theme(self, persist: bool = True) -> ThemeTokens:
        """Switch between 'light' and 'dark' themes and apply immediately."""
        next_theme = "dark" if self._tokens.name == "light" else "light"
        return self.apply_theme(next_theme, persist=persist)