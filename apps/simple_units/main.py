"""SimpleUnits — Precision multi-category unit converter for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.simple_units.converter import (
    ConversionOutput,
    UnitCategory,
    UnitConversionError,
    UnitConverter,
    UnitDefinition,
)
from core.notifications.notifier import NotificationLevel
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.widgets import (
    CardFrame,
    HorizontalDivider,
    SearchInput,
    SectionHeader,
    StatusBadge,
    make_button,
)

logger = logging.getLogger(__name__)

APP_ID = "simple_units"
_MAX_FAVORITES = 25


@dataclass(frozen=True, slots=True)
class FavoritePair:
    """Saved favorite conversion pair across any category."""

    category_id: str
    from_unit_id: str
    to_unit_id: str
    label: str


class SimpleUnitsWindow(BaseWindow):
    """Main window for SimpleUnits with instant bidirectional conversion and equivalence table."""

    def __init__(self) -> None:
        super().__init__(app_id=APP_ID, min_width=900, min_height=600)
        self.converter = UnitConverter()

        self._active_category_id: str = str(self.settings.get("category_id", "length"))
        if self._active_category_id not in {c.category_id for c in self.converter.categories}:
            self._active_category_id = "length"

        self._sig_figs: int = int(self.settings.get("sig_figs", 10))
        if self._sig_figs not in {4, 6, 8, 10, 12}:
            self._sig_figs = 10

        self._favorites: list[FavoritePair] = []
        self._last_formatted_output: str = "0"
        self._updating_selectors: bool = False

        self._build_workspace()
        self._bind_shortcuts()
        self._populate_categories()
        self._restore_favorites()

        # Select initial category and restore saved unit pair
        self._select_category(self._active_category_id, restore_saved_units=True)

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        splitter.setChildrenCollapsible(False)

        # ==============================================================
        # Left Rail: Global Search, Categories & Pinned Favorites
        # ==============================================================
        left_panel = QFrame(splitter)
        left_panel.setProperty("surface", "card")
        left_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left_panel.setMinimumWidth(240)
        left_panel.setMaximumWidth(310)

        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        self._search_input = SearchInput(
            placeholder="Find any unit (e.g., psi, GiB, acre)...",
            debounce_ms=140,
            parent=left_panel,
        )
        self._search_input.search_changed.connect(self._on_search_changed)
        left_layout.addWidget(self._search_input)

        # Quick search dropdown results list (shown only while searching)
        self._search_results_list = QListWidget(left_panel)
        self._search_results_list.setMaximumHeight(160)
        self._search_results_list.setVisible(False)
        self._search_results_list.itemClicked.connect(self._on_search_result_clicked)
        left_layout.addWidget(self._search_results_list)

        left_layout.addWidget(SectionHeader("Categories", parent=left_panel))

        self._category_list = QListWidget(left_panel)
        self._category_list.currentItemChanged.connect(self._on_category_item_changed)
        left_layout.addWidget(self._category_list, 3)

        left_layout.addWidget(HorizontalDivider(left_panel))

        clear_fav_btn = make_button(
            text="Clear",
            variant="ghost",
            tooltip="Remove all pinned unit pairs",
            on_click=self._clear_favorites,
            parent=left_panel,
        )
        left_layout.addWidget(
            SectionHeader("Pinned Pairs", accessory=clear_fav_btn, parent=left_panel)
        )

        self._favorites_list = QListWidget(left_panel)
        self._favorites_list.setMaximumHeight(150)
        self._favorites_list.itemClicked.connect(self._on_favorite_clicked)
        left_layout.addWidget(self._favorites_list, 1)

        self._no_fav_lbl = QLabel("Pin frequent conversions using ★ Pin Pair.", left_panel)
        self._no_fav_lbl.setProperty("role", "caption")
        self._no_fav_lbl.setWordWrap(True)
        left_layout.addWidget(self._no_fav_lbl)

        splitter.addWidget(left_panel)

        # ==============================================================
        # Right Workspace: Primary Converter Stage & Equivalence Table
        # ==============================================================
        right_panel = QWidget(splitter)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(12)

        # --- Category Header Strip ---
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._cat_title_lbl = QLabel("Length & Distance", right_panel)
        self._cat_title_lbl.setProperty("role", "title")
        title_col.addWidget(self._cat_title_lbl)

        self._cat_desc_lbl = QLabel("", right_panel)
        self._cat_desc_lbl.setProperty("role", "muted")
        title_col.addWidget(self._cat_desc_lbl)
        header_row.addLayout(title_col, 1)

        precision_lbl = QLabel("Precision:", right_panel)
        precision_lbl.setProperty("role", "muted")
        header_row.addWidget(precision_lbl)

        self._precision_combo = QComboBox(right_panel)
        for figs in (4, 6, 8, 10, 12):
            self._precision_combo.addItem(f"{figs} digits", figs)
        idx = self._precision_combo.findData(self._sig_figs)
        if idx >= 0:
            self._precision_combo.setCurrentIndex(idx)
        self._precision_combo.currentIndexChanged.connect(self._on_precision_changed)
        header_row.addWidget(self._precision_combo)

        self._pin_btn = make_button(
            text="★ Pin Pair",
            tooltip="Save this unit pair to Pinned Pairs",
            on_click=self._pin_current_pair,
            parent=right_panel,
        )
        header_row.addWidget(self._pin_btn)

        right_layout.addLayout(header_row)

        # --- Primary Bidirectional Conversion Card ---
        conv_card = CardFrame(padding=16, spacing=12, parent=right_panel)

        pair_row = QHBoxLayout()
        pair_row.setSpacing(12)

        # FROM Column
        from_col = QVBoxLayout()
        from_col.setSpacing(6)
        from_lbl = QLabel("FROM", conv_card)
        from_lbl.setProperty("role", "caption")
        from_col.addWidget(from_lbl)

        self._from_combo = QComboBox(conv_card)
        self._from_combo.currentIndexChanged.connect(self._recalculate)
        from_col.addWidget(self._from_combo)

        self._value_input = QLineEdit("1", conv_card)
        self._value_input.setPlaceholderText("Enter value...")
        self._value_input.setStyleSheet(
            "QLineEdit { font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 20px; font-weight: 600; padding: 10px 12px; }"
        )
        self._value_input.textChanged.connect(self._recalculate)
        from_col.addWidget(self._value_input)
        pair_row.addLayout(from_col, 1)

        # Center Swap Button
        swap_col = QVBoxLayout()
        swap_col.addStretch(1)
        self._swap_btn = make_button(
            text="⇄",
            tooltip="Swap From and To units (Ctrl+Shift+S)",
            on_click=self._swap_units,
            parent=conv_card,
        )
        self._swap_btn.setFixedSize(40, 40)
        self._swap_btn.setStyleSheet("font-size: 17px; font-weight: 600; padding: 0px;")
        swap_col.addWidget(self._swap_btn)
        swap_col.addStretch(1)
        pair_row.addLayout(swap_col, 0)

        # TO Column
        to_col = QVBoxLayout()
        to_col.setSpacing(6)
        to_lbl = QLabel("TO", conv_card)
        to_lbl.setProperty("role", "caption")
        to_col.addWidget(to_lbl)

        self._to_combo = QComboBox(conv_card)
        self._to_combo.currentIndexChanged.connect(self._recalculate)
        to_col.addWidget(self._to_combo)

        # Recessed result readout well
        result_well = QFrame(conv_card)
        result_well.setProperty("surface", "recessed")
        result_well.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        well_layout = QHBoxLayout(result_well)
        well_layout.setContentsMargins(12, 8, 8, 8)
        well_layout.setSpacing(8)

        self._result_lbl = QLabel("3.280839895", result_well)
        self._result_lbl.setProperty("role", "display-lg")
        self._result_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        well_layout.addWidget(self._result_lbl, 1)

        copy_btn = make_button(
            text="Copy",
            variant="primary",
            tooltip="Copy converted value to clipboard (Ctrl+C)",
            on_click=self._copy_result,
            parent=result_well,
        )
        well_layout.addWidget(copy_btn, 0)

        to_col.addWidget(result_well)
        pair_row.addLayout(to_col, 1)

        conv_card.body_layout.addLayout(pair_row)

        # Formula / Scale Factor Footer Strip
        formula_row = QHBoxLayout()
        self._formula_badge = StatusBadge("1 m = 3.28084 ft", variant="info", parent=conv_card)
        formula_row.addWidget(self._formula_badge)

        self._error_lbl = QLabel("", conv_card)
        self._error_lbl.setVisible(False)
        formula_row.addWidget(self._error_lbl)

        formula_row.addStretch(1)

        quick_vals_lbl = QLabel("Quick inputs:", conv_card)
        quick_vals_lbl.setProperty("role", "caption")
        formula_row.addWidget(quick_vals_lbl)

        for preset in ("0", "1", "10", "100", "1000"):
            p_btn = make_button(
                text=preset,
                variant="ghost",
                on_click=lambda _=False, v=preset: self._set_input_value(v),
                parent=conv_card,
            )
            p_btn.setStyleSheet("padding: 2px 7px; font-size: 11px;")
            formula_row.addWidget(p_btn)

        conv_card.body_layout.addLayout(formula_row)
        right_layout.addWidget(conv_card)

        # --- All Equivalent Units Reference Table ---
        table_card = CardFrame(padding=14, spacing=8, parent=right_panel)
        table_hint = QLabel("Click any row to copy its converted value", table_card)
        table_hint.setProperty("role", "caption")
        table_card.body_layout.addWidget(
            SectionHeader("All Equivalent Units", accessory=table_hint, parent=table_card)
        )

        self._equiv_table = QTableWidget(0, 3, table_card)
        self._equiv_table.setHorizontalHeaderLabels(["Unit", "Symbol", "Converted Value"])
        self._equiv_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._equiv_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._equiv_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._equiv_table.setAlternatingRowColors(True)
        self._equiv_table.verticalHeader().setVisible(False)

        header = self._equiv_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self._equiv_table.cellClicked.connect(self._on_table_row_clicked)
        table_card.body_layout.addWidget(self._equiv_table, 1)

        right_layout.addWidget(table_card, 1)

        splitter.addWidget(right_panel)
        splitter.setSizes([260, 680])
        root.addWidget(splitter, 1)

    def _bind_shortcuts(self) -> None:
        swap_sc = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        swap_sc.activated.connect(self._swap_units)

        find_sc = QShortcut(QKeySequence.StandardKey.Find, self)
        find_sc.activated.connect(self._search_input.setFocus)

    # ------------------------------------------------------------------
    # Category & Unit Selection
    # ------------------------------------------------------------------

    def _populate_categories(self) -> None:
        self._category_list.clear()
        for cat in self.converter.categories:
            item = QListWidgetItem(cat.name)
            item.setData(Qt.ItemDataRole.UserRole, cat.category_id)
            item.setToolTip(cat.description)
            self._category_list.addItem(item)

    def _on_category_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        cat_id = str(current.data(Qt.ItemDataRole.UserRole))
        if cat_id != self._active_category_id:
            self._select_category(cat_id, restore_saved_units=False)

    def _select_category(
        self,
        category_id: str,
        restore_saved_units: bool = False,
        override_from: str | None = None,
        override_to: str | None = None,
    ) -> None:
        cat: UnitCategory = self.converter.get_category(category_id)
        self._active_category_id = cat.category_id
        self.settings.set("category_id", cat.category_id)

        # Highlight category in sidebar
        for row in range(self._category_list.count()):
            item = self._category_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == cat.category_id:
                self._category_list.setCurrentRow(row)
                break

        self._cat_title_lbl.setText(cat.name)
        self._cat_desc_lbl.setText(cat.description)

        self._updating_selectors = True
        try:
            self._from_combo.clear()
            self._to_combo.clear()
            for unit in cat.units:
                display = f"{unit.name} ({unit.symbol})"
                self._from_combo.addItem(display, unit.unit_id)
                self._to_combo.addItem(display, unit.unit_id)

            target_from = override_from or (
                str(self.settings.get("from_unit_id", cat.default_from))
                if restore_saved_units
                else cat.default_from
            )
            target_to = override_to or (
                str(self.settings.get("to_unit_id", cat.default_to))
                if restore_saved_units
                else cat.default_to
            )

            from_idx = self._from_combo.findData(target_from)
            to_idx = self._to_combo.findData(target_to)
            self._from_combo.setCurrentIndex(from_idx if from_idx >= 0 else 0)
            self._to_combo.setCurrentIndex(
                to_idx if to_idx >= 0 else min(1, self._to_combo.count() - 1)
            )
        finally:
            self._updating_selectors = False

        self._recalculate()

    # ------------------------------------------------------------------
    # Conversion Execution & Equivalence Table
    # ------------------------------------------------------------------

    def _set_input_value(self, text: str) -> None:
        self._value_input.setText(text)
        self._value_input.setFocus()

    def _swap_units(self) -> None:
        from_idx = self._from_combo.currentIndex()
        to_idx = self._to_combo.currentIndex()
        if from_idx < 0 or to_idx < 0:
            return

        self._updating_selectors = True
        try:
            self._from_combo.setCurrentIndex(to_idx)
            self._to_combo.setCurrentIndex(from_idx)
        finally:
            self._updating_selectors = False

        self._recalculate()
        self.set_status_message("Swapped units", timeout_ms=1800)

    def _on_precision_changed(self) -> None:
        val = self._precision_combo.currentData()
        if isinstance(val, int):
            self._sig_figs = val
            self.settings.set("sig_figs", val)
            self._recalculate()

    def _recalculate(self) -> None:
        if self._updating_selectors:
            return

        from_id = str(self._from_combo.currentData() or "")
        to_id = str(self._to_combo.currentData() or "")
        if not from_id or not to_id:
            return

        self.settings.set("from_unit_id", from_id)
        self.settings.set("to_unit_id", to_id)

        raw_text = self._value_input.text().strip()
        if not raw_text:
            self._show_input_error("Enter a numeric value to convert.")
            return

        try:
            numeric_val = float(raw_text)
        except ValueError:
            self._show_input_error("Invalid number format.")
            return

        try:
            primary: ConversionOutput = self.converter.convert(
                category_id=self._active_category_id,
                from_unit_id=from_id,
                to_unit_id=to_id,
                value=numeric_val,
                sig_figs=self._sig_figs,
            )
            all_outputs: list[ConversionOutput] = self.converter.convert_to_all(
                category_id=self._active_category_id,
                from_unit_id=from_id,
                value=numeric_val,
                sig_figs=self._sig_figs,
            )
        except UnitConversionError as exc:
            self._show_input_error(str(exc))
            return

        self._error_lbl.setVisible(False)
        self._result_lbl.setText(f"{primary.formatted_output} {primary.to_unit.symbol}")
        self._last_formatted_output = primary.formatted_output
        self._formula_badge.set_status(primary.formula_summary, variant="info")

        self._populate_equivalence_table(all_outputs)

    def _show_input_error(self, message: str) -> None:
        self._result_lbl.setText("—")
        self._error_lbl.setStyleSheet(
            f"color: {self.theme_manager.tokens.danger}; font-size: 12px; font-weight: 500;"
        )
        self._error_lbl.setText(message)
        self._error_lbl.setVisible(True)
        self._equiv_table.setRowCount(0)

    def _populate_equivalence_table(self, outputs: list[ConversionOutput]) -> None:
        self._equiv_table.setRowCount(len(outputs))
        for row_idx, item in enumerate(outputs):
            name_cell = QTableWidgetItem(item.to_unit.name)
            sym_cell = QTableWidgetItem(item.to_unit.symbol)
            val_cell = QTableWidgetItem(item.formatted_output)

            sym_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            val_cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_cell.setData(Qt.ItemDataRole.UserRole, item.formatted_output)

            self._equiv_table.setItem(row_idx, 0, name_cell)
            self._equiv_table.setItem(row_idx, 1, sym_cell)
            self._equiv_table.setItem(row_idx, 2, val_cell)

    def _on_table_row_clicked(self, row: int, _col: int) -> None:
        val_item = self._equiv_table.item(row, 2)
        unit_item = self._equiv_table.item(row, 0)
        if val_item is None:
            return
        formatted = str(val_item.text())
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(formatted)
            unit_name = unit_item.text() if unit_item is not None else "unit"
            self.set_status_message(
                f"Copied {formatted} ({unit_name}) to clipboard",
                timeout_ms=2500,
            )

    def _copy_result(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._last_formatted_output)
            self.set_status_message(
                f"Copied '{self._last_formatted_output}' to clipboard",
                timeout_ms=2200,
            )

    # ------------------------------------------------------------------
    # Global Unit Search & Pinned Favorites
    # ------------------------------------------------------------------

    def _on_search_changed(self, query: str) -> None:
        cleaned = query.strip()
        if not cleaned:
            self._search_results_list.clear()
            self._search_results_list.setVisible(False)
            return

        matches = self.converter.search_units(cleaned)
        self._search_results_list.clear()
        for cat, unit in matches[:20]:
            item = QListWidgetItem(f"{unit.name} ({unit.symbol}) — {cat.name}")
            item.setData(Qt.ItemDataRole.UserRole, (cat.category_id, unit.unit_id))
            self._search_results_list.addItem(item)

        self._search_results_list.setVisible(self._search_results_list.count() > 0)

    def _on_search_result_clicked(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        cat_id, unit_id = str(payload[0]), str(payload[1])
        cat = self.converter.get_category(cat_id)
        fallback_to = (
            cat.default_to
            if cat.default_to != unit_id
            else next((u.unit_id for u in cat.units if u.unit_id != unit_id), unit_id)
        )
        self._select_category(
            cat_id,
            override_from=unit_id,
            override_to=fallback_to,
        )
        self._search_input.clear()
        self._search_results_list.setVisible(False)

    def _pin_current_pair(self) -> None:
        from_id = str(self._from_combo.currentData() or "")
        to_id = str(self._to_combo.currentData() or "")
        if not from_id or not to_id or from_id == to_id:
            self.notify(
                "Select Distinct Units",
                "Choose two different units before pinning a pair.",
                level=NotificationLevel.WARNING,
            )
            return

        cat = self.converter.get_category(self._active_category_id)
        src: UnitDefinition = self.converter.get_unit(cat.category_id, from_id)
        dst: UnitDefinition = self.converter.get_unit(cat.category_id, to_id)

        # Avoid duplicate pinned pair
        for existing in self._favorites:
            if (
                existing.category_id == cat.category_id
                and existing.from_unit_id == from_id
                and existing.to_unit_id == to_id
            ):
                self.set_status_message("Unit pair is already pinned", timeout_ms=2000)
                return

        pair = FavoritePair(
            category_id=cat.category_id,
            from_unit_id=from_id,
            to_unit_id=to_id,
            label=f"{src.symbol} → {dst.symbol}  ({cat.name})",
        )
        self._favorites.insert(0, pair)
        if len(self._favorites) > _MAX_FAVORITES:
            self._favorites = self._favorites[:_MAX_FAVORITES]

        self._render_favorites()
        self._save_favorites()
        self.notify(
            "Pinned Unit Pair",
            f"Saved {src.symbol} → {dst.symbol} to Pinned Pairs.",
            level=NotificationLevel.SUCCESS,
        )

    def _on_favorite_clicked(self, item: QListWidgetItem) -> None:
        pair = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(pair, FavoritePair):
            self._select_category(
                pair.category_id,
                override_from=pair.from_unit_id,
                override_to=pair.to_unit_id,
            )

    def _clear_favorites(self) -> None:
        self._favorites.clear()
        self._render_favorites()
        self._save_favorites()

    def _render_favorites(self) -> None:
        self._favorites_list.clear()
        for pair in self._favorites:
            item = QListWidgetItem(pair.label)
            item.setData(Qt.ItemDataRole.UserRole, pair)
            self._favorites_list.addItem(item)

        has_favs = len(self._favorites) > 0
        self._favorites_list.setVisible(has_favs)
        self._no_fav_lbl.setVisible(not has_favs)

    def _save_favorites(self) -> None:
        payload = [
            {
                "category_id": f.category_id,
                "from_unit_id": f.from_unit_id,
                "to_unit_id": f.to_unit_id,
                "label": f.label,
            }
            for f in self._favorites
        ]
        self.settings.set("favorite_pairs", payload)

    def _restore_favorites(self) -> None:
        raw = self.settings.get("favorite_pairs", [])
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                try:
                    self._favorites.append(
                        FavoritePair(
                            category_id=str(entry["category_id"]),
                            from_unit_id=str(entry["from_unit_id"]),
                            to_unit_id=str(entry["to_unit_id"]),
                            label=str(entry["label"]),
                        )
                    )
                except KeyError:
                    continue
        self._render_favorites()

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        if self._error_lbl.isVisible() and self._error_lbl.text():
            self._show_input_error(self._error_lbl.text())
        QTimer.singleShot(0, self.update)


def main() -> int:
    """Application entry point for SimpleUnits.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleUnitsWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())