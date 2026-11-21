"""Non-intrusive in-app toast notifications anchored to parent windows."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.notifications.notifier import NotificationLevel
from core.themes.tokens import LIGHT_THEME, ThemeTokens


class ToastBanner(QFrame):
    """Single compact notification banner with severity accent and hover-pause."""

    dismissed = Signal(object)  # Emits self when closed

    def __init__(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
        duration_ms: int = 4000,
        tokens: ThemeTokens = LIGHT_THEME,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("JaihoToastBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(340)

        self._duration_ms = max(0, int(duration_ms))
        self._closed = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close_toast)

        self._build_ui(title=title, message=message, level=level, tokens=tokens)
        self.apply_tokens(tokens, level)

        if self._duration_ms > 0:
            self._timer.start(self._duration_ms)

    def _build_ui(
        self,
        title: str,
        message: str,
        level: NotificationLevel,
        tokens: ThemeTokens,
    ) -> None:
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(12, 10, 10, 10)
        root_layout.setSpacing(10)

        # Severity badge symbol
        badge_map = {
            NotificationLevel.INFO: "i",
            NotificationLevel.SUCCESS: "✓",
            NotificationLevel.WARNING: "!",
            NotificationLevel.ERROR: "×",
        }
        self._badge = QLabel(badge_map.get(level, "i"), self)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFixedSize(20, 20)
        root_layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignTop)

        # Title + message text column
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        if title.strip():
            self._title_label = QLabel(title.strip(), self)
            self._title_label.setWordWrap(True)
            text_col.addWidget(self._title_label)
        else:
            self._title_label = None

        self._message_label = QLabel(message.strip(), self)
        self._message_label.setWordWrap(True)
        text_col.addWidget(self._message_label)

        root_layout.addLayout(text_col, 1)

        # Dismiss button
        self._close_btn = QPushButton("×", self)
        self._close_btn.setToolTip("Dismiss notification")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedSize(22, 22)
        self._close_btn.clicked.connect(self.close_toast)
        root_layout.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.adjustSize()

    def apply_tokens(self, tokens: ThemeTokens, level: NotificationLevel) -> None:
        """Style the banner using active theme tokens and semantic severity colors."""
        accent_map = {
            NotificationLevel.INFO: (tokens.accent_primary, tokens.bg_selected),
            NotificationLevel.SUCCESS: (tokens.success, tokens.success_bg),
            NotificationLevel.WARNING: (tokens.warning, tokens.warning_bg),
            NotificationLevel.ERROR: (tokens.danger, tokens.danger_bg),
        }
        accent_color, badge_bg = accent_map.get(
            level, (tokens.accent_primary, tokens.bg_selected)
        )

        self.setStyleSheet(
            f"""
            QFrame#JaihoToastBanner {{
                background-color: {tokens.bg_surface};
                border: 1px solid {tokens.border_strong};
                border-left: 4px solid {accent_color};
                border-radius: {tokens.radius_sm};
            }}
            """
        )
        self._badge.setStyleSheet(
            f"""
            QLabel {{
                background-color: {badge_bg};
                color: {accent_color};
                font-weight: 700;
                border-radius: 10px;
                font-size: {tokens.font_size_sm};
            }}
            """
        )
        if self._title_label is not None:
            self._title_label.setStyleSheet(
                f"color: {tokens.text_primary}; font-weight: 600; font-size: {tokens.font_size_base};"
            )
        self._message_label.setStyleSheet(
            f"color: {tokens.text_secondary}; font-size: {tokens.font_size_sm};"
        )
        self._close_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {tokens.text_muted};
                font-size: 15px;
                font-weight: 600;
                padding: 0px;
            }}
            QPushButton:hover {{
                color: {tokens.text_primary};
                background-color: {tokens.bg_hover};
                border-radius: 3px;
            }}
            """
        )

    def enterEvent(self, event: QEnterEvent) -> None:
        """Pause auto-dismiss while the user hovers over the notification."""
        if self._timer.isActive():
            self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        """Resume a short dismiss countdown when the cursor leaves."""
        if not self._closed and self._duration_ms > 0:
            self._timer.start(min(self._duration_ms, 2000))
        super().leaveEvent(event)

    def close_toast(self) -> None:
        """Hide, emit dismissal signal, and schedule deletion."""
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self.hide()
        self.dismissed.emit(self)
        self.deleteLater()


class ToastManager(QObject):
    """Manages stacked toast banners inside a host QWidget (such as BaseWindow)."""

    MAX_VISIBLE = 3
    MARGIN_RIGHT = 16
    MARGIN_BOTTOM = 36  # Clears standard QStatusBar height
    STACK_GAP = 8

    def __init__(
        self,
        host: QWidget,
        tokens: ThemeTokens = LIGHT_THEME,
    ) -> None:
        super().__init__(host)
        self._host = host
        self._tokens = tokens
        self._active: list[tuple[ToastBanner, NotificationLevel]] = []
        self._host.installEventFilter(self)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Update theme tokens and restyle any currently visible toasts."""
        self._tokens = tokens
        for banner, level in self._active:
            banner.apply_tokens(tokens, level)
        self._reposition_all()

    def show_toast(
        self,
        title: str,
        message: str,
        level: NotificationLevel | str = NotificationLevel.INFO,
        duration_ms: int = 4000,
    ) -> ToastBanner:
        """Create, display, and position a toast banner in the host window."""
        resolved_level = (
            level
            if isinstance(level, NotificationLevel)
            else NotificationLevel(str(level).lower())
        )

        # Evict oldest toast if stack limit is reached
        while len(self._active) >= self.MAX_VISIBLE:
            oldest_banner, _ = self._active[0]
            oldest_banner.close_toast()

        banner = ToastBanner(
            title=title,
            message=message,
            level=resolved_level,
            duration_ms=duration_ms,
            tokens=self._tokens,
            parent=self._host,
        )
        banner.dismissed.connect(self._on_banner_dismissed)
        self._active.append((banner, resolved_level))

        banner.show()
        banner.raise_()
        self._reposition_all()
        return banner

    def clear_all(self) -> None:
        """Dismiss all active toast banners immediately."""
        for banner, _ in list(self._active):
            banner.close_toast()

    def _on_banner_dismissed(self, banner: object) -> None:
        self._active = [(b, lvl) for b, lvl in self._active if b is not banner]
        self._reposition_all()

    def _reposition_all(self) -> None:
        """Stack visible toasts upward from the bottom-right corner of the host widget."""
        if not self._active:
            return

        host_rect = self._host.rect()
        cursor_y = host_rect.height() - self.MARGIN_BOTTOM

        for banner, _ in reversed(self._active):
            banner.adjustSize()
            b_width = banner.width()
            b_height = banner.height()
            x = max(8, host_rect.width() - b_width - self.MARGIN_RIGHT)
            y = max(8, cursor_y - b_height)
            banner.move(QPoint(x, y))
            banner.raise_()
            cursor_y = y - self.STACK_GAP

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Reposition toasts automatically whenever the host window resizes."""
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self._reposition_all()
        return super().eventFilter(watched, event)