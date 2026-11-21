"""Unified main window base class and application bootstrapper for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMenu,
    QStatusBar,
    QWidget,
)

from core.logging.logger import install_exception_hooks, setup_logging
from core.notifications.notifier import NotificationLevel, NotificationManager
from core.settings.settings_manager import SettingsManager
from core.themes.theme_manager import ThemeManager
from core.ui.dialogs import show_about_dialog, show_error_dialog
from core.ui.toast import ToastManager
from core.utilities.paths import get_logs_dir, get_resource_path
from core.version import ORGANIZATION_NAME, SUITE_NAME, SUITE_VERSION, get_app_metadata

logger = logging.getLogger(__name__)


class BaseWindow(QMainWindow):
    """Base QMainWindow providing consistent menus, theme switching, geometry persistence, and toasts."""

    def __init__(
        self,
        app_id: str,
        settings: SettingsManager | None = None,
        theme_manager: ThemeManager | None = None,
        notifier: NotificationManager | None = None,
        min_width: int = 760,
        min_height: int = 500,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_id = app_id
        self.meta = get_app_metadata(app_id)
        self._window_ready: bool = False

        self.settings = settings or SettingsManager(app_id)
        self.theme_manager = theme_manager or ThemeManager(settings=self.settings)
        self.notifier = notifier or NotificationManager(app_id=app_id, parent=self)

        self.setWindowTitle(self.meta.display_name)
        self.setMinimumSize(min_width, min_height)
        self._apply_window_icon()

        # In-app toast stack anchored to this window
        self.toasts = ToastManager(host=self, tokens=self.theme_manager.tokens)
        self.notifier.notification_dispatched.connect(self._on_notification_dispatched)
        self.theme_manager.theme_changed.connect(self._on_theme_changed)

        # Standard menus and status bar
        self._init_menus()
        self._init_status_bar()

        # Apply active theme and restore saved window geometry
        self.theme_manager.apply_theme(persist=False)
        self._restore_window_geometry()
        self.settings.record_app_launch(self.app_id)
        self._window_ready = True

    def _apply_window_icon(self) -> None:
        """Load per-app icon if available, falling back to suite icon."""
        for candidate in (f"icons/{self.app_id}.ico", "icons/jaiho_office.ico"):
            icon_path = get_resource_path(candidate)
            if icon_path.exists():
                self.setWindowIcon(QIcon(str(icon_path)))
                return

    def _init_menus(self) -> None:
        """Create standard File, View, and Help menus that subclasses can extend."""
        menubar = self.menuBar()

        self.file_menu: QMenu = menubar.addMenu("&File")
        self.exit_action = QAction("E&xit", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.setStatusTip(f"Close {self.meta.display_name}")
        self.exit_action.triggered.connect(self.close)
        self.file_menu.addAction(self.exit_action)

        self.view_menu: QMenu = menubar.addMenu("&View")
        self.toggle_theme_action = QAction("Toggle Light / Dark Theme", self)
        self.toggle_theme_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self.toggle_theme_action.setStatusTip("Switch between Light Mode and Dark Mode")
        self.toggle_theme_action.triggered.connect(self.toggle_theme)
        self.view_menu.addAction(self.toggle_theme_action)

        self.reset_geometry_action = QAction("Reset Window Size", self)
        self.reset_geometry_action.setStatusTip("Restore default window dimensions")
        self.reset_geometry_action.triggered.connect(self.reset_window_geometry)
        self.view_menu.addAction(self.reset_geometry_action)

        self.help_menu: QMenu = menubar.addMenu("&Help")
        self.open_logs_action = QAction("Open Logs Folder", self)
        self.open_logs_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_logs_dir())))
        )
        self.help_menu.addAction(self.open_logs_action)
        self.help_menu.addSeparator()

        self.about_action = QAction(f"About {self.meta.display_name}", self)
        self.about_action.triggered.connect(lambda: show_about_dialog(self, self.app_id))
        self.help_menu.addAction(self.about_action)

    def _init_status_bar(self) -> None:
        """Create a clean status bar with a persistent right-aligned theme indicator."""
        status = QStatusBar(self)
        self.setStatusBar(status)

        self._theme_status_label = QLabel(self)
        self._theme_status_label.setProperty("role", "caption")
        self._theme_status_label.setContentsMargins(6, 0, 8, 0)
        status.addPermanentWidget(self._theme_status_label)
        self._update_theme_indicator(self.theme_manager.current_theme)

        status.showMessage("Ready")

    def set_status_message(self, message: str, timeout_ms: int = 0) -> None:
        """Update the left status bar text."""
        self.statusBar().showMessage(message, max(0, int(timeout_ms)))

    def toggle_theme(self) -> None:
        """Toggle between Light and Dark themes across the active application."""
        tokens = self.theme_manager.toggle_theme(persist=True)
        self.set_status_message(f"Switched to {tokens.name.title()} theme", timeout_ms=2500)

    def _on_theme_changed(self, theme_name: str) -> None:
        self.toasts.set_tokens(self.theme_manager.tokens)
        self._update_theme_indicator(theme_name)
        if self._window_ready:
            self.on_theme_updated(theme_name)

    def _update_theme_indicator(self, theme_name: str) -> None:
        self._theme_status_label.setText(f"Theme: {theme_name.title()}")

    def on_theme_updated(self, theme_name: str) -> None:
        """Hook for subclasses (e.g., custom canvases/charts) to redraw on theme change."""
        _ = theme_name

    def notify(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
        desktop_banner: bool = False,
        play_sound: bool = False,
    ) -> None:
        """Show an in-app toast notification and optionally a Windows system tray banner."""
        self.notifier.notify(
            title=title,
            message=message,
            level=level,
            desktop_banner=desktop_banner,
            play_sound=play_sound,
        )

    def _on_notification_dispatched(self, title: str, message: str, level_str: str) -> None:
        self.toasts.show_toast(title=title, message=message, level=level_str)

    def _restore_window_geometry(self) -> None:
        """Restore window dimensions and position while ensuring visibility on active screens."""
        geom = self.settings.get_window_geometry()
        width = max(self.minimumWidth(), int(geom.get("width", 1100)))
        height = max(self.minimumHeight(), int(geom.get("height", 720)))
        self.resize(width, height)

        saved_x = geom.get("x")
        saved_y = geom.get("y")
        if (
            isinstance(saved_x, int)
            and isinstance(saved_y, int)
            and self._is_point_on_screen(saved_x, saved_y)
        ):
            self.move(saved_x, saved_y)

        if bool(geom.get("maximized", False)):
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    @staticmethod
    def _is_point_on_screen(x: int, y: int) -> bool:
        """Return True if the coordinate falls within any currently connected display."""
        for screen in QGuiApplication.screens():
            avail = screen.availableGeometry()
            if avail.contains(x + 40, y + 40):
                return True
        return False

    def reset_window_geometry(self) -> None:
        """Reset window to standard 1100x720 dimensions centered on the primary screen."""
        self.showNormal()
        self.resize(max(self.minimumWidth(), 1100), max(self.minimumHeight(), 720))
        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            center = primary.availableGeometry().center()
            frame = self.frameGeometry()
            frame.moveCenter(center)
            self.move(frame.topLeft())
        self._save_window_geometry()

    def _save_window_geometry(self) -> None:
        """Persist current window size, coordinates, and maximized state."""
        is_max = self.isMaximized()
        normal_rect = self.normalGeometry() if is_max else self.geometry()
        try:
            self.settings.set_window_geometry(
                width=normal_rect.width(),
                height=normal_rect.height(),
                x=normal_rect.x(),
                y=normal_rect.y(),
                maximized=is_max,
            )
        except Exception as exc:
            logger.warning("Failed to save window geometry for '%s': %s", self.app_id, exc)

    def can_close(self) -> bool:
        """Lifecycle hook for subclasses to prompt on unsaved changes before closing."""
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        """Validate close readiness, persist window state, and clean up tray resources."""
        if not self.can_close():
            event.ignore()
            return

        self._save_window_geometry()
        self.toasts.clear_all()
        self.notifier.shutdown()
        event.accept()


def bootstrap_application(
    app_id: str,
    window_factory: Callable[[], BaseWindow],
    argv: list[str] | None = None,
    debug: bool = False,
) -> int:
    """Initialize logging, QApplication, global crash hooks, and run the main event loop."""
    setup_logging(app_id=app_id, debug=debug)
    meta = get_app_metadata(app_id)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv if argv is not None else sys.argv)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setApplicationName(meta.display_name)
    app.setApplicationVersion(SUITE_VERSION)

    window = window_factory()

    def _show_crash_dialog(summary: str, trace: str) -> None:
        QTimer.singleShot(
            0,
            lambda: show_error_dialog(
                parent=window,
                title=f"{meta.display_name} — Unexpected Error",
                message=f"An unexpected error occurred: {summary}\n\nDetails have been written to the log file.",
                details=trace,
            ),
        )

    install_exception_hooks(app_id=app_id, on_uncaught_error=_show_crash_dialog)

    window.show()
    logger.info("Started %s (%s)", meta.display_name, SUITE_NAME)
    return app.exec()