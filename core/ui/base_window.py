"""Unified main window base class and application bootstrapper for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.logging.logger import install_exception_hooks, setup_logging
from core.notifications.notifier import NotificationLevel, NotificationManager
from core.settings.settings_manager import SettingsManager
from core.themes.theme_manager import ThemeManager
from core.themes.tokens import BUILT_IN_THEMES
from core.ui.dialogs import show_about_dialog, show_error_dialog
from core.ui.icons import get_lucide_icon
from core.ui.toast import ToastManager
from core.utilities.paths import get_logs_dir, get_resource_path
from core.version import ORGANIZATION_NAME, SUITE_NAME, SUITE_VERSION, get_app_metadata

logger = logging.getLogger(__name__)


class BaseWindow(QMainWindow):
    """Base QMainWindow with Soft Modern Pastel header, 4-theme switcher, geometry memory, and toasts."""

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
        self._active_content: QWidget | None = None

        self.settings = settings or SettingsManager(app_id)
        self.theme_manager = theme_manager or ThemeManager(settings=self.settings)
        self.notifier = notifier or NotificationManager(app_id=app_id, parent=self)

        self.setWindowTitle(self.meta.display_name)
        self.setMinimumSize(min_width, min_height)
        self._app_icon = self._load_window_icon()
        if not self._app_icon.isNull():
            self.setWindowIcon(self._app_icon)

        # In-app toast stack anchored to this window
        self.toasts = ToastManager(host=self, tokens=self.theme_manager.tokens)
        self.notifier.notification_dispatched.connect(self._on_notification_dispatched)
        self.theme_manager.theme_changed.connect(self._on_theme_changed)

        # Build modern integrated shell (replaces old grey Windows menu/status bars)
        self._build_modern_shell()
        self._init_actions_and_shortcuts()

        # Apply active pastel theme and restore saved window geometry
        self.theme_manager.apply_theme(persist=False)
        self._refresh_header_chrome()
        self._restore_window_geometry()
        self.settings.record_app_launch(self.app_id)
        self._window_ready = True

    def _load_window_icon(self) -> QIcon:
        """Load per-app SVG/ICO icon if available, falling back to suite icon."""
        for candidate in (
            f"icons/{self.app_id}.svg",
            f"icons/{self.app_id}.ico",
            "icons/jaiho_office.svg",
            "icons/jaiho_office.ico",
        ):
            icon_path = get_resource_path(candidate)
            if icon_path.exists():
                return QIcon(str(icon_path))
        return QIcon()

    def _build_modern_shell(self) -> None:
        """Create the top integrated app header and content host container."""
        self._shell_widget = QWidget(self)
        self._shell_widget.setObjectName("windowShell")
        self._shell_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        shell_layout = QVBoxLayout(self._shell_widget)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        # Integrated Soft Modern Pastel Header Bar
        self._header_bar = QFrame(self._shell_widget)
        self._header_bar.setProperty("surface", "app-header")
        self._header_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._header_bar.setFixedHeight(48)

        header_layout = QHBoxLayout(self._header_bar)
        header_layout.setContentsMargins(16, 6, 14, 6)
        header_layout.setSpacing(10)

        # App Icon Emblem
        self._header_icon_lbl = QLabel(self._header_bar)
        self._header_icon_lbl.setFixedSize(24, 24)
        if not self._app_icon.isNull():
            self._header_icon_lbl.setPixmap(self._app_icon.pixmap(QSize(24, 24)))
        header_layout.addWidget(self._header_icon_lbl)

        # Clean App Title
        self._header_title_lbl = QLabel(self.meta.display_name, self._header_bar)
        self._header_title_lbl.setProperty("role", "app-title")
        header_layout.addWidget(self._header_title_lbl)

        # Transient subtle status pill (replaces old bottom Windows status bar)
        self._transient_status_lbl = QLabel("", self._header_bar)
        self._transient_status_lbl.setProperty("role", "caption")
        self._transient_status_lbl.setVisible(False)
        header_layout.addWidget(self._transient_status_lbl)

        self._status_clear_timer = QTimer(self)
        self._status_clear_timer.setSingleShot(True)
        self._status_clear_timer.timeout.connect(lambda: self._transient_status_lbl.setVisible(False))

        header_layout.addStretch(1)

        # Extensible slot where child apps can mount compact mode tabs or toolbar pills
        self.header_actions_layout = QHBoxLayout()
        self.header_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.header_actions_layout.setSpacing(6)
        header_layout.addLayout(self.header_actions_layout)

        # Theme & Pastel Palette Switcher Button
        self._theme_btn = QPushButton("Lavender Dream", self._header_bar)
        self._theme_btn.setProperty("variant", "ghost")
        self._theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_btn.setToolTip("Switch pastel theme or customize accent color (Ctrl+Shift+T)")
        self._theme_btn.setStyleSheet("padding: 5px 10px; font-size: 12px;")
        self._theme_menu = QMenu(self._theme_btn)
        self._theme_btn.setMenu(self._theme_menu)
        self._theme_btn.setProperty("has_menu", True)
        header_layout.addWidget(self._theme_btn)

        # Minimal Overflow Menu Button (•••)
        self._more_btn = QPushButton("", self._header_bar)
        self._more_btn.setProperty("variant", "ghost")
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.setFixedSize(30, 30)
        self._more_btn.setToolTip("Window & application options")
        self._more_btn.setStyleSheet("padding: 0px;")
        self._more_menu = QMenu(self._more_btn)
        self._more_btn.setMenu(self._more_menu)
        header_layout.addWidget(self._more_btn)

        shell_layout.addWidget(self._header_bar)

        # Content Host where subclass central widgets live
        self._content_host = QWidget(self._shell_widget)
        self._content_host.setObjectName("contentHost")
        self._content_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._content_host_layout = QVBoxLayout(self._content_host)
        self._content_host_layout.setContentsMargins(0, 0, 0, 0)
        self._content_host_layout.setSpacing(0)
        shell_layout.addWidget(self._content_host, 1)

        super().setCentralWidget(self._shell_widget)

    def setCentralWidget(self, widget: QWidget | None) -> None:  # noqa: N802
        """Mount `widget` inside the modern shell below the integrated pastel header."""
        if self._active_content is not None:
            self._content_host_layout.removeWidget(self._active_content)
            self._active_content.setParent(None)
            self._active_content = None

        if widget is not None:
            self._active_content = widget
            self._content_host_layout.addWidget(widget, 1)

    def _init_actions_and_shortcuts(self) -> None:
        """Configure theme menu, overflow actions, and global keyboard shortcuts."""
        # Populate 4 Built-in Pastel Themes + Custom Accent Picker
        self._theme_action_group = QActionGroup(self)
        self._theme_action_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}

        for theme_id, tokens in BUILT_IN_THEMES.items():
            act = QAction(tokens.display_name, self)
            act.setCheckable(True)
            act.setChecked(theme_id == self.theme_manager.active_theme_id)
            act.triggered.connect(lambda _=False, tid=theme_id: self.select_theme(tid))
            self._theme_action_group.addAction(act)
            self._theme_menu.addAction(act)
            self._theme_actions[theme_id] = act

        self._theme_menu.addSeparator()

        self.custom_accent_action = QAction("Custom Accent Color...", self)
        self.custom_accent_action.triggered.connect(self.choose_custom_accent)
        self._theme_menu.addAction(self.custom_accent_action)

        self.reset_accent_action = QAction("Reset Theme Default Accent", self)
        self.reset_accent_action.triggered.connect(self.reset_custom_accent)
        self._theme_menu.addAction(self.reset_accent_action)

        # Populate Overflow Menu
        self.toggle_theme_action = QAction("Toggle Light / Dark Mode", self)
        self.toggle_theme_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self.toggle_theme_action.triggered.connect(self.toggle_theme)
        self._more_menu.addAction(self.toggle_theme_action)

        self.reset_geometry_action = QAction("Reset Window Size", self)
        self.reset_geometry_action.triggered.connect(self.reset_window_geometry)
        self._more_menu.addAction(self.reset_geometry_action)

        self.open_logs_action = QAction("Open Logs Folder", self)
        self.open_logs_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_logs_dir())))
        )
        self._more_menu.addAction(self.open_logs_action)

        self._more_menu.addSeparator()

        self.about_action = QAction(f"About {self.meta.display_name}", self)
        self.about_action.triggered.connect(lambda: show_about_dialog(self, self.app_id))
        self._more_menu.addAction(self.about_action)

        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.triggered.connect(self.close)
        self._more_menu.addAction(self.exit_action)

        # Ensure shortcuts trigger even without a traditional QMenuBar
        QShortcut(QKeySequence("Ctrl+Shift+T"), self).activated.connect(self.toggle_theme)

    def _refresh_header_chrome(self) -> None:
        """Update theme button label, Lucide icons, and checked theme radio item."""
        tokens = self.theme_manager.tokens
        self._theme_btn.setText(tokens.display_name)
        self._theme_btn.setIcon(
            get_lucide_icon("palette", color=tokens.accent_primary, size=14)
        )
        self._theme_btn.setIconSize(QSize(14, 14))

        self._more_btn.setIcon(
            get_lucide_icon("more-horizontal", color=tokens.text_secondary, size=16)
        )
        self._more_btn.setIconSize(QSize(16, 16))

        for tid, act in self._theme_actions.items():
            act.setChecked(tid == self.theme_manager.active_theme_id)
        self.reset_accent_action.setEnabled(bool(self.theme_manager.custom_accent))

    def set_status_message(self, message: str, timeout_ms: int = 2400) -> None:
        """Show a subtle transient status pill in the header bar."""
        cleaned = message.strip()
        if not cleaned or cleaned.lower() == "ready":
            self._transient_status_lbl.setVisible(False)
            return

        self._transient_status_lbl.setText(f"•  {cleaned}")
        self._transient_status_lbl.setVisible(True)
        duration = timeout_ms if timeout_ms > 0 else 2600
        self._status_clear_timer.start(duration)

    def select_theme(self, theme_id: str) -> None:
        """Apply one of the 4 built-in pastel themes and persist user choice."""
        tokens = self.theme_manager.set_theme(theme_id, persist=True)
        self.set_status_message(f"{tokens.display_name} theme", timeout_ms=2200)

    def choose_custom_accent(self) -> None:
        """Open a color picker to customize the primary accent across the active theme."""
        initial = QColor(self.theme_manager.tokens.accent_primary)
        chosen = QColorDialog.getColor(initial, self, "Choose Custom Accent Color")
        if chosen.isValid():
            self.theme_manager.set_custom_accent(chosen.name(), persist=True)
            self.set_status_message(f"Accent updated ({chosen.name().upper()})", timeout_ms=2400)

    def reset_custom_accent(self) -> None:
        """Restore the active theme's default pastel accent color."""
        self.theme_manager.set_custom_accent(None, persist=True)
        self.set_status_message("Restored default theme accent", timeout_ms=2200)

    def toggle_theme(self) -> None:
        """Toggle between Light (Lavender Dream) and Dark (Midnight Orchid)."""
        tokens = self.theme_manager.toggle_theme(persist=True)
        self.set_status_message(f"{tokens.display_name} theme", timeout_ms=2200)

    def _on_theme_changed(self, theme_name: str) -> None:
        self.toasts.set_tokens(self.theme_manager.tokens)
        self._refresh_header_chrome()
        if self._window_ready:
            self.on_theme_updated(theme_name)

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
        width = max(self.minimumWidth(), int(geom.get("width", 1040)))
        height = max(self.minimumHeight(), int(geom.get("height", 680)))
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
        """Reset window to standard dimensions centered on the primary screen."""
        self.showNormal()
        self.resize(max(self.minimumWidth(), 1040), max(self.minimumHeight(), 680))
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