"""SimpleWriter — Google Docs & MS Word-style multi-page Soft Modern Pastel document editor."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMarginsF, QPoint, QRectF, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QMouseEvent,
    QPageLayout,
    QPageSize,
    QPainter,
    QPaintEvent,
    QPdfWriter,
    QPen,
    QResizeEvent,
    QShortcut,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
    QTextListFormat,
    QTextTableFormat,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from apps.simple_writer.document_model import (
    APP_ID,
    DEFAULT_DOCUMENT_EXTENSION,
    compute_document_stats,
    format_saved_timestamp,
    load_document_into_qtext,
    save_qtext_document,
)
from core.file_manager.file_ops import FileOperationError, sanitize_filename
from core.notifications.notifier import NotificationLevel
from core.themes.tokens import LIGHT_THEME, ThemeTokens
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import ask_confirmation, show_error_dialog
from core.ui.widgets import (
    HorizontalDivider,
    SectionHeader,
    StatusBadge,
    VerticalDivider,
    make_button,
)

logger = logging.getLogger(__name__)

# Standard US Letter page proportions at 96 DPI (8.5 in x 11 in)
_BASE_PAGE_WIDTH = 816
_BASE_PAGE_HEIGHT = 1056
_BASE_SIDE_MARGIN = 68
_BASE_TOP_MARGIN = 64
_BASE_BOTTOM_MARGIN = 64
_BASE_PAGE_GAP = 28

_PROP_NATURAL_TOP = int(QTextFormat.Property.UserProperty) + 101

_FONT_CHOICES: tuple[tuple[str, str], ...] = (
    ("Inter Sans", "Inter, Manrope, Segoe UI Variable Text, Segoe UI, sans-serif"),
    ("Georgia Serif", "Georgia, Times New Roman, serif"),
    ("Calibri Clean", "Calibri, Segoe UI, sans-serif"),
    ("Times New Roman", "Times New Roman, Georgia, serif"),
    ("JetBrains Mono", "JetBrains Mono, Cascadia Code, Consolas, monospace"),
)

_FONT_SIZES: tuple[int, ...] = (9, 10, 11, 12, 13, 14, 16, 18, 20, 24, 28, 36)

_PARAGRAPH_STYLES: tuple[tuple[str, int], ...] = (
    ("Normal text", 0),
    ("Title", 10),
    ("Subtitle", 11),
    ("Heading 1", 1),
    ("Heading 2", 2),
    ("Heading 3", 3),
    ("Quote", 20),
)

_HIGHLIGHT_SWATCHES: tuple[tuple[str, str | None], ...] = (
    ("No Highlight", None),
    ("Soft Lavender", "#E8E0F7"),
    ("Sage Mint", "#DDEFE8"),
    ("Blush Rose", "#F7DFE7"),
    ("Butter Cream", "#F8EBCF"),
    ("Powder Sky", "#DFEAF8"),
    ("Warm Peach", "#F4E5D9"),
)

_ZOOM_LEVELS: tuple[int, ...] = (75, 90, 100, 115, 130, 150)


class DocumentRulerWidget(QWidget):
    """Google Docs / Word horizontal inch & margin ruler centered over the active page."""

    def __init__(self, tokens: ThemeTokens = LIGHT_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._page_width: int = _BASE_PAGE_WIDTH
        self._margin_px: int = _BASE_SIDE_MARGIN
        self.setFixedHeight(24)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def set_page_metrics(self, page_width: int, margin_px: int) -> None:
        self._page_width = max(420, int(page_width))
        self._margin_px = max(24, int(margin_px))
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())

        painter.fillRect(QRectF(0.0, 0.0, w, h), QColor(self._tokens.bg_elevated))

        track_w = min(w - 24.0, float(self._page_width))
        track_left = (w - track_w) / 2.0
        printable_left = track_left + float(self._margin_px)
        printable_w = max(120.0, track_w - (float(self._margin_px) * 2.0))

        painter.fillRect(
            QRectF(printable_left, 0.0, printable_w, h),
            QColor(self._tokens.bg_surface),
        )

        painter.setPen(QPen(QColor(self._tokens.border_subtle), 1.0))
        painter.drawLine(0, int(h - 1), int(w), int(h - 1))

        total_eighths = 56
        step_px = printable_w / float(total_eighths)
        painter.setFont(QFont("Segoe UI Variable Text", 8))

        for idx in range(total_eighths + 1):
            x = printable_left + idx * step_px
            if idx % 8 == 0:
                painter.setPen(QPen(QColor(self._tokens.text_secondary), 1.1))
                painter.drawLine(int(round(x)), 6, int(round(x)), int(h - 2))
                inch_num = idx // 8
                if 0 < inch_num < 7:
                    painter.drawText(
                        QRectF(x - 10.0, 1.0, 20.0, 12.0),
                        Qt.AlignmentFlag.AlignCenter,
                        str(inch_num),
                    )
            elif idx % 4 == 0:
                painter.setPen(QPen(QColor(self._tokens.border_strong), 1.0))
                painter.drawLine(int(round(x)), 12, int(round(x)), int(h - 2))
            else:
                painter.setPen(QPen(QColor(self._tokens.border_subtle), 1.0))
                painter.drawLine(int(round(x)), 15, int(round(x)), int(h - 2))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._tokens.accent_primary))
        painter.drawRect(QRectF(printable_left - 3.0, h - 5.0, 6.0, 4.0))
        painter.drawRect(QRectF(printable_left + printable_w - 3.0, h - 5.0, 6.0, 4.0))

        painter.end()


class PaginatedPageEditor(QTextEdit):
    """QTextEdit enforcing strict page height limits with automatic multi-page flow."""

    def __init__(self, tokens: ThemeTokens = LIGHT_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._zoom_percent: int = 100
        self._page_w: int = _BASE_PAGE_WIDTH
        self._page_h: int = _BASE_PAGE_HEIGHT
        self._side_margin: int = _BASE_SIDE_MARGIN
        self._top_margin: int = _BASE_TOP_MARGIN
        self._bottom_margin: int = _BASE_BOTTOM_MARGIN
        self._page_gap: int = _BASE_PAGE_GAP
        self._page_count: int = 1
        self._is_reflowing: bool = False

        self.setAcceptRichText(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet(
            "QTextEdit { background: transparent; border: none; padding: 0px; }"
        )

        self._apply_page_geometry()
        self.document().contentsChanged.connect(self.reflow_pages)
        self.reflow_pages()

    @property
    def page_width(self) -> int:
        return self._page_w

    @property
    def page_margin(self) -> int:
        return self._side_margin

    @property
    def page_count(self) -> int:
        return self._page_count

    @property
    def page_stride(self) -> int:
        return self._page_h + self._page_gap

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.viewport().update()

    def set_zoom_percent(self, zoom_percent: int) -> None:
        self._zoom_percent = max(50, min(200, int(zoom_percent)))
        self._apply_page_geometry()
        self._recenter_page_margins()
        self.reflow_pages()

    def _apply_page_geometry(self) -> None:
        scale = self._zoom_percent / 100.0
        self._page_w = round(_BASE_PAGE_WIDTH * scale)
        self._page_h = round(_BASE_PAGE_HEIGHT * scale)
        self._side_margin = round(_BASE_SIDE_MARGIN * scale)
        self._top_margin = round(_BASE_TOP_MARGIN * scale)
        self._bottom_margin = round(_BASE_BOTTOM_MARGIN * scale)
        self._page_gap = round(_BASE_PAGE_GAP * scale)

        doc = self.document()
        doc.setPageSize(QSizeF(float(self._page_w), float(self._page_h)))
        doc.setDocumentMargin(float(self._side_margin))
        self.setLineWrapColumnOrWidth(self._page_w)

    def _recenter_page_margins(self) -> None:
        """Center the fixed-width page sheet inside the full-bleed viewport."""
        avail_w = self.width() - (
            self.verticalScrollBar().width() if self.verticalScrollBar().isVisible() else 0
        )
        side_pad = max(24, (avail_w - self._page_w) // 2)
        self.setViewportMargins(side_pad, 22, side_pad, 34)

    def reflow_pages(self) -> None:
        """Enforce strict page height limits so overflowing content moves cleanly onto the next page."""
        if self._is_reflowing:
            return
        self._is_reflowing = True
        try:
            doc = self.document()
            if doc is None:
                return
            layout = doc.documentLayout()
            if layout is None:
                return

            stride = float(self._page_h + self._page_gap)
            top_m = float(self._top_margin)
            bottom_m = float(self._bottom_margin)
            max_printable_h = max(120.0, float(self._page_h) - top_m - bottom_m)

            block = doc.begin()
            while block.isValid():
                bfmt = block.blockFormat()
                natural_top = float(bfmt.property(_PROP_NATURAL_TOP) or 0.0)
                has_explicit_break = (
                    block.blockNumber() > 0
                    and bfmt.pageBreakPolicy() == QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore
                )

                # Reset to natural top margin first so deleting text pulls content back onto earlier pages
                if abs(bfmt.topMargin() - natural_top) > 0.5:
                    reset_fmt = QTextBlockFormat(bfmt)
                    reset_fmt.setTopMargin(natural_top)
                    c = QTextCursor(block)
                    c.joinPreviousEditBlock()
                    c.setBlockFormat(reset_fmt)
                    c.endEditBlock()
                    bfmt = block.blockFormat()

                rect = layout.blockBoundingRect(block)
                block_top = rect.top()
                block_height = rect.height()

                page_idx = max(0, int(math.floor(block_top / stride)))
                page_top_edge = page_idx * stride
                printable_top = page_top_edge + top_m
                printable_bottom = page_top_edge + float(self._page_h) - bottom_m

                target_top = natural_top

                if has_explicit_break:
                    next_printable_top = (page_idx + 1) * stride + top_m
                    target_top = natural_top + (next_printable_top - block_top)
                elif block_top < printable_top - 0.5:
                    target_top = natural_top + (printable_top - block_top)
                elif (block_top + block_height) > (printable_bottom + 0.5) and block_height <= max_printable_h:
                    next_printable_top = (page_idx + 1) * stride + top_m
                    target_top = natural_top + (next_printable_top - block_top)

                if abs(bfmt.topMargin() - target_top) > 0.5:
                    shifted_fmt = QTextBlockFormat(bfmt)
                    shifted_fmt.setTopMargin(target_top)
                    c = QTextCursor(block)
                    c.joinPreviousEditBlock()
                    c.setBlockFormat(shifted_fmt)
                    c.endEditBlock()

                block = block.next()

            doc_bottom = doc.size().height()
            height_pages = max(1, int(math.ceil(max(1.0, doc_bottom) / stride)))

            hard_break_count = 0
            scan_block = doc.begin()
            while scan_block.isValid():
                if (
                    scan_block.blockNumber() > 0
                    and (
                        scan_block.blockFormat().pageBreakPolicy()
                        & QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore
                    )
                ):
                    hard_break_count += 1
                scan_block = scan_block.next()

            self._page_count = max(height_pages, 1 + hard_break_count)
            self.viewport().update()
        finally:
            self._is_reflowing = False

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._recenter_page_margins()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        parent_win = self.window()
        if hasattr(parent_win, "_on_editor_mouse_released"):
            parent_win._on_editor_mouse_released()  # type: ignore[attr-defined]

    def paintEvent(self, event: QPaintEvent) -> None:
        """Render warm Canvas desk, discrete fixed-height Snow pages, Canvas gaps, then text."""
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        vp_rect = self.viewport().rect()
        painter.fillRect(vp_rect, QColor(self._tokens.bg_canvas))

        total_pages = max(1, self._page_count)
        scroll_y = self.verticalScrollBar().value()
        page_w = float(self._page_w)
        page_h = float(self._page_h)
        stride = float(self._page_h + self._page_gap)

        painter.setFont(QFont("Segoe UI Variable Text", 8))

        for page_idx in range(total_pages):
            sheet_top = page_idx * stride - float(scroll_y)
            sheet_bottom = sheet_top + page_h

            if sheet_bottom < 0.0 or sheet_top > float(vp_rect.height()):
                continue

            sheet_rect = QRectF(0.0, sheet_top, page_w - 1.0, page_h - 1.0)

            # Discrete Snow (#FFFEFC) paper sheet with crisp hairline border
            painter.setPen(QPen(QColor(self._tokens.border_strong), 1.0))
            painter.setBrush(QColor(self._tokens.bg_surface))
            painter.drawRect(sheet_rect)

            # Subtle page number in bottom margin
            footer_rect = QRectF(
                float(self._side_margin),
                sheet_bottom - float(self._bottom_margin) + 12.0,
                page_w - float(self._side_margin * 2),
                18.0,
            )
            painter.setPen(QColor(self._tokens.text_muted))
            painter.drawText(
                footer_rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"Page {page_idx + 1} of {total_pages}",
            )

        painter.end()

        # Draw text, tables, images, selections, and caret on top of the discrete pages
        super().paintEvent(event)


class FloatingSelectionToolbar(QFrame):
    """Compact pastel formatting pill that floats gently above selected text."""

    def __init__(self, editor_window: SimpleWriterWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("surface", "card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setVisible(False)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(3)

        specs: tuple[tuple[str, str, object], ...] = (
            ("B", "Bold", editor_window._toggle_bold),
            ("I", "Italic", editor_window._toggle_italic),
            ("U", "Underline", editor_window._toggle_underline),
            ("S", "Strikethrough", editor_window._toggle_strikethrough),
            ("H1", "Heading 1", lambda: editor_window._apply_style_code(1)),
            ("H2", "Heading 2", lambda: editor_window._apply_style_code(2)),
            (
                "Highlight",
                "Soft Lavender Highlight",
                lambda: editor_window._apply_highlight_color("#E8E0F7"),
            ),
            ("Link", "Insert Link (Ctrl+K)", editor_window.insert_hyperlink_prompt),
        )
        for text, tip, handler in specs:
            btn = make_button(
                text=text,
                variant="ghost",
                tooltip=tip,
                on_click=handler,  # type: ignore[arg-type]
                parent=self,
            )
            btn.setStyleSheet("padding: 3px 7px; font-size: 11px; font-weight: 600;")
            row.addWidget(btn)

        self.adjustSize()


class InsertTableDialog(QDialog):
    """Modal dialog for choosing row and column counts when inserting a table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Table")
        self.setModal(True)
        self.setMinimumWidth(300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.rows_spin = QSpinBox(self)
        self.rows_spin.setRange(1, 30)
        self.rows_spin.setValue(3)
        form.addRow("Rows:", self.rows_spin)

        self.cols_spin = QSpinBox(self)
        self.cols_spin.setRange(1, 12)
        self.cols_spin.setValue(3)
        form.addRow("Columns:", self.cols_spin)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SimpleWriterWindow(BaseWindow):
    """Google Docs & MS Word-inspired multi-page document editor (.docx default)."""

    def __init__(self) -> None:
        super().__init__(app_id=APP_ID, min_width=940, min_height=650)
        if self.settings.get("window_width") is None:
            self.resize(1200, 790)

        self._current_file_path: Path | None = None
        self._is_dirty: bool = False
        self._is_loading: bool = False
        self._zen_mode: bool = False
        self._outline_visible: bool = bool(self.settings.get("outline_visible", True))
        self._zoom_percent: int = 100

        saved_font_idx = int(self.settings.get("doc_font_idx", 0))
        saved_font_size = int(self.settings.get("doc_font_size", 12))
        self._doc_font_idx: int = max(0, min(len(_FONT_CHOICES) - 1, saved_font_idx))
        self._doc_font_size: int = saved_font_size if saved_font_size in _FONT_SIZES else 12

        self._build_header_actions()
        self._build_workspace()
        self._bind_shortcuts()

        self._apply_document_typography(persist=False)
        self._sync_outline_visibility()
        self._refresh_telemetry_and_outline()
        self._sync_dirty_badge()

    def _build_header_actions(self) -> None:
        """Mount Google Docs-style editable document title + File & View actions in header."""
        self._doc_title_input = QLineEdit(self)
        self._doc_title_input.setPlaceholderText("Untitled Document")
        self._doc_title_input.setText("Untitled Document")
        self._doc_title_input.setFixedWidth(210)
        self._doc_title_input.setToolTip("Document title")
        self._doc_title_input.setStyleSheet(
            "QLineEdit { padding: 4px 10px; font-size: 13px; font-weight: 600; }"
        )
        self._doc_title_input.textEdited.connect(self._mark_dirty)
        self.header_actions_layout.addWidget(self._doc_title_input)

        self._outline_btn = QPushButton("Outline", self)
        self._outline_btn.setProperty("variant", "segmented")
        self._outline_btn.setCheckable(True)
        self._outline_btn.setChecked(self._outline_visible)
        self._outline_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._outline_btn.setToolTip("Show or hide document headings outline")
        self._outline_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self._outline_btn.clicked.connect(self._toggle_outline_panel)
        self.header_actions_layout.addWidget(self._outline_btn)

        new_btn = make_button(
            text="New",
            variant="ghost",
            tooltip="Start a blank document (Ctrl+N)",
            on_click=self.new_document,
            icon_name="plus",
            icon_size=14,
            parent=self,
        )
        new_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(new_btn)

        open_btn = make_button(
            text="Open",
            variant="ghost",
            tooltip="Open Word (.docx), Markdown, HTML, or Text file (Ctrl+O)",
            on_click=self.open_document,
            icon_name="folder",
            icon_size=14,
            parent=self,
        )
        open_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(open_btn)

        self._save_btn = make_button(
            text="Save",
            variant="pastel",
            tooltip="Save as Word (.docx) document (Ctrl+S)",
            on_click=self.save_document,
            icon_name="check",
            icon_size=14,
            parent=self,
        )
        self._save_btn.setStyleSheet("padding: 4px 11px; font-size: 12px;")
        self.header_actions_layout.addWidget(self._save_btn)

        export_btn = make_button(
            text="Export",
            variant="ghost",
            tooltip="Save As / Export (.docx, .pdf, .md, .html, .txt)",
            on_click=self.export_document_as,
            parent=self,
        )
        export_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(export_btn)

        self._zen_btn = QPushButton("Zen", self)
        self._zen_btn.setProperty("variant", "segmented")
        self._zen_btn.setCheckable(True)
        self._zen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zen_btn.setToolTip("Toggle distraction-free Zen mode (F11)")
        self._zen_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self._zen_btn.clicked.connect(self.toggle_zen_mode)
        self.header_actions_layout.addWidget(self._zen_btn)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ==============================================================
        # 1. Google Docs / MS Word Formatting Ribbon Bar
        # ==============================================================
        self._toolbar_bar = QFrame(central)
        self._toolbar_bar.setProperty("surface", "app-header")
        self._toolbar_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        tb_row = QHBoxLayout(self._toolbar_bar)
        tb_row.setContentsMargins(14, 6, 14, 6)
        tb_row.setSpacing(4)

        undo_btn = make_button(
            text="↶",
            variant="ghost",
            tooltip="Undo (Ctrl+Z)",
            on_click=lambda: self._editor.undo(),
            parent=self._toolbar_bar,
        )
        undo_btn.setStyleSheet("padding: 4px 7px; font-size: 14px;")
        tb_row.addWidget(undo_btn)

        redo_btn = make_button(
            text="↷",
            variant="ghost",
            tooltip="Redo (Ctrl+Y)",
            on_click=lambda: self._editor.redo(),
            parent=self._toolbar_bar,
        )
        redo_btn.setStyleSheet("padding: 4px 7px; font-size: 14px;")
        tb_row.addWidget(redo_btn)

        tb_row.addWidget(VerticalDivider(self._toolbar_bar))

        # Paragraph Styles Dropdown
        self._style_combo = QComboBox(self._toolbar_bar)
        for label, style_code in _PARAGRAPH_STYLES:
            self._style_combo.addItem(label, style_code)
        self._style_combo.setToolTip("Paragraph & heading style")
        self._style_combo.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        self._style_combo.activated.connect(self._on_style_selected)
        tb_row.addWidget(self._style_combo)

        # Font Family & Size
        self._font_combo = QComboBox(self._toolbar_bar)
        for label, _ in _FONT_CHOICES:
            self._font_combo.addItem(label)
        self._font_combo.setCurrentIndex(self._doc_font_idx)
        self._font_combo.setToolTip("Document font family")
        self._font_combo.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        self._font_combo.currentIndexChanged.connect(self._on_font_family_changed)
        tb_row.addWidget(self._font_combo)

        self._size_combo = QComboBox(self._toolbar_bar)
        for sz in _FONT_SIZES:
            self._size_combo.addItem(f"{sz}", sz)
        size_idx = self._size_combo.findData(self._doc_font_size)
        self._size_combo.setCurrentIndex(size_idx if size_idx >= 0 else 3)
        self._size_combo.setToolTip("Font size (pt)")
        self._size_combo.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        self._size_combo.currentIndexChanged.connect(self._on_font_size_changed)
        tb_row.addWidget(self._size_combo)

        tb_row.addWidget(VerticalDivider(self._toolbar_bar))

        # Inline Formatting: B, I, U, S, x₂, x², Text Color, Highlight, Clear Formatting
        self._bold_btn = QPushButton("B", self._toolbar_bar)
        self._bold_btn.setProperty("variant", "segmented")
        self._bold_btn.setCheckable(True)
        self._bold_btn.setToolTip("Bold (Ctrl+B)")
        self._bold_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bold_btn.setStyleSheet("padding: 4px 8px; font-size: 12px; font-weight: 700;")
        self._bold_btn.clicked.connect(self._toggle_bold)
        tb_row.addWidget(self._bold_btn)

        self._italic_btn = QPushButton("I", self._toolbar_bar)
        self._italic_btn.setProperty("variant", "segmented")
        self._italic_btn.setCheckable(True)
        self._italic_btn.setToolTip("Italic (Ctrl+I)")
        self._italic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._italic_btn.setStyleSheet("padding: 4px 9px; font-size: 12px; font-style: italic;")
        self._italic_btn.clicked.connect(self._toggle_italic)
        tb_row.addWidget(self._italic_btn)

        self._underline_btn = QPushButton("U", self._toolbar_bar)
        self._underline_btn.setProperty("variant", "segmented")
        self._underline_btn.setCheckable(True)
        self._underline_btn.setToolTip("Underline (Ctrl+U)")
        self._underline_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._underline_btn.setStyleSheet(
            "padding: 4px 8px; font-size: 12px; text-decoration: underline;"
        )
        self._underline_btn.clicked.connect(self._toggle_underline)
        tb_row.addWidget(self._underline_btn)

        self._strike_btn = QPushButton("S", self._toolbar_bar)
        self._strike_btn.setProperty("variant", "segmented")
        self._strike_btn.setCheckable(True)
        self._strike_btn.setToolTip("Strikethrough")
        self._strike_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._strike_btn.setStyleSheet(
            "padding: 4px 8px; font-size: 12px; text-decoration: line-through;"
        )
        self._strike_btn.clicked.connect(self._toggle_strikethrough)
        tb_row.addWidget(self._strike_btn)

        self._sub_btn = QPushButton("x₂", self._toolbar_bar)
        self._sub_btn.setProperty("variant", "segmented")
        self._sub_btn.setCheckable(True)
        self._sub_btn.setToolTip("Subscript")
        self._sub_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sub_btn.setStyleSheet("padding: 4px 7px; font-size: 11px;")
        self._sub_btn.clicked.connect(self._toggle_subscript)
        tb_row.addWidget(self._sub_btn)

        self._sup_btn = QPushButton("x²", self._toolbar_bar)
        self._sup_btn.setProperty("variant", "segmented")
        self._sup_btn.setCheckable(True)
        self._sup_btn.setToolTip("Superscript")
        self._sup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sup_btn.setStyleSheet("padding: 4px 7px; font-size: 11px;")
        self._sup_btn.clicked.connect(self._toggle_superscript)
        tb_row.addWidget(self._sup_btn)

        text_color_btn = make_button(
            text="A",
            variant="ghost",
            tooltip="Text color",
            on_click=self._choose_text_color,
            parent=self._toolbar_bar,
        )
        text_color_btn.setStyleSheet(
            "padding: 4px 8px; font-size: 12px; font-weight: 700; text-decoration: underline;"
        )
        tb_row.addWidget(text_color_btn)

        highlight_btn = QPushButton("Highlight", self._toolbar_bar)
        highlight_btn.setProperty("variant", "ghost")
        highlight_btn.setProperty("has_menu", True)
        highlight_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        highlight_btn.setToolTip("Pastel text highlight color")
        highlight_btn.setStyleSheet("padding: 4px 22px 4px 8px; font-size: 12px;")
        highlight_menu = QMenu(highlight_btn)
        for swatch_label, hex_code in _HIGHLIGHT_SWATCHES:
            act = highlight_menu.addAction(swatch_label)
            act.triggered.connect(lambda _=False, c=hex_code: self._apply_highlight_color(c))
        highlight_btn.setMenu(highlight_menu)
        tb_row.addWidget(highlight_btn)

        clear_fmt_btn = make_button(
            text="Tx",
            variant="ghost",
            tooltip="Clear formatting on selection",
            on_click=self.clear_selection_formatting,
            parent=self._toolbar_bar,
        )
        clear_fmt_btn.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        tb_row.addWidget(clear_fmt_btn)

        tb_row.addWidget(VerticalDivider(self._toolbar_bar))

        # Alignment & Line Spacing
        align_specs: tuple[tuple[str, Qt.AlignmentFlag, str], ...] = (
            ("Left", Qt.AlignmentFlag.AlignLeft, "Align left"),
            ("Center", Qt.AlignmentFlag.AlignHCenter, "Align center"),
            ("Right", Qt.AlignmentFlag.AlignRight, "Align right"),
            ("Justify", Qt.AlignmentFlag.AlignJustify, "Justify paragraph"),
        )
        for label, align_flag, tip in align_specs:
            abtn = make_button(
                text=label,
                variant="ghost",
                tooltip=tip,
                on_click=lambda _=False, al=align_flag: self._set_alignment(al),
                parent=self._toolbar_bar,
            )
            abtn.setStyleSheet("padding: 4px 6px; font-size: 12px;")
            tb_row.addWidget(abtn)

        spacing_btn = QPushButton("Spacing", self._toolbar_bar)
        spacing_btn.setProperty("variant", "ghost")
        spacing_btn.setProperty("has_menu", True)
        spacing_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        spacing_btn.setToolTip("Line spacing")
        spacing_btn.setStyleSheet("padding: 4px 22px 4px 7px; font-size: 12px;")
        spacing_menu = QMenu(spacing_btn)
        for sp_label, pct in (
            ("Single (1.0)", 100.0),
            ("Relaxed (1.15)", 115.0),
            ("1.5 Lines", 150.0),
            ("Double (2.0)", 200.0),
        ):
            s_act = spacing_menu.addAction(sp_label)
            s_act.triggered.connect(lambda _=False, val=pct: self._set_line_spacing(val))
        spacing_btn.setMenu(spacing_menu)
        tb_row.addWidget(spacing_btn)

        tb_row.addWidget(VerticalDivider(self._toolbar_bar))

        # Lists & Indentation
        bullet_btn = make_button(
            text="• List",
            variant="ghost",
            tooltip="Bulleted list",
            on_click=lambda: self._toggle_list(QTextListFormat.Style.ListDisc),
            parent=self._toolbar_bar,
        )
        bullet_btn.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        tb_row.addWidget(bullet_btn)

        num_btn = make_button(
            text="1. List",
            variant="ghost",
            tooltip="Numbered list",
            on_click=lambda: self._toggle_list(QTextListFormat.Style.ListDecimal),
            parent=self._toolbar_bar,
        )
        num_btn.setStyleSheet("padding: 4px 7px; font-size: 12px;")
        tb_row.addWidget(num_btn)

        outdent_btn = make_button(
            text="⇤",
            variant="ghost",
            tooltip="Decrease indent",
            on_click=lambda: self._adjust_indent(-1),
            parent=self._toolbar_bar,
        )
        outdent_btn.setStyleSheet("padding: 4px 6px; font-size: 13px;")
        tb_row.addWidget(outdent_btn)

        indent_btn = make_button(
            text="⇥",
            variant="ghost",
            tooltip="Increase indent",
            on_click=lambda: self._adjust_indent(1),
            parent=self._toolbar_bar,
        )
        indent_btn.setStyleSheet("padding: 4px 6px; font-size: 13px;")
        tb_row.addWidget(indent_btn)

        tb_row.addWidget(VerticalDivider(self._toolbar_bar))

        # Insert Tools (Page Break, Separator, Table, Link, Image, Date)
        page_break_btn = make_button(
            text="+ Page",
            variant="ghost",
            tooltip="Insert new page break (Ctrl+Enter)",
            on_click=self.insert_page_break,
            parent=self._toolbar_bar,
        )
        page_break_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        tb_row.addWidget(page_break_btn)

        insert_btn = QPushButton("+ Insert", self._toolbar_bar)
        insert_btn.setProperty("variant", "pastel")
        insert_btn.setProperty("has_menu", True)
        insert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        insert_btn.setToolTip("Insert page break, separator, table, link, image, or date")
        insert_btn.setStyleSheet("padding: 4px 24px 4px 10px; font-size: 12px;")
        insert_menu = QMenu(insert_btn)
        insert_menu.addAction("Page Break (Ctrl+Enter)", self.insert_page_break)
        insert_menu.addAction("Horizontal Separator Line", self.insert_horizontal_separator)
        insert_menu.addSeparator()
        insert_menu.addAction("Table (Rows × Columns)...", self.insert_table_prompt)
        insert_menu.addAction("Hyperlink (Ctrl+K)...", self.insert_hyperlink_prompt)
        insert_menu.addAction("Image from File...", self.insert_image_prompt)
        insert_menu.addSeparator()
        insert_menu.addAction("Current Date & Time", self.insert_current_datetime)
        insert_btn.setMenu(insert_menu)
        tb_row.addWidget(insert_btn)

        tb_row.addStretch(1)
        outer.addWidget(self._toolbar_bar)

        # ==============================================================
        # 2. Inline Find & Replace Bar (Hidden until Ctrl+F)
        # ==============================================================
        self._find_bar = QFrame(central)
        self._find_bar.setProperty("surface", "app-header")
        self._find_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._find_bar.setVisible(False)
        find_row = QHBoxLayout(self._find_bar)
        find_row.setContentsMargins(16, 6, 16, 6)
        find_row.setSpacing(6)

        self._find_input = QLineEdit(self._find_bar)
        self._find_input.setPlaceholderText("Find in document...")
        self._find_input.returnPressed.connect(self.find_next)
        find_row.addWidget(self._find_input, 2)

        self._replace_input = QLineEdit(self._find_bar)
        self._replace_input.setPlaceholderText("Replace with...")
        find_row.addWidget(self._replace_input, 2)

        find_row.addWidget(
            make_button(text="Next", variant="ghost", on_click=self.find_next, parent=self._find_bar)
        )
        find_row.addWidget(
            make_button(
                text="Replace",
                variant="ghost",
                on_click=self.replace_current,
                parent=self._find_bar,
            )
        )
        find_row.addWidget(
            make_button(
                text="Replace All",
                variant="pastel",
                on_click=self.replace_all,
                parent=self._find_bar,
            )
        )
        find_row.addWidget(
            make_button(
                text="✕",
                variant="ghost",
                tooltip="Close Find & Replace (Esc)",
                on_click=self.hide_find_bar,
                parent=self._find_bar,
            )
        )
        outer.addWidget(self._find_bar)

        # ==============================================================
        # 3. Main Studio Area: Docked Outline Sidebar + Multi-Page Desk
        # ==============================================================
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)

        # Left Docked Outline Sidebar
        self._outline_sidebar = QFrame(central)
        self._outline_sidebar.setProperty("surface", "sidebar")
        self._outline_sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._outline_sidebar.setFixedWidth(215)

        outline_layout = QVBoxLayout(self._outline_sidebar)
        outline_layout.setContentsMargins(16, 16, 14, 16)
        outline_layout.setSpacing(10)

        outline_layout.addWidget(SectionHeader("Outline", parent=self._outline_sidebar))
        outline_layout.addWidget(HorizontalDivider(self._outline_sidebar))

        self._outline_list = QListWidget(self._outline_sidebar)
        self._outline_list.setWordWrap(True)
        self._outline_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; padding: 0px; }"
        )
        self._outline_list.itemClicked.connect(self._on_outline_item_clicked)
        outline_layout.addWidget(self._outline_list, 1)

        self._outline_empty_lbl = QLabel(
            "Headings (H1–H3) you add to your pages appear here for instant navigation.",
            self._outline_sidebar,
        )
        self._outline_empty_lbl.setProperty("role", "muted")
        self._outline_empty_lbl.setWordWrap(True)
        outline_layout.addWidget(self._outline_empty_lbl)

        self._outline_spacer = QWidget(self._outline_sidebar)
        outline_layout.addWidget(self._outline_spacer, 1)

        body_row.addWidget(self._outline_sidebar)

        # Right Column: Horizontal Ruler + Multi-Page Paginated Editor
        desk_col = QVBoxLayout()
        desk_col.setContentsMargins(0, 0, 0, 0)
        desk_col.setSpacing(0)

        self._ruler = DocumentRulerWidget(tokens=self.theme_manager.tokens, parent=central)
        desk_col.addWidget(self._ruler)

        self._editor = PaginatedPageEditor(tokens=self.theme_manager.tokens, parent=central)
        self._editor.setPlaceholderText("Click on Page 1 and start typing your document...")
        self._editor.textChanged.connect(self._on_editor_text_changed)
        self._editor.cursorPositionChanged.connect(self._sync_format_buttons)
        self._editor.selectionChanged.connect(self._on_editor_selection_changed)
        desk_col.addWidget(self._editor, 1)

        self._floating_toolbar = FloatingSelectionToolbar(self, parent=self._editor.viewport())

        body_row.addLayout(desk_col, 1)
        outer.addLayout(body_row, 1)

        # ==============================================================
        # 4. Bottom Document Status Bar (Page X of Y, Words, Zoom)
        # ==============================================================
        footer_bar = QFrame(central)
        footer_bar.setProperty("surface", "footer-bar")
        footer_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_bar.setFixedHeight(36)

        footer_row = QHBoxLayout(footer_bar)
        footer_row.setContentsMargins(16, 4, 16, 4)
        footer_row.setSpacing(10)

        self._status_badge = StatusBadge("New Draft (.docx)", variant="lavender", parent=footer_bar)
        footer_row.addWidget(self._status_badge)

        self._file_path_lbl = QLabel("Not yet saved to disk", footer_bar)
        self._file_path_lbl.setProperty("role", "muted")
        footer_row.addWidget(self._file_path_lbl)

        footer_row.addStretch(1)

        self._stats_lbl = QLabel("Page 1 of 1  •  0 words  •  0 chars  •  0 min read", footer_bar)
        self._stats_lbl.setProperty("role", "muted")
        footer_row.addWidget(self._stats_lbl)

        footer_row.addWidget(VerticalDivider(footer_bar))

        zoom_out_btn = make_button(
            text="−",
            variant="ghost",
            tooltip="Zoom out",
            on_click=lambda: self._step_zoom(-1),
            parent=footer_bar,
        )
        zoom_out_btn.setStyleSheet("padding: 2px 7px; font-size: 13px;")
        footer_row.addWidget(zoom_out_btn)

        self._zoom_combo = QComboBox(footer_bar)
        for z in _ZOOM_LEVELS:
            self._zoom_combo.addItem(f"{z}%", z)
        self._zoom_combo.setCurrentIndex(2)
        self._zoom_combo.setToolTip("Page zoom level")
        self._zoom_combo.setStyleSheet("padding: 2px 8px; font-size: 11px;")
        self._zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        footer_row.addWidget(self._zoom_combo)

        zoom_in_btn = make_button(
            text="+",
            variant="ghost",
            tooltip="Zoom in",
            on_click=lambda: self._step_zoom(1),
            parent=footer_bar,
        )
        zoom_in_btn.setStyleSheet("padding: 2px 7px; font-size: 13px;")
        footer_row.addWidget(zoom_in_btn)

        outer.addWidget(footer_bar)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.New, self).activated.connect(self.new_document)
        QShortcut(QKeySequence.StandardKey.Open, self).activated.connect(self.open_document)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self.save_document)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self).activated.connect(self.export_document_as)
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self.show_find_bar)
        QShortcut(QKeySequence("Ctrl+H"), self).activated.connect(self.show_find_bar)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self._handle_escape)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self._toggle_bold)
        QShortcut(QKeySequence("Ctrl+I"), self).activated.connect(self._toggle_italic)
        QShortcut(QKeySequence("Ctrl+U"), self).activated.connect(self._toggle_underline)
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self.insert_hyperlink_prompt)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self.insert_page_break)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self).activated.connect(self.toggle_zen_mode)

    def _handle_escape(self) -> None:
        self._floating_toolbar.setVisible(False)
        self.hide_find_bar()

    # ------------------------------------------------------------------
    # Floating Selection Mini-Toolbar
    # ------------------------------------------------------------------

    def _on_editor_selection_changed(self) -> None:
        cursor = self._editor.textCursor()
        if not cursor.hasSelection():
            self._floating_toolbar.setVisible(False)

    def _on_editor_mouse_released(self) -> None:
        cursor = self._editor.textCursor()
        if not cursor.hasSelection() or not cursor.selectedText().strip():
            self._floating_toolbar.setVisible(False)
            return

        sel_rect = self._editor.cursorRect(cursor)
        vp = self._editor.viewport()
        self._floating_toolbar.adjustSize()

        bar_w = self._floating_toolbar.width()
        bar_h = self._floating_toolbar.height()

        target_x = max(8, min(vp.width() - bar_w - 8, sel_rect.center().x() - bar_w // 2))
        target_y = sel_rect.top() - bar_h - 10
        if target_y < 8:
            target_y = sel_rect.bottom() + 10

        self._floating_toolbar.move(QPoint(target_x, target_y))
        self._floating_toolbar.setVisible(True)
        self._floating_toolbar.raise_()

    # ------------------------------------------------------------------
    # Outline Panel, Zoom & Zen Mode
    # ------------------------------------------------------------------

    def _toggle_outline_panel(self) -> None:
        self._outline_visible = not self._outline_visible
        self._outline_btn.setChecked(self._outline_visible)
        self.settings.set("outline_visible", self._outline_visible)
        self._sync_outline_visibility()

    def _sync_outline_visibility(self) -> None:
        self._outline_sidebar.setVisible(self._outline_visible and not self._zen_mode)

    def _on_outline_item_clicked(self, item: QListWidgetItem) -> None:
        block_num = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        doc = self._editor.document()
        if doc is None:
            return
        block = doc.findBlockByNumber(block_num)
        if block.isValid():
            self._editor.setTextCursor(QTextCursor(block))
            self._editor.setFocus()

    def _step_zoom(self, delta: int) -> None:
        next_idx = max(0, min(len(_ZOOM_LEVELS) - 1, self._zoom_combo.currentIndex() + delta))
        self._zoom_combo.setCurrentIndex(next_idx)

    def _on_zoom_changed(self, idx: int) -> None:
        zoom_val = self._zoom_combo.itemData(idx)
        if not isinstance(zoom_val, int):
            return
        self._zoom_percent = zoom_val
        self._editor.set_zoom_percent(zoom_val)
        self._ruler.set_page_metrics(self._editor.page_width, self._editor.page_margin)
        self._refresh_telemetry_and_outline(update_outline=False)

    def toggle_zen_mode(self) -> None:
        """Toggle distraction-free Zen writing mode (F11)."""
        self._zen_mode = not self._zen_mode
        self._zen_btn.setChecked(self._zen_mode)
        self._toolbar_bar.setVisible(not self._zen_mode)
        self._ruler.setVisible(not self._zen_mode)
        self._sync_outline_visibility()
        self.set_status_message(
            "Zen mode active (F11 to exit)" if self._zen_mode else "Standard Studio view",
            timeout_ms=2200,
        )

    # ------------------------------------------------------------------
    # Paragraph Styles, Typography & Text Selection Formatting
    # ------------------------------------------------------------------

    def _on_font_family_changed(self, idx: int) -> None:
        self._doc_font_idx = max(0, min(len(_FONT_CHOICES) - 1, idx))
        _, css_stack = _FONT_CHOICES[self._doc_font_idx]
        primary_family = css_stack.split(",")[0].strip()

        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            fmt = QTextCharFormat()
            fmt.setFontFamilies([primary_family])
            self._editor.mergeCurrentCharFormat(fmt)
            self._editor.setFocus()
        else:
            self._apply_document_typography(persist=True)

    def _on_font_size_changed(self, idx: int) -> None:
        size_val = self._size_combo.itemData(idx)
        if not isinstance(size_val, int):
            return
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            fmt = QTextCharFormat()
            fmt.setFontPointSize(float(size_val))
            self._editor.mergeCurrentCharFormat(fmt)
            self._editor.setFocus()
        else:
            self._doc_font_size = size_val
            self._apply_document_typography(persist=True)

    def _apply_document_typography(self, persist: bool = True) -> None:
        _, css_stack = _FONT_CHOICES[self._doc_font_idx]
        primary_family = css_stack.split(",")[0].strip()

        doc_font = QFont(primary_family)
        doc_font.setPointSize(self._doc_font_size)
        self._editor.setFont(doc_font)
        self._editor.document().setDefaultFont(doc_font)
        self._editor.reflow_pages()

        if persist:
            self.settings.set("doc_font_idx", self._doc_font_idx)
            self.settings.set("doc_font_size", self._doc_font_size)

    def _on_style_selected(self, idx: int) -> None:
        style_code = int(self._style_combo.itemData(idx) or 0)
        self._apply_style_code(style_code)

    def _apply_style_code(self, style_code: int) -> None:
        cursor = self._editor.textCursor()
        cursor.beginEditBlock()

        block_fmt = cursor.blockFormat()
        block_cursor = QTextCursor(cursor)
        if not block_cursor.hasSelection():
            block_cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        char_fmt = QTextCharFormat()

        if style_code == 20:  # Quote
            block_fmt.setHeadingLevel(0)
            block_fmt.setLeftMargin(28.0)
            char_fmt.setFontItalic(True)
            char_fmt.setForeground(QColor(self.theme_manager.tokens.text_secondary))
        else:
            heading_lvl = style_code if style_code in {1, 2, 3} else 0
            block_fmt.setHeadingLevel(heading_lvl)
            block_fmt.setLeftMargin(0.0)
            size_map = {
                0: float(self._doc_font_size),
                10: float(self._doc_font_size + 14),
                11: float(self._doc_font_size + 4),
                1: float(self._doc_font_size + 9),
                2: float(self._doc_font_size + 5),
                3: float(self._doc_font_size + 2),
            }
            char_fmt.setFontPointSize(size_map.get(style_code, float(self._doc_font_size)))
            char_fmt.setFontWeight(
                QFont.Weight.Bold if style_code in {10, 1, 2, 3} else QFont.Weight.Normal
            )
            char_fmt.setFontItalic(style_code == 11)
            char_fmt.setForeground(
                QColor(
                    self.theme_manager.tokens.text_secondary
                    if style_code == 11
                    else self.theme_manager.tokens.text_primary
                )
            )

        cursor.setBlockFormat(block_fmt)
        block_cursor.mergeCharFormat(char_fmt)
        cursor.endEditBlock()
        self._editor.reflow_pages()
        self._refresh_telemetry_and_outline()
        self._editor.setFocus()

    def _toggle_bold(self) -> None:
        fmt = QTextCharFormat()
        is_bold = self._editor.fontWeight() >= QFont.Weight.Bold
        fmt.setFontWeight(QFont.Weight.Normal if is_bold else QFont.Weight.Bold)
        self._editor.mergeCurrentCharFormat(fmt)
        self._sync_format_buttons()
        self._editor.setFocus()

    def _toggle_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self._editor.fontItalic())
        self._editor.mergeCurrentCharFormat(fmt)
        self._sync_format_buttons()
        self._editor.setFocus()

    def _toggle_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self._editor.fontUnderline())
        self._editor.mergeCurrentCharFormat(fmt)
        self._sync_format_buttons()
        self._editor.setFocus()

    def _toggle_strikethrough(self) -> None:
        fmt = QTextCharFormat()
        current_strike = self._editor.currentCharFormat().fontStrikeOut()
        fmt.setFontStrikeOut(not current_strike)
        self._editor.mergeCurrentCharFormat(fmt)
        self._sync_format_buttons()
        self._editor.setFocus()

    def _toggle_subscript(self) -> None:
        fmt = QTextCharFormat()
        current = self._editor.currentCharFormat().verticalAlignment()
        next_align = (
            QTextCharFormat.VerticalAlignment.AlignNormal
            if current == QTextCharFormat.VerticalAlignment.AlignSubScript
            else QTextCharFormat.VerticalAlignment.AlignSubScript
        )
        fmt.setVerticalAlignment(next_align)
        self._editor.mergeCurrentCharFormat(fmt)
        self._sync_format_buttons()
        self._editor.setFocus()

    def _toggle_superscript(self) -> None:
        fmt = QTextCharFormat()
        current = self._editor.currentCharFormat().verticalAlignment()
        next_align = (
            QTextCharFormat.VerticalAlignment.AlignNormal
            if current == QTextCharFormat.VerticalAlignment.AlignSuperScript
            else QTextCharFormat.VerticalAlignment.AlignSuperScript
        )
        fmt.setVerticalAlignment(next_align)
        self._editor.mergeCurrentCharFormat(fmt)
        self._sync_format_buttons()
        self._editor.setFocus()

    def _choose_text_color(self) -> None:
        chosen = QColorDialog.getColor(
            QColor(self.theme_manager.tokens.text_primary),
            self,
            "Choose Text Color",
        )
        if chosen.isValid():
            fmt = QTextCharFormat()
            fmt.setForeground(chosen)
            self._editor.mergeCurrentCharFormat(fmt)
        self._editor.setFocus()

    def _apply_highlight_color(self, hex_code: str | None) -> None:
        fmt = QTextCharFormat()
        if hex_code is None:
            fmt.clearBackground()
        else:
            fmt.setBackground(QColor(hex_code))
        self._editor.mergeCurrentCharFormat(fmt)
        self._editor.setFocus()

    def clear_selection_formatting(self) -> None:
        """Reset selected text or active block back to clean default typography."""
        cursor = self._editor.textCursor()
        cursor.beginEditBlock()
        clean_char = QTextCharFormat()
        clean_char.setFont(self._editor.document().defaultFont())
        clean_char.setForeground(QColor(self.theme_manager.tokens.text_primary))
        cursor.setCharFormat(clean_char)

        block_fmt = cursor.blockFormat()
        block_fmt.setHeadingLevel(0)
        block_fmt.setLeftMargin(0.0)
        block_fmt.setIndent(0)
        cursor.setBlockFormat(block_fmt)
        cursor.endEditBlock()
        self._editor.reflow_pages()
        self._sync_format_buttons()
        self._editor.setFocus()

    def _set_alignment(self, alignment: Qt.AlignmentFlag) -> None:
        self._editor.setAlignment(alignment)
        self._editor.setFocus()

    def _set_line_spacing(self, percent: float) -> None:
        cursor = self._editor.textCursor()
        block_fmt = cursor.blockFormat()
        block_fmt.setLineHeight(
            percent,
            QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
        )
        cursor.setBlockFormat(block_fmt)
        self._editor.reflow_pages()
        self._editor.setFocus()

    def _toggle_list(self, style: QTextListFormat.Style) -> None:
        cursor = self._editor.textCursor()
        current_list = cursor.currentList()
        if current_list is not None and current_list.format().style() == style:
            block_fmt = cursor.blockFormat()
            block_fmt.setObjectIndex(-1)
            cursor.setBlockFormat(block_fmt)
        else:
            list_fmt = QTextListFormat()
            list_fmt.setStyle(style)
            cursor.createList(list_fmt)
        self._editor.reflow_pages()
        self._editor.setFocus()

    def _adjust_indent(self, delta: int) -> None:
        cursor = self._editor.textCursor()
        block_fmt = cursor.blockFormat()
        block_fmt.setIndent(max(0, block_fmt.indent() + delta))
        cursor.setBlockFormat(block_fmt)
        self._editor.setFocus()

    def _sync_format_buttons(self) -> None:
        char_fmt = self._editor.currentCharFormat()
        v_align = char_fmt.verticalAlignment()
        self._bold_btn.setChecked(self._editor.fontWeight() >= QFont.Weight.Bold)
        self._italic_btn.setChecked(self._editor.fontItalic())
        self._underline_btn.setChecked(self._editor.fontUnderline())
        self._strike_btn.setChecked(char_fmt.fontStrikeOut())
        self._sub_btn.setChecked(v_align == QTextCharFormat.VerticalAlignment.AlignSubScript)
        self._sup_btn.setChecked(v_align == QTextCharFormat.VerticalAlignment.AlignSuperScript)

        self._refresh_telemetry_and_outline(update_outline=False)

    # ------------------------------------------------------------------
    # Word / Google Docs Insert Tools (Page Break, Separator, Table, Link, Image)
    # ------------------------------------------------------------------

    def insert_page_break(self) -> None:
        """Insert a hard page break so content immediately continues onto the next physical page."""
        cursor = self._editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertBlock()
        next_block_fmt = cursor.blockFormat()
        next_block_fmt.setPageBreakPolicy(QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore)
        cursor.setBlockFormat(next_block_fmt)
        cursor.endEditBlock()

        self._editor.reflow_pages()
        self._editor.ensureCursorVisible()
        self._refresh_telemetry_and_outline(update_outline=False)
        self._editor.setFocus()

    def insert_horizontal_separator(self) -> None:
        """Insert a crisp horizontal section separator rule across the page."""
        border_hex = self.theme_manager.tokens.border_strong
        cursor = self._editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertHtml(
            f'<hr style="border: none; border-top: 1px solid {border_hex}; margin: 14px 0;" /><p></p>'
        )
        cursor.endEditBlock()
        self._editor.reflow_pages()
        self._editor.setFocus()

    def insert_hyperlink_prompt(self) -> None:
        """Turn selected text (or new link text) into a styled clickable hyperlink."""
        cursor = self._editor.textCursor()
        selected_text = cursor.selectedText().strip()
        url_text, ok = QInputDialog.getText(
            self,
            "Insert Hyperlink",
            "URL (https://...):",
            text="https://",
        )
        if not ok or not url_text.strip():
            return

        clean_url = url_text.strip()
        display_text = selected_text or clean_url

        link_fmt = QTextCharFormat()
        link_fmt.setAnchor(True)
        link_fmt.setAnchorHref(clean_url)
        link_fmt.setForeground(QColor(self.theme_manager.tokens.accent_primary))
        link_fmt.setFontUnderline(True)

        cursor.insertText(display_text, link_fmt)
        self._editor.setFocus()

    def insert_table_prompt(self) -> None:
        dialog = InsertTableDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.insert_table(rows=dialog.rows_spin.value(), cols=dialog.cols_spin.value())

    def insert_table(self, rows: int = 3, cols: int = 3) -> None:
        tokens = self.theme_manager.tokens
        cursor = self._editor.textCursor()
        table_fmt = QTextTableFormat()
        table_fmt.setBorder(1.0)
        table_fmt.setBorderBrush(QColor(tokens.border_strong))
        table_fmt.setCellPadding(8.0)
        table_fmt.setCellSpacing(0.0)
        table_fmt.setHeaderRowCount(1)

        table = cursor.insertTable(max(1, rows), max(1, cols), table_fmt)
        header_fmt = QTextCharFormat()
        header_fmt.setFontWeight(QFont.Weight.Bold)
        for col in range(table.columns()):
            cell = table.cellAt(0, col)
            cell_fmt = cell.format()
            cell_fmt.setBackground(QColor(tokens.bg_elevated))
            cell.setFormat(cell_fmt)
            cell_cursor = cell.firstCursorPosition()
            cell_cursor.mergeCharFormat(header_fmt)

        self._editor.reflow_pages()
        self._editor.setFocus()

    def insert_image_prompt(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Insert Image",
            str(Path.home() / "Pictures"),
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not selected_path:
            return

        img_fmt = QTextImageFormat()
        img_fmt.setName(selected_path)
        img_fmt.setWidth(520.0)
        self._editor.textCursor().insertImage(img_fmt)
        self._editor.reflow_pages()
        self._editor.setFocus()

    def insert_current_datetime(self) -> None:
        stamp = datetime.now().strftime("%B %d, %Y at %H:%M")
        self._editor.textCursor().insertText(stamp)
        self._editor.setFocus()

    # ------------------------------------------------------------------
    # Inline Find & Replace
    # ------------------------------------------------------------------

    def show_find_bar(self) -> None:
        self._find_bar.setVisible(True)
        selected = self._editor.textCursor().selectedText()
        if selected:
            self._find_input.setText(selected)
        self._find_input.setFocus()
        self._find_input.selectAll()

    def hide_find_bar(self) -> None:
        if self._find_bar.isVisible():
            self._find_bar.setVisible(False)
            self._editor.setFocus()

    def find_next(self) -> bool:
        needle = self._find_input.text()
        if not needle:
            return False
        found = self._editor.find(needle)
        if not found:
            cursor = self._editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._editor.setTextCursor(cursor)
            found = self._editor.find(needle)
        if not found:
            self.set_status_message(f"'{needle}' not found", timeout_ms=2000)
        return found

    def replace_current(self) -> None:
        needle = self._find_input.text()
        if not needle:
            return
        cursor = self._editor.textCursor()
        if cursor.selectedText() == needle:
            cursor.insertText(self._replace_input.text())
        self.find_next()

    def replace_all(self) -> None:
        needle = self._find_input.text()
        if not needle:
            return
        replacement = self._replace_input.text()

        cursor = self._editor.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._editor.setTextCursor(cursor)

        count = 0
        while self._editor.find(needle):
            self._editor.textCursor().insertText(replacement)
            count += 1
        cursor.endEditBlock()
        self.set_status_message(f"Replaced {count} occurrence(s)", timeout_ms=2200)

    # ------------------------------------------------------------------
    # Telemetry, Outline Sync, Open, Save & Export (.docx default)
    # ------------------------------------------------------------------

    def _on_editor_text_changed(self) -> None:
        self._refresh_telemetry_and_outline(update_outline=True)
        self._mark_dirty()

    def _mark_dirty(self, *_args: object) -> None:
        if self._is_loading:
            return
        if not self._is_dirty:
            self._is_dirty = True
            self._sync_dirty_badge()

    def _sync_dirty_badge(self) -> None:
        title_display = self._doc_title_input.text().strip() or "Untitled Document"
        dirty_dot = " •" if self._is_dirty else ""
        self.setWindowTitle(f"{title_display}{dirty_dot} — {self.meta.display_name}")

        if self._is_dirty:
            self._status_badge.set_status("Unsaved changes", variant="butter")
        elif self._current_file_path is not None:
            self._status_badge.set_status(f"Saved {format_saved_timestamp()}", variant="sage")
        else:
            self._status_badge.set_status("New Draft (.docx)", variant="lavender")

        if self._current_file_path is not None:
            self._file_path_lbl.setText(self._current_file_path.name)
        else:
            self._file_path_lbl.setText("Default format: Word Document (.docx)")

    def _refresh_telemetry_and_outline(self, update_outline: bool = True) -> None:
        stats = compute_document_stats(self._editor.toPlainText())
        total_pages = max(1, self._editor.page_count)

        cursor_rect = self._editor.cursorRect()
        doc_y = cursor_rect.top() + self._editor.verticalScrollBar().value()
        stride = max(1, self._editor.page_stride)
        current_page = max(1, min(total_pages, math.floor(max(0, doc_y) / stride) + 1))

        word_label = "word" if stats.words == 1 else "words"
        self._stats_lbl.setText(
            f"Page {current_page} of {total_pages}  •  {stats.words} {word_label}  •  "
            f"{stats.characters} chars  •  {stats.reading_time_minutes} min read"
        )

        if not update_outline:
            return

        doc = self._editor.document()
        self._outline_list.clear()
        if doc is not None:
            block = doc.begin()
            while block.isValid():
                lvl = block.blockFormat().headingLevel()
                text = block.text().strip()
                if lvl > 0 and text:
                    indent = "  " * (lvl - 1)
                    item = QListWidgetItem(f"{indent}{text[:42]}")
                    item.setData(Qt.ItemDataRole.UserRole, block.blockNumber())
                    self._outline_list.addItem(item)
                block = block.next()

        has_headings = self._outline_list.count() > 0
        self._outline_list.setVisible(has_headings)
        self._outline_empty_lbl.setVisible(not has_headings)
        self._outline_spacer.setVisible(not has_headings)

    def _confirm_discard_if_dirty(self) -> bool:
        if not self._is_dirty:
            return True
        return ask_confirmation(
            parent=self,
            title="Discard Unsaved Changes?",
            message="You have unsaved changes in the current document. Continue without saving?",
            confirm_text="Discard Changes",
            destructive=True,
        )

    def new_document(self) -> None:
        if not self._confirm_discard_if_dirty():
            return

        self._is_loading = True
        try:
            self._current_file_path = None
            self._is_dirty = False
            self._doc_title_input.setText("Untitled Document")
            self._editor.clear()
            self._apply_document_typography(persist=False)
        finally:
            self._is_loading = False

        self._editor.reflow_pages()
        self._refresh_telemetry_and_outline(update_outline=True)
        self._sync_dirty_badge()
        self._editor.setFocus()
        self.set_status_message("Started new .docx document", timeout_ms=1800)

    def open_document(self) -> None:
        if not self._confirm_discard_if_dirty():
            return

        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Document",
            str(Path.home() / "Documents"),
            (
                "All Supported Documents (*.docx *.md *.markdown *.html *.htm *.txt);;"
                "Word Document (*.docx);;"
                "Markdown (*.md);;"
                "HTML (*.html);;"
                "Plain Text (*.txt)"
            ),
        )
        if not selected_path:
            return

        self.load_file(selected_path)

    def load_file(self, file_path: str | Path) -> bool:
        self._is_loading = True
        try:
            loaded_path, inferred_title = load_document_into_qtext(
                file_path,
                self._editor.document(),
            )
            self._current_file_path = loaded_path
            self._doc_title_input.setText(inferred_title)
            self._is_dirty = False
        except FileOperationError as exc:
            show_error_dialog(self, "Could Not Open Document", str(exc))
            return False
        finally:
            self._is_loading = False

        self._editor.reflow_pages()
        self._refresh_telemetry_and_outline(update_outline=True)
        self._sync_dirty_badge()
        self.set_status_message(f"Opened {loaded_path.name}", timeout_ms=2200)
        return True

    def save_document(self) -> bool:
        if self._current_file_path is None:
            return self.export_document_as(set_as_active=True)

        return self._write_to_path(self._current_file_path, set_as_active=True)

    def export_document_as(self, set_as_active: bool = False) -> bool:
        stem = sanitize_filename(self._doc_title_input.text().strip() or "Untitled_Document")
        default_dir = (
            self._current_file_path.parent
            if self._current_file_path is not None
            else (Path.home() / "Documents")
        )
        default_target = str(default_dir / f"{stem}{DEFAULT_DOCUMENT_EXTENSION}")

        selected_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save or Export Document",
            default_target,
            (
                "Word Document (*.docx);;"
                "PDF Document (*.pdf);;"
                "Markdown Document (*.md);;"
                "Rich HTML Document (*.html);;"
                "Plain Text File (*.txt)"
            ),
        )
        if not selected_path:
            return False

        target = Path(selected_path)
        if not target.suffix:
            if "PDF" in selected_filter:
                target = target.with_suffix(".pdf")
            elif "Markdown" in selected_filter:
                target = target.with_suffix(".md")
            elif "HTML" in selected_filter:
                target = target.with_suffix(".html")
            elif "Plain Text" in selected_filter:
                target = target.with_suffix(".txt")
            else:
                target = target.with_suffix(DEFAULT_DOCUMENT_EXTENSION)

        if target.suffix.lower() == ".pdf":
            return self._export_to_pdf(target)

        should_bind = set_as_active or target.suffix.lower() in {".docx", ".md", ".html", ".txt"}
        return self._write_to_path(target, set_as_active=should_bind)

    def _write_to_path(self, target: Path, set_as_active: bool) -> bool:
        title = self._doc_title_input.text().strip() or "Untitled Document"
        try:
            saved_path = save_qtext_document(
                destination=target,
                title=title,
                source_doc=self._editor.document(),
            )
        except FileOperationError as exc:
            show_error_dialog(self, "Save Failed", str(exc))
            return False

        if set_as_active:
            self._current_file_path = saved_path
            self._is_dirty = False
            self._sync_dirty_badge()

        self.notify(
            "Document Saved",
            f"Saved '{saved_path.name}' cleanly.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def _export_to_pdf(self, target: Path) -> bool:
        title = self._doc_title_input.text().strip() or "Untitled Document"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            writer = QPdfWriter(str(target))
            writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
            writer.setPageMargins(QMarginsF(18.0, 18.0, 18.0, 18.0), QPageLayout.Unit.Millimeter)
            writer.setTitle(title)
            writer.setCreator("SimpleWriter — Jaiho Super Simple Office")

            print_doc = QTextDocument()
            print_doc.setDefaultFont(self._editor.document().defaultFont())
            print_doc.setHtml(self._editor.document().toHtml())

            painter = QPainter(writer)
            try:
                print_doc.drawContents(painter)
            finally:
                painter.end()
        except Exception as exc:
            show_error_dialog(self, "PDF Export Failed", str(exc))
            return False

        self.notify(
            "PDF Exported",
            f"Exported '{target.name}' successfully.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def can_close(self) -> bool:
        return self._confirm_discard_if_dirty()

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        if hasattr(self, "_ruler"):
            self._ruler.set_tokens(self.theme_manager.tokens)
        if hasattr(self, "_editor"):
            self._editor.set_tokens(self.theme_manager.tokens)


def main() -> int:
    """Application entry point for SimpleWriter.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleWriterWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())