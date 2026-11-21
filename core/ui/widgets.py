"""Reusable, theme-aware UI components shared across Jaiho Super Simple Office."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def refresh_widget_style(widget: QWidget) -> None:
    """Force Qt to re-evaluate QSS rules after changing dynamic properties."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def make_button(
    text: str,
    variant: str = "default",
    tooltip: str = "",
    on_click: Callable[[], None] | None = None,
    parent: QWidget | None = None,
) -> QPushButton:
    """Create a QPushButton configured with a standard QSS variant ('default', 'primary', 'danger', 'ghost')."""
    btn = QPushButton(text, parent)
    if variant != "default":
        btn.setProperty("variant", variant)
    if tooltip:
        btn.setToolTip(tooltip)
    if on_click is not None:
        btn.clicked.connect(on_click)
    return btn


class HorizontalDivider(QFrame):
    """Crisp 1px horizontal separator styled via base.qss."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setFixedHeight(1)


class VerticalDivider(QFrame):
    """Crisp 1px vertical separator styled via base.qss."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.VLine)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setFixedWidth(1)


class CardFrame(QFrame):
    """Structured surface container with subtle border and consistent internal padding."""

    def __init__(
        self,
        padding: int = 16,
        spacing: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("surface", "card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(padding, padding, padding, padding)
        self.body_layout.setSpacing(spacing)


class SidebarFrame(QFrame):
    """Consistent left/right navigation or inspector panel."""

    def __init__(
        self,
        width: int | None = 260,
        padding: int = 12,
        spacing: int = 8,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("surface", "sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if width is not None:
            self.setFixedWidth(width)

        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(padding, padding, padding, padding)
        self.body_layout.setSpacing(spacing)


class PageHeader(QWidget):
    """Top workspace header with title, optional subtitle, and right-aligned action bar."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(12)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "title")
        text_col.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setProperty("role", "muted")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle.strip()))
        text_col.addWidget(self.subtitle_label)

        root.addLayout(text_col, 1)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(0, 0, 0, 0)
        self.actions_layout.setSpacing(8)
        root.addLayout(self.actions_layout, 0)

    def set_title(self, title: str) -> None:
        """Update the primary page title."""
        self.title_label.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        """Update or hide the subtitle text."""
        clean = subtitle.strip()
        self.subtitle_label.setText(clean)
        self.subtitle_label.setVisible(bool(clean))

    def add_action(self, widget: QWidget) -> None:
        """Append a button or control to the right side of the header."""
        self.actions_layout.addWidget(widget)


class SectionHeader(QWidget):
    """Compact section heading with optional right-aligned accessory control."""

    def __init__(
        self,
        title: str,
        accessory: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self.label = QLabel(title, self)
        self.label.setProperty("role", "section-header")
        layout.addWidget(self.label, 1)

        if accessory is not None:
            layout.addWidget(accessory, 0)

    def set_title(self, title: str) -> None:
        """Update the section title text."""
        self.label.setText(title)


class SearchInput(QLineEdit):
    """Debounced search input with built-in clear button for responsive filtering."""

    search_changed = Signal(str)

    def __init__(
        self,
        placeholder: str = "Search...",
        debounce_ms: int = 180,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, int(debounce_ms)))
        self._timer.timeout.connect(self._emit_current_text)

        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, text: str) -> None:
        if self._timer.interval() == 0 or not text:
            self._timer.stop()
            self.search_changed.emit(text)
        else:
            self._timer.start()

    def _emit_current_text(self) -> None:
        self.search_changed.emit(self.text())


class StatusBadge(QLabel):
    """Compact pill label for displaying states such as Saved, Overdue, or Connected."""

    _VARIANTS: dict[str, tuple[str, str, str]] = {
        "neutral": ("#EEF1F5", "#4E5869", "#DCE1E8"),
        "info": ("#E6EFFD", "#1D5FD6", "#B6D0FA"),
        "success": ("#E6F5EE", "#16794C", "#A8DFC5"),
        "warning": ("#FEF4E6", "#A65E00", "#F5D099"),
        "danger": ("#FDECEC", "#C42B2B", "#F5B8B8"),
    }

    def __init__(
        self,
        text: str,
        variant: str = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_status(text, variant)

    def set_status(self, text: str, variant: str = "neutral") -> None:
        """Update badge label text and semantic color variant."""
        self.setText(text)
        bg, fg, border = self._VARIANTS.get(variant, self._VARIANTS["neutral"])
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 600;
            }}
            """
        )


class EmptyStateWidget(QWidget):
    """Centered empty-state view with title, explanation, and optional primary action."""

    action_triggered = Signal()

    def __init__(
        self,
        title: str,
        description: str,
        action_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 32, 24, 32)
        outer.addStretch(1)

        inner = QVBoxLayout()
        inner.setSpacing(8)
        inner.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "section-header")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        inner.addWidget(self.title_label)

        self.description_label = QLabel(description, self)
        self.description_label.setProperty("role", "muted")
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description_label.setWordWrap(True)
        self.description_label.setMaximumWidth(420)
        inner.addWidget(self.description_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.action_button: QPushButton | None = None
        if action_text.strip():
            self.action_button = make_button(
                text=action_text.strip(),
                variant="primary",
                on_click=self.action_triggered.emit,
                parent=self,
            )
            inner.addSpacing(6)
            inner.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignHCenter)

        outer.addLayout(inner)
        outer.addStretch(1)