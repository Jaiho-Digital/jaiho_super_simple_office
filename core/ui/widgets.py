"""Reusable Soft Modern Pastel UI components for Jaiho Super Simple Office."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.ui.icons import get_lucide_icon, render_lucide_pixmap


def apply_subtle_shadow(
    widget: QWidget,
    blur_radius: float = 20.0,
    y_offset: float = 4.0,
    alpha: int = 16,
) -> QGraphicsDropShadowEffect:
    """Attach a delicate, low-opacity ambient elevation shadow to a card or floating surface."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur_radius)
    shadow.setOffset(0.0, y_offset)
    shadow.setColor(QColor(35, 34, 46, max(0, min(255, alpha))))
    widget.setGraphicsEffect(shadow)
    return shadow


class CardFrame(QFrame):
    """Elevated Snow/Charcoal surface container with 16px rounded corners and delicate hairline border."""

    def __init__(
        self,
        padding: int = 18,
        spacing: int = 12,
        elevated_shadow: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        _ = elevated_shadow
        self.setProperty("surface", "card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(padding, padding, padding, padding)
        self.body_layout.setSpacing(spacing)


class SectionHeader(QWidget):
    """Clean editorial section heading with optional right-aligned action or badge."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        accessory: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self._title_lbl = QLabel(title, self)
        self._title_lbl.setProperty("role", "section-header")
        text_col.addWidget(self._title_lbl)

        self._subtitle_lbl = QLabel(subtitle, self)
        self._subtitle_lbl.setProperty("role", "muted")
        self._subtitle_lbl.setVisible(bool(subtitle))
        text_col.addWidget(self._subtitle_lbl)

        root.addLayout(text_col, 1)
        if accessory is not None:
            root.addWidget(accessory, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_title(self, title: str) -> None:
        self._title_lbl.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle_lbl.setText(subtitle)
        self._subtitle_lbl.setVisible(bool(subtitle))


class HorizontalDivider(QFrame):
    """Subtle 1px horizontal hairline divider."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setFixedHeight(1)


class VerticalDivider(QFrame):
    """Subtle 1px vertical hairline divider."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.VLine)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setFixedWidth(1)


class StatusBadge(QLabel):
    """Soft pastel pill badge supporting lavender, sage, blush, blue, butter, and peach tones."""

    _PALETTE_MAP: dict[str, tuple[str, str]] = {
        "lavender": ("#E8E0F7", "#5943B3"),
        "info": ("#E8E0F7", "#5943B3"),
        "sage": ("#DDEFE8", "#2F6B54"),
        "success": ("#DDEFE8", "#2F6B54"),
        "blush": ("#F7DFE7", "#9E3B50"),
        "danger": ("#F7DFE7", "#9E3B50"),
        "blue": ("#DFEAF8", "#2F5C99"),
        "butter": ("#F8EBCF", "#7D591D"),
        "warning": ("#F8EBCF", "#7D591D"),
        "peach": ("#F4E5D9", "#8B4D2B"),
        "neutral": ("#F5F0E8", "#65626C"),
    }

    def __init__(
        self,
        text: str = "",
        variant: str = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_status(text, variant=variant)

    def set_status(self, text: str, variant: str = "neutral") -> None:
        bg, fg = self._PALETTE_MAP.get(variant, self._PALETTE_MAP["neutral"])
        self.setText(text)
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                border-radius: 9px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
            """
        )


class SearchInput(QLineEdit):
    """Rounded search field with built-in clear button and debounced `search_changed` signal."""

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
        self._timer.timeout.connect(self._emit_search)
        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, _text: str) -> None:
        if self._timer.interval() == 0:
            self._emit_search()
        else:
            self._timer.start()

    def _emit_search(self) -> None:
        self.search_changed.emit(self.text().strip())


class EmptyStateWidget(QWidget):
    """Aesthetic pastel empty-state placeholder with Lucide icon medallion and optional CTA."""

    action_triggered = Signal()

    def __init__(
        self,
        title: str,
        description: str,
        action_text: str = "",
        icon_name: str = "sparkles",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Soft pastel circular icon medallion
        medallion = QLabel(self)
        medallion.setFixedSize(56, 56)
        medallion.setAlignment(Qt.AlignmentFlag.AlignCenter)
        medallion.setStyleSheet(
            "background-color: #E8E0F7; border-radius: 28px;"
        )
        medallion.setPixmap(render_lucide_pixmap(icon_name, color="#6F59C9", size=24))
        layout.addWidget(medallion, 0, Qt.AlignmentFlag.AlignCenter)

        self._title_lbl = QLabel(title, self)
        self._title_lbl.setProperty("role", "section-header")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_lbl)

        self._desc_lbl = QLabel(description, self)
        self._desc_lbl.setProperty("role", "muted")
        self._desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setMaximumWidth(360)
        layout.addWidget(self._desc_lbl)

        self._action_btn = QPushButton(action_text, self)
        self._action_btn.setProperty("variant", "pastel")
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setVisible(bool(action_text))
        self._action_btn.clicked.connect(self.action_triggered.emit)
        layout.addWidget(self._action_btn, 0, Qt.AlignmentFlag.AlignCenter)


def make_button(
    text: str,
    variant: str = "default",
    tooltip: str = "",
    on_click: Callable[[], None] | None = None,
    icon_name: str = "",
    icon_color: str = "#35343B",
    icon_size: int = 15,
    parent: QWidget | None = None,
) -> QPushButton:
    """Factory helper creating a styled QPushButton with optional Lucide vector icon."""
    btn = QPushButton(text, parent)
    if variant != "default":
        btn.setProperty("variant", variant)
    if tooltip:
        btn.setToolTip(tooltip)
    if icon_name:
        btn.setIcon(get_lucide_icon(icon_name, color=icon_color, size=icon_size))
        btn.setIconSize(QSize(icon_size, icon_size))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        btn.clicked.connect(lambda _checked=False: on_click())
    return btn