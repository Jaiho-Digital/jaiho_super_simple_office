"""SimpleSheets — Soft Modern Pastel spreadsheet & pastel chart application."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QModelIndex, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.simple_sheets.formula_engine import (
    CellCoord,
    col_index_to_label,
    format_cell_display,
    format_cell_ref,
    parse_cell_ref,
)
from apps.simple_sheets.sheet_model import (
    APP_ID,
    DEFAULT_WORKBOOK_EXTENSION,
    CellData,
    SheetModel,
    WorkbookModel,
    load_workbook,
    save_workbook,
)
from core.file_manager.file_ops import FileOperationError, sanitize_filename
from core.notifications.notifier import NotificationLevel
from core.themes.tokens import LIGHT_THEME, ThemeTokens
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import ask_confirmation, show_error_dialog
from core.ui.widgets import (
    CardFrame,
    StatusBadge,
    VerticalDivider,
    make_button,
)

logger = logging.getLogger(__name__)

_NUMBER_FORMATS: tuple[tuple[str, str], ...] = (
    ("General", "general"),
    ("Number (1,234.56)", "number"),
    ("Currency ($1,234.56)", "currency"),
    ("Percent (12.50%)", "percent"),
    ("Integer (1,235)", "integer"),
)

_CELL_TINT_SWATCHES: tuple[tuple[str, str], ...] = (
    ("Clear Fill", ""),
    ("Soft Lavender", "#E8E0F7"),
    ("Sage Mint", "#DDEFE8"),
    ("Blush Rose", "#F7DFE7"),
    ("Powder Sky", "#DFEAF8"),
    ("Butter Cream", "#F8EBCF"),
    ("Warm Peach", "#F4E5D9"),
)

_CHART_PASTEL_PALETTE: tuple[str, ...] = (
    "#8B78E6",  # Muted Lavender
    "#5BA88C",  # Soft Sage
    "#D9788D",  # Dusty Blush Rose
    "#6A9EE0",  # Powder Blue
    "#D9A754",  # Warm Butter Amber
    "#D98A63",  # Muted Peach Terracotta
    "#9E8AD9",  # Soft Iris
    "#6EB5A8",  # Seafoam Mint
)


class SheetCellDelegate(QStyledItemDelegate):
    """Item delegate that loads raw cell formulas into inline cell editors."""

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:  # noqa: N802
        raw_val = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(editor, QLineEdit):
            editor.setText(str(raw_val) if raw_val is not None else "")
            editor.selectAll()
            return
        super().setEditorData(editor, index)


class PastelChartCanvas(QWidget):
    """Custom-painted Bar, Line, and Donut chart widget using muted pastel colors."""

    def __init__(
        self,
        labels: list[str],
        values: list[float],
        series_title: str = "Series 1",
        tokens: ThemeTokens = LIGHT_THEME,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._labels = labels
        self._values = values
        self._series_title = series_title
        self._tokens = tokens
        self._chart_type: str = "bar"  # 'bar' | 'line' | 'donut'
        self.setMinimumSize(560, 340)

    def set_chart_type(self, chart_type: str) -> None:
        self._chart_type = chart_type
        self.update()

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(self.rect())
        painter.fillRect(rect, QColor(self._tokens.bg_surface))

        if not self._values:
            painter.setPen(QColor(self._tokens.text_secondary))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                "Select numeric cells in your sheet to visualize them here.",
            )
            painter.end()
            return

        if self._chart_type == "donut":
            self._paint_donut_chart(painter, rect)
        elif self._chart_type == "line":
            self._paint_cartesian_chart(painter, rect, is_line=True)
        else:
            self._paint_cartesian_chart(painter, rect, is_line=False)

        painter.end()

    def _paint_cartesian_chart(self, painter: QPainter, rect: QRectF, is_line: bool) -> None:
        pad_left = 64.0
        pad_right = 28.0
        pad_top = 34.0
        pad_bottom = 52.0

        plot = QRectF(
            rect.left() + pad_left,
            rect.top() + pad_top,
            max(120.0, rect.width() - pad_left - pad_right),
            max(120.0, rect.height() - pad_top - pad_bottom),
        )

        # Series title top-left
        title_font = QFont("Inter", 11, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(self._tokens.text_primary))
        painter.drawText(
            QRectF(plot.left(), 8.0, plot.width(), 22.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._series_title,
        )

        raw_min = min(0.0, min(self._values))
        raw_max = max(0.0, max(self._values))
        if abs(raw_max - raw_min) < 1e-9:
            raw_max = raw_min + 10.0

        span = raw_max - raw_min
        grid_steps = 5
        axis_font = QFont("Segoe UI Variable Text", 8)
        painter.setFont(axis_font)

        # Horizontal hairline gridlines & Y-axis labels
        for step in range(grid_steps + 1):
            frac = step / float(grid_steps)
            val = raw_min + frac * span
            y = plot.bottom() - frac * plot.height()

            painter.setPen(QPen(QColor(self._tokens.border_subtle), 1.0))
            painter.drawLine(int(plot.left()), int(round(y)), int(plot.right()), int(round(y)))

            painter.setPen(QColor(self._tokens.text_muted))
            painter.drawText(
                QRectF(6.0, y - 9.0, pad_left - 12.0, 18.0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                format_cell_display(val),
            )

        zero_frac = (0.0 - raw_min) / span
        zero_y = plot.bottom() - zero_frac * plot.height()
        painter.setPen(QPen(QColor(self._tokens.border_strong), 1.2))
        painter.drawLine(int(plot.left()), int(round(zero_y)), int(plot.right()), int(round(zero_y)))

        count = len(self._values)
        slot_w = plot.width() / float(max(1, count))
        points: list[tuple[float, float]] = []

        for idx, val in enumerate(self._values):
            cx = plot.left() + (idx + 0.5) * slot_w
            val_frac = (val - raw_min) / span
            cy = plot.bottom() - val_frac * plot.height()
            points.append((cx, cy))

            color_hex = _CHART_PASTEL_PALETTE[idx % len(_CHART_PASTEL_PALETTE)]

            if not is_line:
                bar_w = min(44.0, slot_w * 0.58)
                top_y = min(cy, zero_y)
                bar_h = max(2.0, abs(zero_y - cy))
                bar_rect = QRectF(cx - bar_w / 2.0, top_y, bar_w, bar_h)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color_hex))
                painter.drawRoundedRect(bar_rect, 6.0, 6.0)

            # X-axis category label
            lbl_text = self._labels[idx] if idx < len(self._labels) else f"#{idx + 1}"
            painter.setPen(QColor(self._tokens.text_secondary))
            painter.drawText(
                QRectF(cx - slot_w / 2.0, plot.bottom() + 6.0, slot_w, 22.0),
                Qt.AlignmentFlag.AlignCenter,
                lbl_text[:14],
            )

        if is_line:
            line_color = QColor(_CHART_PASTEL_PALETTE[0])
            if len(points) >= 2:
                painter.setPen(QPen(line_color, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                for idx in range(len(points) - 1):
                    x1, y1 = points[idx]
                    x2, y2 = points[idx + 1]
                    painter.drawLine(int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))

            for idx, (px, py) in enumerate(points):
                dot_hex = _CHART_PASTEL_PALETTE[idx % len(_CHART_PASTEL_PALETTE)]
                painter.setPen(QPen(QColor(self._tokens.bg_surface), 2.0))
                painter.setBrush(QColor(dot_hex))
                painter.drawEllipse(QRectF(px - 5.0, py - 5.0, 10.0, 10.0))

    def _paint_donut_chart(self, painter: QPainter, rect: QRectF) -> None:
        positive_pairs = [
            (
                self._labels[i] if i < len(self._labels) else f"Item {i + 1}",
                max(0.0, float(v)),
            )
            for i, v in enumerate(self._values)
            if float(v) > 0.0
        ]
        total = sum(v for _, v in positive_pairs)
        if total <= 0.0:
            painter.setPen(QColor(self._tokens.text_secondary))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                "Donut chart requires positive numeric values.",
            )
            return

        title_font = QFont("Inter", 11, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(self._tokens.text_primary))
        painter.drawText(
            QRectF(20.0, 8.0, rect.width() - 40.0, 22.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._series_title,
        )

        diameter = min(rect.width() * 0.48, rect.height() - 68.0)
        cx = rect.left() + diameter * 0.62 + 20.0
        cy = rect.center().y() + 10.0
        pie_rect = QRectF(cx - diameter / 2.0, cy - diameter / 2.0, diameter, diameter)

        start_angle_16 = 90 * 16
        for idx, (_lbl, val) in enumerate(positive_pairs):
            span_angle_16 = -round((val / total) * 360.0 * 16.0)
            color_hex = _CHART_PASTEL_PALETTE[idx % len(_CHART_PASTEL_PALETTE)]
            painter.setPen(QPen(QColor(self._tokens.bg_surface), 2.0))
            painter.setBrush(QColor(color_hex))
            painter.drawPie(pie_rect, start_angle_16, span_angle_16)
            start_angle_16 += span_angle_16

        # Inner cutout for modern Donut look
        hole_d = diameter * 0.54
        hole_rect = QRectF(cx - hole_d / 2.0, cy - hole_d / 2.0, hole_d, hole_d)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._tokens.bg_surface))
        painter.drawEllipse(hole_rect)

        # Total readout centered inside donut ring
        painter.setFont(QFont("Inter", 10, QFont.Weight.DemiBold))
        painter.setPen(QColor(self._tokens.text_primary))
        painter.drawText(
            hole_rect,
            Qt.AlignmentFlag.AlignCenter,
            f"Total\n{format_cell_display(total)}",
        )

        # Right-side pastel legend
        legend_x = pie_rect.right() + 28.0
        legend_y = pie_rect.top() + 8.0
        painter.setFont(QFont("Segoe UI Variable Text", 9))
        for idx, (lbl, val) in enumerate(positive_pairs[:9]):
            row_y = legend_y + idx * 24.0
            color_hex = _CHART_PASTEL_PALETTE[idx % len(_CHART_PASTEL_PALETTE)]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color_hex))
            painter.drawRoundedRect(QRectF(legend_x, row_y + 3.0, 12.0, 12.0), 4.0, 4.0)

            pct = (val / total) * 100.0
            painter.setPen(QColor(self._tokens.text_primary))
            painter.drawText(
                QRectF(legend_x + 18.0, row_y, max(120.0, rect.right() - legend_x - 24.0), 18.0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{lbl[:16]}  ({pct:.1f}%)",
            )


class PastelChartDialog(QDialog):
    """Floating modal chart viewer with Bar, Line, Donut switchers and PNG export."""

    def __init__(
        self,
        labels: list[str],
        values: list[float],
        series_title: str = "Selected Data",
        tokens: ThemeTokens = LIGHT_THEME,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Pastel Chart — {series_title}")
        self.setModal(True)
        self.resize(680, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        type_group = QButtonGroup(self)
        type_group.setExclusive(True)

        for chart_id, label in (("bar", "Bar Chart"), ("line", "Line Chart"), ("donut", "Donut Chart")):
            btn = QPushButton(label, self)
            btn.setProperty("variant", "segmented")
            btn.setCheckable(True)
            btn.setChecked(chart_id == "bar")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, cid=chart_id: self._canvas.set_chart_type(cid))
            type_group.addButton(btn)
            top_bar.addWidget(btn)

        top_bar.addStretch(1)

        save_png_btn = make_button(
            text="Save PNG",
            variant="pastel",
            tooltip="Save chart image as PNG",
            on_click=self._export_png,
            parent=self,
        )
        top_bar.addWidget(save_png_btn)

        close_btn = make_button(
            text="Close",
            variant="ghost",
            on_click=self.accept,
            parent=self,
        )
        top_bar.addWidget(close_btn)
        layout.addLayout(top_bar)

        chart_card = CardFrame(padding=12, spacing=0, parent=self)
        self._canvas = PastelChartCanvas(
            labels=labels,
            values=values,
            series_title=series_title,
            tokens=tokens,
            parent=chart_card,
        )
        chart_card.body_layout.addWidget(self._canvas, 1)
        layout.addWidget(chart_card, 1)

    def _export_png(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Save Chart as PNG",
            str(Path.home() / "Pictures" / "simple_sheets_chart.png"),
            "PNG Image (*.png)",
        )
        if not selected:
            return
        target = Path(selected)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        pixmap = QPixmap(self._canvas.size())
        self._canvas.render(pixmap)
        pixmap.save(str(target), "PNG")


class SimpleSheetsWindow(BaseWindow):
    """Main window for SimpleSheets with Soft Modern Pastel grid, formula bar, and charts."""

    def __init__(self, workbook: WorkbookModel | None = None) -> None:
        super().__init__(app_id=APP_ID, min_width=940, min_height=640)
        if self.settings.get("window_width") is None:
            self.resize(1200, 780)

        self.workbook: WorkbookModel = workbook or WorkbookModel()
        self._current_file_path: Path | None = None
        self._is_dirty: bool = False
        self._is_syncing_grid: bool = False
        self._sheet_tab_buttons: list[QPushButton] = []

        self._build_header_actions()
        self._build_workspace()
        self._bind_shortcuts()

        self._load_active_sheet_into_grid()
        self._rebuild_sheet_tabs()
        self._sync_dirty_badge()

    def _build_header_actions(self) -> None:
        """Mount New, Open, Save, Export, and Chart actions in the integrated pastel header."""
        new_btn = make_button(
            text="New",
            variant="ghost",
            tooltip="Create a blank workbook (Ctrl+N)",
            on_click=self.new_workbook,
            icon_name="plus",
            icon_size=14,
            parent=self,
        )
        new_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(new_btn)

        open_btn = make_button(
            text="Open",
            variant="ghost",
            tooltip="Open Excel (.xlsx) or CSV (.csv) spreadsheet (Ctrl+O)",
            on_click=self.open_workbook_prompt,
            icon_name="folder",
            icon_size=14,
            parent=self,
        )
        open_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(open_btn)

        save_btn = make_button(
            text="Save",
            variant="pastel",
            tooltip="Save workbook as .xlsx (Ctrl+S)",
            on_click=self.save_workbook_action,
            icon_name="check",
            icon_size=14,
            parent=self,
        )
        save_btn.setStyleSheet("padding: 4px 11px; font-size: 12px;")
        self.header_actions_layout.addWidget(save_btn)

        export_btn = make_button(
            text="Export",
            variant="ghost",
            tooltip="Save As / Export (.xlsx, .csv, .tsv)",
            on_click=self.export_workbook_as,
            parent=self,
        )
        export_btn.setStyleSheet("padding: 4px 9px; font-size: 12px;")
        self.header_actions_layout.addWidget(export_btn)

        chart_btn = make_button(
            text="Chart",
            variant="segmented",
            tooltip="Create a pastel Bar, Line, or Donut chart from selected cells",
            on_click=self.open_chart_for_selection,
            icon_name="sparkles",
            icon_size=13,
            parent=self,
        )
        chart_btn.setStyleSheet("padding: 4px 10px; font-size: 12px;")
        self.header_actions_layout.addWidget(chart_btn)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ==============================================================
        # 1. Pastel Formatting & Functions Toolbar Strip
        # ==============================================================
        toolbar_bar = QFrame(central)
        toolbar_bar.setProperty("surface", "app-header")
        toolbar_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        tb_row = QHBoxLayout(toolbar_bar)
        tb_row.setContentsMargins(14, 6, 14, 6)
        tb_row.setSpacing(5)

        self._bold_btn = QPushButton("B", toolbar_bar)
        self._bold_btn.setProperty("variant", "segmented")
        self._bold_btn.setCheckable(True)
        self._bold_btn.setToolTip("Bold selected cells (Ctrl+B)")
        self._bold_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bold_btn.setStyleSheet("padding: 4px 9px; font-size: 12px; font-weight: 700;")
        self._bold_btn.clicked.connect(self._toggle_selection_bold)
        tb_row.addWidget(self._bold_btn)

        self._italic_btn = QPushButton("I", toolbar_bar)
        self._italic_btn.setProperty("variant", "segmented")
        self._italic_btn.setCheckable(True)
        self._italic_btn.setToolTip("Italic selected cells (Ctrl+I)")
        self._italic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._italic_btn.setStyleSheet("padding: 4px 10px; font-size: 12px; font-style: italic;")
        self._italic_btn.clicked.connect(self._toggle_selection_italic)
        tb_row.addWidget(self._italic_btn)

        tb_row.addWidget(VerticalDivider(toolbar_bar))

        # Horizontal Alignment
        for label, align_code, tip in (
            ("Left", "left", "Align left"),
            ("Center", "center", "Align center"),
            ("Right", "right", "Align right"),
        ):
            abtn = make_button(
                text=label,
                variant="ghost",
                tooltip=tip,
                on_click=lambda _=False, ac=align_code: self._set_selection_alignment(ac),
                parent=toolbar_bar,
            )
            abtn.setStyleSheet("padding: 4px 7px; font-size: 12px;")
            tb_row.addWidget(abtn)

        tb_row.addWidget(VerticalDivider(toolbar_bar))

        # Number Format Selector
        self._num_fmt_combo = QComboBox(toolbar_bar)
        for label, code in _NUMBER_FORMATS:
            self._num_fmt_combo.addItem(label, code)
        self._num_fmt_combo.setToolTip("Number display format")
        self._num_fmt_combo.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        self._num_fmt_combo.activated.connect(self._on_number_format_selected)
        tb_row.addWidget(self._num_fmt_combo)

        # Pastel Cell Fill Swatch Picker
        fill_btn = QPushButton("Cell Tint", toolbar_bar)
        fill_btn.setProperty("variant", "ghost")
        fill_btn.setProperty("has_menu", True)
        fill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fill_btn.setToolTip("Apply soft pastel background tint to selected cells")
        fill_btn.setStyleSheet("padding: 4px 22px 4px 8px; font-size: 12px;")
        fill_menu = QMenu(fill_btn)
        for swatch_label, hex_code in _CELL_TINT_SWATCHES:
            act = fill_menu.addAction(swatch_label)
            act.triggered.connect(lambda _=False, c=hex_code: self._apply_selection_tint(c))
        fill_btn.setMenu(fill_menu)
        tb_row.addWidget(fill_btn)

        tb_row.addWidget(VerticalDivider(toolbar_bar))

        # Quick Formula Insertion (AutoSum / Functions)
        fn_btn = QPushButton("Σ Functions", toolbar_bar)
        fn_btn.setProperty("variant", "pastel")
        fn_btn.setProperty("has_menu", True)
        fn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fn_btn.setToolTip("Insert spreadsheet formula")
        fn_btn.setStyleSheet("padding: 4px 24px 4px 10px; font-size: 12px;")
        fn_menu = QMenu(fn_btn)
        for fn_name in ("SUM", "AVERAGE", "COUNT", "MIN", "MAX", "IF", "ROUND", "MEDIAN"):
            fact = fn_menu.addAction(f"={fn_name}(...)")
            fact.triggered.connect(lambda _=False, fn=fn_name: self._insert_formula_template(fn))
        fn_btn.setMenu(fn_menu)
        tb_row.addWidget(fn_btn)

        clear_btn = make_button(
            text="Clear",
            variant="ghost",
            tooltip="Clear selected cells (Delete)",
            on_click=self.clear_selected_cells,
            parent=toolbar_bar,
        )
        clear_btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
        tb_row.addWidget(clear_btn)

        tb_row.addStretch(1)
        outer.addWidget(toolbar_bar)

        # ==============================================================
        # 2. Refined Name Box & Formula Bar (fx)
        # ==============================================================
        formula_strip = QFrame(central)
        formula_strip.setProperty("surface", "app-header")
        formula_strip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        f_row = QHBoxLayout(formula_strip)
        f_row.setContentsMargins(14, 5, 14, 5)
        f_row.setSpacing(8)

        self._name_box = QLineEdit("A1", formula_strip)
        self._name_box.setFixedWidth(74)
        self._name_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_box.setToolTip("Active cell reference (e.g. A1, C12) — press Enter to jump")
        self._name_box.setStyleSheet(
            "QLineEdit { font-family: 'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace; "
            "font-size: 12px; font-weight: 600; padding: 4px 6px; }"
        )
        self._name_box.returnPressed.connect(self._jump_to_name_box_ref)
        f_row.addWidget(self._name_box)

        fx_lbl = QLabel("fx", formula_strip)
        fx_lbl.setProperty("role", "muted")
        fx_lbl.setStyleSheet("font-style: italic; font-weight: 700; font-size: 13px;")
        f_row.addWidget(fx_lbl)

        self._formula_input = QLineEdit(formula_strip)
        self._formula_input.setPlaceholderText("Enter a number, text, or formula (e.g. =SUM(A1:A5))...")
        self._formula_input.setStyleSheet(
            "QLineEdit { font-family: 'JetBrains Mono', 'Cascadia Code', 'Consolas', monospace; "
            "font-size: 12px; padding: 4px 10px; }"
        )
        self._formula_input.returnPressed.connect(self._commit_formula_bar)
        f_row.addWidget(self._formula_input, 1)

        outer.addWidget(formula_strip)

        # ==============================================================
        # 3. Clean Spreadsheet Grid
        # ==============================================================
        self._table = QTableWidget(central)
        self._table.setItemDelegate(SheetCellDelegate(self._table))
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_grid_context_menu)
        self._table.currentCellChanged.connect(self._on_current_cell_changed)
        self._table.itemSelectionChanged.connect(self._refresh_selection_telemetry)
        self._table.itemChanged.connect(self._on_table_item_changed)

        self._table.horizontalHeader().setDefaultSectionSize(108)
        self._table.horizontalHeader().setMinimumSectionSize(44)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setMinimumWidth(46)
        self._table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        self._apply_grid_stylesheet()
        outer.addWidget(self._table, 1)

        # ==============================================================
        # 4. Bottom Multi-Sheet Tab Bar & Live Selection Summary
        # ==============================================================
        bottom_bar = QFrame(central)
        bottom_bar.setProperty("surface", "footer-bar")
        bottom_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bottom_bar.setFixedHeight(38)

        b_row = QHBoxLayout(bottom_bar)
        b_row.setContentsMargins(12, 4, 16, 4)
        b_row.setSpacing(6)

        add_sheet_btn = make_button(
            text="+",
            variant="ghost",
            tooltip="Add new worksheet",
            on_click=self.add_new_sheet,
            parent=bottom_bar,
        )
        add_sheet_btn.setStyleSheet("padding: 2px 8px; font-size: 14px; font-weight: 700;")
        b_row.addWidget(add_sheet_btn)

        self._tabs_container = QWidget(bottom_bar)
        self._tabs_layout = QHBoxLayout(self._tabs_container)
        self._tabs_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_layout.setSpacing(4)
        b_row.addWidget(self._tabs_container)

        b_row.addStretch(1)

        self._selection_stats_lbl = QLabel("Ready", bottom_bar)
        self._selection_stats_lbl.setProperty("role", "muted")
        b_row.addWidget(self._selection_stats_lbl)

        b_row.addWidget(VerticalDivider(bottom_bar))

        self._status_badge = StatusBadge("New (.xlsx)", variant="lavender", parent=bottom_bar)
        b_row.addWidget(self._status_badge)

        outer.addWidget(bottom_bar)

    def _apply_grid_stylesheet(self) -> None:
        """Apply soft pastel cell selection and hairline borders to QTableWidget."""
        t = self.theme_manager.tokens
        self._table.setStyleSheet(
            f"""
            QTableWidget {{
                background-color: {t.bg_surface};
                alternate-background-color: {t.bg_surface};
                border: none;
                border-radius: 0px;
                gridline-color: {t.border_subtle};
                selection-background-color: {t.bg_selected};
                selection-color: {t.text_primary};
                padding: 0px;
            }}
            QTableWidget::item:selected {{
                background-color: {t.bg_selected};
                color: {t.text_primary};
            }}
            QHeaderView::section {{
                background-color: {t.bg_elevated};
                color: {t.text_secondary};
                font-weight: 600;
                font-size: 11px;
                border: none;
                border-right: 1px solid {t.border_subtle};
                border-bottom: 1px solid {t.border_subtle};
                padding: 4px 6px;
            }}
            QHeaderView::section:checked {{
                background-color: {t.accent_subtle};
                color: {t.accent_primary};
            }}
            """
        )

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.New, self).activated.connect(self.new_workbook)
        QShortcut(QKeySequence.StandardKey.Open, self).activated.connect(self.open_workbook_prompt)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(self.save_workbook_action)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self).activated.connect(self.export_workbook_as)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self._toggle_selection_bold)
        QShortcut(QKeySequence("Ctrl+I"), self).activated.connect(self._toggle_selection_italic)
        QShortcut(QKeySequence.StandardKey.Copy, self).activated.connect(self._copy_if_grid_focused)
        QShortcut(QKeySequence.StandardKey.Paste, self).activated.connect(self._paste_if_grid_focused)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(self._delete_if_grid_focused)

    # ------------------------------------------------------------------
    # Grid Rendering & Bidirectional Sync with SheetModel
    # ------------------------------------------------------------------

    @property
    def active_sheet(self) -> SheetModel:
        return self.workbook.active_sheet

    def _load_active_sheet_into_grid(self) -> None:
        sheet = self.active_sheet
        self._is_syncing_grid = True
        try:
            self._table.clear()
            self._table.setRowCount(sheet.rows)
            self._table.setColumnCount(sheet.cols)

            col_labels = [col_index_to_label(c) for c in range(sheet.cols)]
            row_labels = [str(r + 1) for r in range(sheet.rows)]
            self._table.setHorizontalHeaderLabels(col_labels)
            self._table.setVerticalHeaderLabels(row_labels)

            for r in range(sheet.rows):
                for c in range(sheet.cols):
                    item = QTableWidgetItem("")
                    item.setData(Qt.ItemDataRole.UserRole, "")
                    self._table.setItem(r, c, item)
                    cell = sheet.get_cell(r, c)
                    if cell is not None:
                        self._apply_cell_to_item(item, cell)
        finally:
            self._is_syncing_grid = False

        self._table.setCurrentCell(0, 0)
        self._on_current_cell_changed(0, 0, -1, -1)

    def _apply_cell_to_item(self, item: QTableWidgetItem, cell: CellData | None) -> None:
        t = self.theme_manager.tokens
        if cell is None or cell.is_empty:
            item.setText("")
            item.setData(Qt.ItemDataRole.UserRole, "")
            item.setFont(QFont("Inter", 10))
            item.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            item.setBackground(QBrush(Qt.BrushStyle.NoBrush))
            item.setForeground(QBrush(QColor(t.text_primary)))
            return

        display_str = cell.display_text()
        item.setText(display_str)
        item.setData(Qt.ItemDataRole.UserRole, cell.raw_input)

        font = QFont("Inter", 10)
        font.setBold(cell.bold)
        font.setItalic(cell.italic)
        item.setFont(font)

        # Alignment: numbers & booleans auto-align right, text aligns left
        if cell.align == "center":
            align_flag = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        elif cell.align == "right":
            align_flag = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        elif cell.align == "left":
            align_flag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        else:
            is_num_or_bool = isinstance(cell.evaluated_value, (int, float, bool))
            align_flag = (
                (Qt.AlignmentFlag.AlignRight if is_num_or_bool else Qt.AlignmentFlag.AlignLeft)
                | Qt.AlignmentFlag.AlignVCenter
            )
        item.setTextAlignment(int(align_flag))

        # Background pastel tint & foreground contrast
        if cell.bg_tint:
            item.setBackground(QBrush(QColor(cell.bg_tint)))
            item.setForeground(QBrush(QColor("#35343B")))
        else:
            item.setBackground(QBrush(Qt.BrushStyle.NoBrush))
            if display_str.startswith("#"):
                item.setForeground(QBrush(QColor(t.danger)))
            else:
                item.setForeground(QBrush(QColor(t.text_primary)))

    def _refresh_specific_coords(self, coords: set[CellCoord]) -> None:
        if not coords:
            return
        self._is_syncing_grid = True
        try:
            for r, c in coords:
                if 0 <= r < self._table.rowCount() and 0 <= c < self._table.columnCount():
                    item = self._table.item(r, c)
                    if item is None:
                        item = QTableWidgetItem("")
                        self._table.setItem(r, c, item)
                    self._apply_cell_to_item(item, self.active_sheet.get_cell(r, c))
        finally:
            self._is_syncing_grid = False

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._is_syncing_grid:
            return
        r = item.row()
        c = item.column()
        raw_entered = item.text()
        changed_coords = self.active_sheet.set_cell_input(r, c, raw_entered)
        self._refresh_specific_coords(changed_coords)

        if r == self._table.currentRow() and c == self._table.currentColumn():
            self._formula_input.setText(self.active_sheet.get_raw_input(r, c))

        self._refresh_selection_telemetry()
        self._mark_dirty()

    def _on_current_cell_changed(
        self,
        curr_row: int,
        curr_col: int,
        _prev_row: int,
        _prev_col: int,
    ) -> None:
        if curr_row < 0 or curr_col < 0:
            return
        self._name_box.setText(format_cell_ref(curr_row, curr_col))
        cell = self.active_sheet.get_cell(curr_row, curr_col)
        self._formula_input.setText(cell.raw_input if cell is not None else "")

        self._bold_btn.setChecked(bool(cell and cell.bold))
        self._italic_btn.setChecked(bool(cell and cell.italic))

        fmt_code = cell.number_format if cell is not None else "general"
        idx = self._num_fmt_combo.findData(fmt_code)
        self._num_fmt_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _commit_formula_bar(self) -> None:
        r = max(0, self._table.currentRow())
        c = max(0, self._table.currentColumn())
        raw_text = self._formula_input.text()
        changed_coords = self.active_sheet.set_cell_input(r, c, raw_text)
        self._refresh_specific_coords(changed_coords)
        self._refresh_selection_telemetry()
        self._mark_dirty()

        # Move down one row on Enter just like Excel / Google Sheets
        next_r = min(self._table.rowCount() - 1, r + 1)
        self._table.setCurrentCell(next_r, c)
        self._table.setFocus()

    def _jump_to_name_box_ref(self) -> None:
        ref_str = self._name_box.text().strip()
        try:
            r, c = parse_cell_ref(ref_str)
        except Exception:
            self.set_status_message(f"Invalid reference '{ref_str}'", timeout_ms=2000)
            return

        if r >= self._table.rowCount() or c >= self._table.columnCount():
            self.active_sheet.ensure_capacity(r + 1, c + 1)
            self._load_active_sheet_into_grid()

        self._table.setCurrentCell(r, c)
        self._table.setFocus()

    # ------------------------------------------------------------------
    # Selection Formatting, Formulas & Charts
    # ------------------------------------------------------------------

    def _selected_coords(self) -> list[CellCoord]:
        selected_items = self._table.selectedItems()
        if selected_items:
            return [(it.row(), it.column()) for it in selected_items]
        r = self._table.currentRow()
        c = self._table.currentColumn()
        return [(r, c)] if (r >= 0 and c >= 0) else []

    def _toggle_selection_bold(self) -> None:
        coords = self._selected_coords()
        if not coords:
            return
        target_bold = self._bold_btn.isChecked()
        if self.sender() != self._bold_btn:
            first_cell = self.active_sheet.get_cell(*coords[0])
            target_bold = not bool(first_cell and first_cell.bold)
            self._bold_btn.setChecked(target_bold)

        for r, c in coords:
            self.active_sheet.update_cell_style(r, c, bold=target_bold)
        self._refresh_specific_coords(set(coords))
        self._mark_dirty()

    def _toggle_selection_italic(self) -> None:
        coords = self._selected_coords()
        if not coords:
            return
        target_italic = self._italic_btn.isChecked()
        if self.sender() != self._italic_btn:
            first_cell = self.active_sheet.get_cell(*coords[0])
            target_italic = not bool(first_cell and first_cell.italic)
            self._italic_btn.setChecked(target_italic)

        for r, c in coords:
            self.active_sheet.update_cell_style(r, c, italic=target_italic)
        self._refresh_specific_coords(set(coords))
        self._mark_dirty()

    def _set_selection_alignment(self, align_code: str) -> None:
        coords = self._selected_coords()
        if not coords:
            return
        for r, c in coords:
            self.active_sheet.update_cell_style(r, c, align=align_code)
        self._refresh_specific_coords(set(coords))
        self._mark_dirty()

    def _on_number_format_selected(self, idx: int) -> None:
        fmt_code = str(self._num_fmt_combo.itemData(idx) or "general")
        coords = self._selected_coords()
        if not coords:
            return
        for r, c in coords:
            self.active_sheet.update_cell_style(r, c, number_format=fmt_code)
        self._refresh_specific_coords(set(coords))
        self._mark_dirty()

    def _apply_selection_tint(self, hex_code: str) -> None:
        coords = self._selected_coords()
        if not coords:
            return
        for r, c in coords:
            self.active_sheet.update_cell_style(r, c, bg_tint=hex_code)
        self._refresh_specific_coords(set(coords))
        self._mark_dirty()

    def _insert_formula_template(self, fn_name: str) -> None:
        coords = self._selected_coords()
        if len(coords) > 1:
            rows = [r for r, _ in coords]
            cols = [c for _, c in coords]
            start_ref = format_cell_ref(min(rows), min(cols))
            end_ref = format_cell_ref(max(rows), max(cols))
            target_r = max(rows) + 1
            target_c = min(cols)
            self.active_sheet.ensure_capacity(target_r + 1, target_c + 1)
            if target_r >= self._table.rowCount():
                self._load_active_sheet_into_grid()
            self._table.setCurrentCell(target_r, target_c)
            formula_str = f"={fn_name}({start_ref}:{end_ref})"
            changed = self.active_sheet.set_cell_input(target_r, target_c, formula_str)
            self._refresh_specific_coords(changed)
            self._formula_input.setText(formula_str)
            self._refresh_selection_telemetry()
            self._mark_dirty()
            return

        self._formula_input.setText(f"={fn_name}()")
        self._formula_input.setFocus()
        self._formula_input.setCursorPosition(len(fn_name) + 2)

    def clear_selected_cells(self) -> None:
        coords = self._selected_coords()
        if not coords:
            return
        all_changed: set[CellCoord] = set()
        for r, c in coords:
            all_changed.update(self.active_sheet.clear_cell(r, c))
        self._refresh_specific_coords(all_changed)
        self._formula_input.clear()
        self._refresh_selection_telemetry()
        self._mark_dirty()

    def extract_chart_series_from_selection(self) -> tuple[list[str], list[float], str]:
        """Extract (labels, values, series_title) from selected cells or used grid data."""
        coords = self._selected_coords()
        sheet = self.active_sheet

        # If user only highlighted a single cell, inspect the populated table block instead
        if len(coords) <= 1 and sheet.cells:
            max_r, max_c = sheet.used_bounds()
            coords = [(r, c) for r in range(max_r + 1) for c in range(max_c + 1)]

        if not coords:
            return ([], [], "Selected Data")

        min_r = min(r for r, _ in coords)
        max_r = max(r for r, _ in coords)
        min_c = min(c for _, c in coords)
        max_c = max(c for _, c in coords)

        # Case A: Multi-column selection (first column = category labels, second+ = numeric values)
        if max_c > min_c:
            val_col = min_c + 1
            header_cell = sheet.get_cell(min_r, val_col)
            has_header = (
                header_cell is not None
                and isinstance(header_cell.evaluated_value, str)
                and header_cell.evaluated_value.strip() != ""
            )
            series_title = (
                str(header_cell.evaluated_value).strip()
                if has_header and header_cell is not None
                else f"Column {col_index_to_label(val_col)}"
            )
            start_row = min_r + 1 if has_header else min_r

            labels: list[str] = []
            values: list[float] = []
            for r in range(start_row, max_r + 1):
                v_cell = sheet.get_cell(r, val_col)
                if v_cell is None or not isinstance(v_cell.evaluated_value, (int, float)) or isinstance(v_cell.evaluated_value, bool):
                    continue
                l_cell = sheet.get_cell(r, min_c)
                lbl = l_cell.display_text().strip() if l_cell is not None else ""
                labels.append(lbl or f"Row {r + 1}")
                values.append(float(v_cell.evaluated_value))
            return (labels, values, series_title)

        # Case B: Single column or single row of numbers
        header_cell = sheet.get_cell(min_r, min_c)
        has_header = (
            header_cell is not None
            and isinstance(header_cell.evaluated_value, str)
            and header_cell.evaluated_value.strip() != ""
        )
        series_title = (
            str(header_cell.evaluated_value).strip()
            if has_header and header_cell is not None
            else f"Column {col_index_to_label(min_c)}"
        )
        start_row = min_r + 1 if has_header else min_r
        labels = []
        values = []
        for r in range(start_row, max_r + 1):
            cell = sheet.get_cell(r, min_c)
            if cell is not None and isinstance(cell.evaluated_value, (int, float)) and not isinstance(cell.evaluated_value, bool):
                labels.append(f"Row {r + 1}")
                values.append(float(cell.evaluated_value))
        return (labels, values, series_title)

    def open_chart_for_selection(self) -> None:
        """Open the PastelChartDialog populated from the selected cell range."""
        labels, values, title = self.extract_chart_series_from_selection()
        if not values:
            show_error_dialog(
                self,
                "No Numeric Data Selected",
                "Select a range containing numeric cells (or a label column next to numbers) to create a chart.",
            )
            return

        dialog = PastelChartDialog(
            labels=labels,
            values=values,
            series_title=title,
            tokens=self.theme_manager.tokens,
            parent=self,
        )
        dialog.exec()

    # ------------------------------------------------------------------
    # Multi-Sheet Tabs & Selection Telemetry
    # ------------------------------------------------------------------

    def _rebuild_sheet_tabs(self) -> None:
        while self._tabs_layout.count() > 0:
            item = self._tabs_layout.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        self._sheet_tab_buttons.clear()

        for idx, sheet in enumerate(self.workbook.sheets):
            btn = QPushButton(sheet.name, self._tabs_container)
            btn.setProperty("variant", "segmented")
            btn.setCheckable(True)
            btn.setChecked(idx == self.workbook.active_index)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("padding: 3px 12px; font-size: 12px;")
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.clicked.connect(lambda _=False, i=idx: self.switch_to_sheet(i))
            btn.customContextMenuRequested.connect(
                lambda pos, i=idx, b=btn: self._show_sheet_tab_menu(b.mapToGlobal(pos), i)
            )
            self._tabs_layout.addWidget(btn)
            self._sheet_tab_buttons.append(btn)

    def switch_to_sheet(self, index: int) -> None:
        self.workbook.set_active_sheet(index)
        self._load_active_sheet_into_grid()
        self._rebuild_sheet_tabs()

    def add_new_sheet(self) -> None:
        sheet = self.workbook.add_sheet()
        self._load_active_sheet_into_grid()
        self._rebuild_sheet_tabs()
        self._mark_dirty()
        self.set_status_message(f"Added {sheet.name}", timeout_ms=1800)

    def _show_sheet_tab_menu(self, global_pos: object, index: int) -> None:
        menu = QMenu(self)
        rename_act = menu.addAction("Rename Sheet...")
        delete_act = menu.addAction("Delete Sheet")
        delete_act.setEnabled(len(self.workbook.sheets) > 1)

        chosen = menu.exec(global_pos)  # type: ignore[arg-type]
        if chosen == rename_act:
            current_name = self.workbook.sheets[index].name
            new_name, ok = QInputDialog.getText(self, "Rename Sheet", "Sheet name:", text=current_name)
            if ok and new_name.strip():
                self.workbook.rename_sheet(index, new_name.strip())
                self._rebuild_sheet_tabs()
                self._mark_dirty()
        elif chosen == delete_act and self.workbook.remove_sheet(index):
                self._load_active_sheet_into_grid()
                self._rebuild_sheet_tabs()
                self._mark_dirty()

    def _refresh_selection_telemetry(self) -> None:
        coords = self._selected_coords()
        summary = self.active_sheet.compute_selection_summary(coords)
        if summary.numeric_count >= 1 and summary.total is not None and summary.average is not None:
            self._selection_stats_lbl.setText(
                f"Count: {summary.non_empty_count}  •  "
                f"Sum: {format_cell_display(summary.total)}  •  "
                f"Avg: {format_cell_display(summary.average)}  •  "
                f"Min: {format_cell_display(summary.minimum)}  •  "
                f"Max: {format_cell_display(summary.maximum)}"
            )
        elif summary.non_empty_count > 0:
            self._selection_stats_lbl.setText(f"Count: {summary.non_empty_count}")
        else:
            self._selection_stats_lbl.setText("Ready")

    # ------------------------------------------------------------------
    # Clipboard Copy / Paste & Context Menu
    # ------------------------------------------------------------------

    def _copy_if_grid_focused(self) -> None:
        if self._table.hasFocus():
            self.copy_selection_to_clipboard()

    def _paste_if_grid_focused(self) -> None:
        if self._table.hasFocus():
            self.paste_from_clipboard()

    def _delete_if_grid_focused(self) -> None:
        if self._table.hasFocus():
            self.clear_selected_cells()

    def copy_selection_to_clipboard(self) -> None:
        coords = self._selected_coords()
        if not coords:
            return
        min_r = min(r for r, _ in coords)
        max_r = max(r for r, _ in coords)
        min_c = min(c for _, c in coords)
        max_c = max(c for _, c in coords)

        lines: list[str] = []
        for r in range(min_r, max_r + 1):
            row_vals = [self.active_sheet.get_display_text(r, c) for c in range(min_c, max_c + 1)]
            lines.append("\t".join(row_vals))

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(lines))
            self.set_status_message("Copied cells to clipboard", timeout_ms=1600)

    def paste_from_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        raw_clip = clipboard.text()
        if not raw_clip:
            return

        start_r = max(0, self._table.currentRow())
        start_c = max(0, self._table.currentColumn())
        rows_data = raw_clip.replace("\r\n", "\n").split("\n")
        if rows_data and rows_data[-1] == "":
            rows_data.pop()

        changed: set[CellCoord] = set()
        for r_off, line in enumerate(rows_data):
            for c_off, val in enumerate(line.split("\t")):
                changed.update(self.active_sheet.set_cell_input(start_r + r_off, start_c + c_off, val))

        self._refresh_specific_coords(changed)
        self._refresh_selection_telemetry()
        self._mark_dirty()

    def _show_grid_context_menu(self, pos: object) -> None:
        menu = QMenu(self)
        menu.addAction("Copy (Ctrl+C)", self.copy_selection_to_clipboard)
        menu.addAction("Paste (Ctrl+V)", self.paste_from_clipboard)
        menu.addSeparator()
        menu.addAction("Create Pastel Chart from Selection...", self.open_chart_for_selection)
        menu.addSeparator()
        menu.addAction("Clear Cells (Delete)", self.clear_selected_cells)
        menu.exec(self._table.viewport().mapToGlobal(pos))  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Workbook Open, Save & Export (.xlsx default)
    # ------------------------------------------------------------------

    def _mark_dirty(self) -> None:
        if not self._is_dirty:
            self._is_dirty = True
            self._sync_dirty_badge()

    def _sync_dirty_badge(self) -> None:
        name_display = self._current_file_path.name if self._current_file_path else "Untitled Workbook"
        dirty_dot = " •" if self._is_dirty else ""
        self.setWindowTitle(f"{name_display}{dirty_dot} — {self.meta.display_name}")

        if self._is_dirty:
            self._status_badge.set_status("Unsaved changes", variant="butter")
        elif self._current_file_path is not None:
            self._status_badge.set_status(f"Saved {self._current_file_path.name}", variant="sage")
        else:
            self._status_badge.set_status("New (.xlsx)", variant="lavender")

    def _confirm_discard_if_dirty(self) -> bool:
        if not self._is_dirty:
            return True
        return ask_confirmation(
            parent=self,
            title="Discard Unsaved Workbook?",
            message="You have unsaved changes in this spreadsheet. Continue without saving?",
            confirm_text="Discard Changes",
            destructive=True,
        )

    def new_workbook(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self.workbook = WorkbookModel()
        self._current_file_path = None
        self._is_dirty = False
        self._load_active_sheet_into_grid()
        self._rebuild_sheet_tabs()
        self._sync_dirty_badge()
        self.set_status_message("Started new workbook", timeout_ms=1800)

    def open_workbook_prompt(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Open Spreadsheet",
            str(Path.home() / "Documents"),
            "Spreadsheets (*.xlsx *.csv *.tsv);;Excel Workbook (*.xlsx);;CSV File (*.csv);;TSV File (*.tsv)",
        )
        if not selected:
            return
        self.load_file(selected)

    def load_file(self, file_path: str | Path) -> bool:
        try:
            loaded_wb = load_workbook(file_path)
        except FileOperationError as exc:
            show_error_dialog(self, "Could Not Open Spreadsheet", str(exc))
            return False

        self.workbook = loaded_wb
        self._current_file_path = Path(file_path).resolve()
        self._is_dirty = False
        self._load_active_sheet_into_grid()
        self._rebuild_sheet_tabs()
        self._sync_dirty_badge()
        self.set_status_message(f"Opened {self._current_file_path.name}", timeout_ms=2200)
        return True

    def save_workbook_action(self) -> bool:
        if self._current_file_path is None:
            return self.export_workbook_as(set_as_active=True)
        return self._write_workbook_to(self._current_file_path, set_as_active=True)

    def export_workbook_as(self, set_as_active: bool = False) -> bool:
        stem = sanitize_filename(
            self._current_file_path.stem if self._current_file_path else "Untitled_Workbook"
        )
        default_dir = (
            self._current_file_path.parent
            if self._current_file_path is not None
            else (Path.home() / "Documents")
        )
        default_target = str(default_dir / f"{stem}{DEFAULT_WORKBOOK_EXTENSION}")

        selected, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save or Export Spreadsheet",
            default_target,
            "Excel Workbook (*.xlsx);;CSV (Comma-Delimited) (*.csv);;TSV (Tab-Delimited) (*.tsv)",
        )
        if not selected:
            return False

        target = Path(selected)
        if not target.suffix:
            if "CSV" in selected_filter:
                target = target.with_suffix(".csv")
            elif "TSV" in selected_filter:
                target = target.with_suffix(".tsv")
            else:
                target = target.with_suffix(DEFAULT_WORKBOOK_EXTENSION)

        should_bind = set_as_active or target.suffix.lower() in {".xlsx", ".csv", ".tsv"}
        return self._write_workbook_to(target, set_as_active=should_bind)

    def _write_workbook_to(self, target: Path, set_as_active: bool) -> bool:
        try:
            saved_path = save_workbook(target, self.workbook)
        except FileOperationError as exc:
            show_error_dialog(self, "Save Failed", str(exc))
            return False

        if set_as_active:
            self._current_file_path = saved_path
            self._is_dirty = False
            self._sync_dirty_badge()

        self.notify(
            "Workbook Saved",
            f"Saved '{saved_path.name}' cleanly.",
            level=NotificationLevel.SUCCESS,
        )
        return True

    def can_close(self) -> bool:
        return self._confirm_discard_if_dirty()

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        self._apply_grid_stylesheet()
        self._refresh_specific_coords(set(self.active_sheet.cells.keys()))


def main() -> int:
    """Application entry point for SimpleSheets.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleSheetsWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())