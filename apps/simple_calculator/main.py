"""SimpleCalculator — Standard & scientific desktop calculator for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from apps.simple_calculator.engine import (
    AngleMode,
    CalculatorError,
    EvaluationResult,
    MathEngine,
    format_number,
)
from core.notifications.notifier import NotificationLevel
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.widgets import (
    CardFrame,
    HorizontalDivider,
    SectionHeader,
    StatusBadge,
    make_button,
)

logger = logging.getLogger(__name__)

APP_ID = "simple_calculator"
_MAX_HISTORY_ITEMS = 100


@dataclass(frozen=True, slots=True)
class TapeEntry:
    """Single item stored in the calculation history tape."""

    expression: str
    formatted_result: str
    numeric_value: float
    angle_mode: str


class SimpleCalculatorWindow(BaseWindow):
    """Main window for SimpleCalculator with Standard/Scientific modes and history tape."""

    def __init__(self) -> None:
        super().__init__(app_id=APP_ID, min_width=780, min_height=580)
        self.engine = MathEngine()
        self._memory_value: float = 0.0
        self._has_memory: bool = False
        self._second_fn_active: bool = False
        self._just_evaluated: bool = False
        self._last_formatted_result: str = "0"
        self._tape_entries: list[TapeEntry] = []

        # Restore persisted calculator preferences
        saved_angle = str(self.settings.get("angle_mode", AngleMode.DEG.value)).upper()
        self.engine.angle_mode = (
            AngleMode.RAD if saved_angle == AngleMode.RAD.value else AngleMode.DEG
        )
        saved_mode = str(self.settings.get("calc_mode", "scientific")).lower()

        self._build_workspace()
        self._bind_shortcuts()
        self._restore_history_from_settings()
        self._set_calculator_mode(saved_mode if saved_mode in {"standard", "scientific"} else "scientific")
        self._sync_angle_ui()
        self._sync_memory_ui()

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(16)

        # ==============================================================
        # Left Column: Tactile Calculator Instrument Console
        # ==============================================================
        console_col = QVBoxLayout()
        console_col.setContentsMargins(0, 0, 0, 0)
        console_col.setSpacing(12)

        # --- Top Mode & Telemetry Strip ---
        top_strip = QHBoxLayout()
        top_strip.setSpacing(8)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)

        self._std_mode_btn = QPushButton("Standard", central)
        self._std_mode_btn.setProperty("variant", "segmented")
        self._std_mode_btn.setCheckable(True)
        self._std_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._std_mode_btn.clicked.connect(lambda: self._set_calculator_mode("standard"))

        self._sci_mode_btn = QPushButton("Scientific", central)
        self._sci_mode_btn.setProperty("variant", "segmented")
        self._sci_mode_btn.setCheckable(True)
        self._sci_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sci_mode_btn.clicked.connect(lambda: self._set_calculator_mode("scientific"))

        self._mode_group.addButton(self._std_mode_btn)
        self._mode_group.addButton(self._sci_mode_btn)
        top_strip.addWidget(self._std_mode_btn)
        top_strip.addWidget(self._sci_mode_btn)

        top_strip.addStretch(1)

        self._angle_btn = QPushButton("DEG", central)
        self._angle_btn.setProperty("variant", "segmented")
        self._angle_btn.setCheckable(True)
        self._angle_btn.setToolTip("Toggle trigonometric angle unit between Degrees (DEG) and Radians (RAD)")
        self._angle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._angle_btn.clicked.connect(self._toggle_angle_mode)
        top_strip.addWidget(self._angle_btn)

        self._mem_badge = StatusBadge("M", variant="info", parent=central)
        self._mem_badge.setToolTip("Memory register contains a stored value")
        top_strip.addWidget(self._mem_badge)

        copy_btn = make_button(
            text="Copy",
            variant="ghost",
            tooltip="Copy current result to clipboard (Ctrl+C)",
            on_click=self._copy_result,
            parent=central,
        )
        top_strip.addWidget(copy_btn)

        console_col.addLayout(top_strip)

        # --- Recessed LCD-Inspired Readout Well ---
        display_well = QFrame(central)
        display_well.setProperty("surface", "recessed")
        display_well.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        display_layout = QVBoxLayout(display_well)
        display_layout.setContentsMargins(16, 12, 16, 14)
        display_layout.setSpacing(6)

        # Breadcrumb / previous calculation line
        self._breadcrumb_label = QLabel("", display_well)
        self._breadcrumb_label.setProperty("role", "caption")
        self._breadcrumb_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._breadcrumb_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        display_layout.addWidget(self._breadcrumb_label)

        # Direct keyboard-editable expression line
        self._expr_input = QLineEdit(display_well)
        self._expr_input.setPlaceholderText("Type an expression or use keypad (e.g., 2 * sin(30) + sqrt(16))")
        self._expr_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._expr_input.setStyleSheet(
            "QLineEdit { border: none; background: transparent; font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 14px; padding: 2px 0px; }"
        )
        self._expr_input.returnPressed.connect(self.evaluate_current)
        self._expr_input.textEdited.connect(self._on_expression_edited)
        display_layout.addWidget(self._expr_input)

        # Primary large numeric readout
        self._readout_label = QLabel("0", display_well)
        self._readout_label.setProperty("role", "display-xl")
        self._readout_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._readout_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._readout_label.setMinimumHeight(44)
        display_layout.addWidget(self._readout_label)

        # Inline non-intrusive error message banner
        self._error_label = QLabel("", display_well)
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        display_layout.addWidget(self._error_label)

        console_col.addWidget(display_well)

        # --- Memory Register Row ---
        mem_row = QHBoxLayout()
        mem_row.setSpacing(6)
        mem_specs: tuple[tuple[str, str, str], ...] = (
            ("MC", "Memory Clear", "mc"),
            ("MR", "Memory Recall", "mr"),
            ("M+", "Add result to Memory", "m+"),
            ("M−", "Subtract result from Memory", "m-"),
            ("MS", "Store result in Memory", "ms"),
        )
        self._mem_buttons: dict[str, QPushButton] = {}
        for label, tip, action_code in mem_specs:
            btn = QPushButton(label, central)
            btn.setProperty("variant", "key-fn")
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(30)
            btn.clicked.connect(lambda _=False, code=action_code: self._handle_memory_action(code))
            mem_row.addWidget(btn, 1)
            self._mem_buttons[action_code] = btn
        console_col.addLayout(mem_row)

        # --- Collapsible Scientific Function Grid ---
        self._sci_panel = QWidget(central)
        sci_grid = QGridLayout(self._sci_panel)
        sci_grid.setContentsMargins(0, 0, 0, 0)
        sci_grid.setSpacing(6)

        self._second_btn = QPushButton("2nd", self._sci_panel)
        self._second_btn.setProperty("variant", "segmented")
        self._second_btn.setCheckable(True)
        self._second_btn.setToolTip("Toggle inverse trigonometric and exponential functions")
        self._second_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._second_btn.setMinimumHeight(34)
        self._second_btn.clicked.connect(self._toggle_second_functions)
        sci_grid.addWidget(self._second_btn, 0, 0)

        self._shiftable_buttons: dict[str, QPushButton] = {}
        shift_specs: tuple[tuple[int, int, str, str, str], ...] = (
            (0, 1, "sin", "sin(", "Sine"),
            (0, 2, "cos", "cos(", "Cosine"),
            (0, 3, "tan", "tan(", "Tangent"),
            (0, 4, "ln", "ln(", "Natural logarithm (base e)"),
            (1, 0, "log", "log(", "Base-10 logarithm"),
            (1, 1, "√", "√(", "Square root"),
            (1, 2, "x²", "^2", "Square"),
            (1, 3, "xʸ", "^", "Raise to power y"),
            (1, 4, "1/x", "^(-1)", "Reciprocal"),
        )
        for row, col, key_id, token, tip in shift_specs:
            btn = QPushButton(key_id, self._sci_panel)
            btn.setProperty("variant", "key-fn")
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(34)
            btn.setProperty("primary_label", key_id)
            btn.setProperty("primary_token", token)
            btn.clicked.connect(lambda _=False, b=btn: self._on_shiftable_clicked(b))
            sci_grid.addWidget(btn, row, col)
            self._shiftable_buttons[key_id] = btn

        # Configure secondary labels/tokens for shiftable keys
        self._configure_shift_metadata()

        static_sci_specs: tuple[tuple[int, int, str, str, str], ...] = (
            (2, 0, "(", "(", "Open parenthesis"),
            (2, 1, ")", ")", "Close parenthesis"),
            (2, 2, "n!", "!", "Factorial"),
            (2, 3, "π", "π", "Pi (3.14159265...)"),
            (2, 4, "Ans", "Ans", "Insert previous calculation result"),
        )
        for row, col, label, token, tip in static_sci_specs:
            btn = QPushButton(label, self._sci_panel)
            btn.setProperty("variant", "key-fn")
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(34)
            btn.clicked.connect(lambda _=False, t=token: self._insert_token(t))
            sci_grid.addWidget(btn, row, col)

        console_col.addWidget(self._sci_panel)

        # --- Primary Tactile Keypad Grid (4x5) ---
        keypad_grid = QGridLayout()
        keypad_grid.setContentsMargins(0, 0, 0, 0)
        keypad_grid.setSpacing(7)

        keypad_specs: tuple[tuple[int, int, str, str, str], ...] = (
            (0, 0, "C", "key-danger", "clear"),
            (0, 1, "⌫", "key-fn", "backspace"),
            (0, 2, "%", "key-op", "token:%"),
            (0, 3, "÷", "key-op", "token:÷"),
            (1, 0, "7", "key-num", "token:7"),
            (1, 1, "8", "key-num", "token:8"),
            (1, 2, "9", "key-num", "token:9"),
            (1, 3, "×", "key-op", "token:×"),
            (2, 0, "4", "key-num", "token:4"),
            (2, 1, "5", "key-num", "token:5"),
            (2, 2, "6", "key-num", "token:6"),
            (2, 3, "−", "key-op", "token:−"),
            (3, 0, "1", "key-num", "token:1"),
            (3, 1, "2", "key-num", "token:2"),
            (3, 2, "3", "key-num", "token:3"),
            (3, 3, "+", "key-op", "token:+"),
            (4, 0, "±", "key-num", "negate"),
            (4, 1, "0", "key-num", "token:0"),
            (4, 2, ".", "key-num", "token:."),
            (4, 3, "=", "primary", "equals"),
        )

        for row, col, label, variant, action in keypad_specs:
            btn = QPushButton(label, central)
            btn.setProperty("variant", variant)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(46)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            if action == "equals":
                btn.setStyleSheet("font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 18px;")
            btn.clicked.connect(lambda _=False, act=action: self._handle_keypad_action(act))
            keypad_grid.addWidget(btn, row, col)

        console_col.addLayout(keypad_grid, 1)
        root.addLayout(console_col, 3)

        # ==============================================================
        # Right Column: Paper Calculation Tape Sidebar
        # ==============================================================
        tape_card = CardFrame(padding=14, spacing=10, parent=central)
        tape_card.setMinimumWidth(250)
        tape_card.setMaximumWidth(330)

        clear_tape_btn = make_button(
            text="Clear",
            variant="ghost",
            tooltip="Clear calculation history tape",
            on_click=self._clear_tape,
            parent=tape_card,
        )
        tape_card.body_layout.addWidget(
            SectionHeader("Calculation Tape", accessory=clear_tape_btn, parent=tape_card)
        )

        hint_lbl = QLabel("Click an item to reuse its result. Double-click to copy.", tape_card)
        hint_lbl.setProperty("role", "caption")
        hint_lbl.setWordWrap(True)
        tape_card.body_layout.addWidget(hint_lbl)

        tape_card.body_layout.addWidget(HorizontalDivider(tape_card))

        self._tape_list = QListWidget(tape_card)
        self._tape_list.setAlternatingRowColors(True)
        self._tape_list.setWordWrap(True)
        self._tape_list.itemClicked.connect(self._on_tape_item_clicked)
        self._tape_list.itemDoubleClicked.connect(self._on_tape_item_double_clicked)
        tape_card.body_layout.addWidget(self._tape_list, 1)

        self._empty_tape_lbl = QLabel(
            "No calculations yet.\nYour evaluated expressions will appear here.",
            tape_card,
        )
        self._empty_tape_lbl.setProperty("role", "muted")
        self._empty_tape_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_tape_lbl.setWordWrap(True)
        tape_card.body_layout.addWidget(self._empty_tape_lbl, 1)

        root.addWidget(tape_card, 2)

    def _configure_shift_metadata(self) -> None:
        """Attach secondary (2nd) labels and tokens to shiftable scientific keys."""
        secondary_map: dict[str, tuple[str, str]] = {
            "sin": ("asin", "asin("),
            "cos": ("acos", "acos("),
            "tan": ("atan", "atan("),
            "ln": ("eˣ", "exp("),
            "log": ("10ˣ", "10^"),
            "√": ("∛", "cbrt("),
            "x²": ("x³", "^3"),
            "xʸ": ("e", "e"),
            "1/x": ("|x|", "abs("),
        }
        for key_id, (sec_label, sec_token) in secondary_map.items():
            btn = self._shiftable_buttons[key_id]
            btn.setProperty("secondary_label", sec_label)
            btn.setProperty("secondary_token", sec_token)

    def _bind_shortcuts(self) -> None:
        """Bind convenient global keyboard shortcuts."""
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        copy_shortcut.activated.connect(self._copy_if_no_selection)

        clear_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        clear_shortcut.activated.connect(self.clear_all)

    # ------------------------------------------------------------------
    # Mode, Angle & Shift Controls
    # ------------------------------------------------------------------

    def _set_calculator_mode(self, mode: str) -> None:
        is_scientific = mode == "scientific"
        self._sci_mode_btn.setChecked(is_scientific)
        self._std_mode_btn.setChecked(not is_scientific)
        self._sci_panel.setVisible(is_scientific)
        self._angle_btn.setVisible(is_scientific)
        self.settings.set("calc_mode", mode)

    def _toggle_angle_mode(self) -> None:
        self.engine.angle_mode = (
            AngleMode.RAD if self.engine.angle_mode == AngleMode.DEG else AngleMode.DEG
        )
        self.settings.set("angle_mode", self.engine.angle_mode.value)
        self._sync_angle_ui()
        self.set_status_message(
            f"Trigonometry angle mode: {self.engine.angle_mode.value}",
            timeout_ms=2200,
        )

    def _sync_angle_ui(self) -> None:
        is_rad = self.engine.angle_mode == AngleMode.RAD
        self._angle_btn.setText(self.engine.angle_mode.value)
        self._angle_btn.setChecked(is_rad)

    def _toggle_second_functions(self) -> None:
        self._second_fn_active = self._second_btn.isChecked()
        for btn in self._shiftable_buttons.values():
            label_key = "secondary_label" if self._second_fn_active else "primary_label"
            btn.setText(str(btn.property(label_key)))

    def _on_shiftable_clicked(self, btn: QPushButton) -> None:
        token_key = "secondary_token" if self._second_fn_active else "primary_token"
        self._insert_token(str(btn.property(token_key)))

    # ------------------------------------------------------------------
    # Input Editing & Evaluation
    # ------------------------------------------------------------------

    def _clear_error_state(self) -> None:
        if self._error_label.isVisible():
            self._error_label.clear()
            self._error_label.setVisible(False)

    def _show_inline_error(self, message: str) -> None:
        danger_color = self.theme_manager.tokens.danger
        self._error_label.setStyleSheet(
            f"color: {danger_color}; font-size: 12px; font-weight: 500;"
        )
        self._error_label.setText(message)
        self._error_label.setVisible(True)
        self.set_status_message(message, timeout_ms=4000)

    def _on_expression_edited(self, _text: str) -> None:
        self._just_evaluated = False
        self._clear_error_state()

    def _insert_token(self, token: str) -> None:
        """Insert a digit, operator, or function token into the expression input cleanly."""
        self._clear_error_state()
        binary_ops = {"+", "−", "×", "÷", "^", "%"}

        if self._just_evaluated:
            if token in binary_ops or token in {"^2", "^3", "^(-1)", "!"}:
                # Chain operation onto the previous answer
                self._expr_input.setText(self._last_formatted_result)
                self._expr_input.setCursorPosition(len(self._expr_input.text()))
            else:
                self._expr_input.clear()
            self._just_evaluated = False

        self._expr_input.insert(token)
        self._expr_input.setFocus()

    def clear_all(self) -> None:
        """Reset active expression, error banner, and primary display to 0."""
        self._expr_input.clear()
        self._breadcrumb_label.clear()
        self._readout_label.setText("0")
        self._last_formatted_result = "0"
        self._just_evaluated = False
        self._clear_error_state()
        self._expr_input.setFocus()

    def backspace(self) -> None:
        """Delete one character before the cursor in the expression input."""
        self._clear_error_state()
        self._just_evaluated = False
        self._expr_input.backspace()
        self._expr_input.setFocus()

    def toggle_sign(self) -> None:
        """Negate the current expression or toggle a leading minus sign."""
        self._clear_error_state()
        text = self._expr_input.text().strip()
        if not text:
            if self._last_formatted_result != "0":
                text = self._last_formatted_result
            else:
                self._insert_token("-")
                return

        if text.startswith("-(") and text.endswith(")"):
            self._expr_input.setText(text[2:-1])
        elif text.startswith("-"):
            self._expr_input.setText(text[1:])
        else:
            self._expr_input.setText(f"-({text})")
        self._just_evaluated = False
        self._expr_input.setFocus()

    def _handle_keypad_action(self, action: str) -> None:
        if action == "clear":
            self.clear_all()
        elif action == "backspace":
            self.backspace()
        elif action == "negate":
            self.toggle_sign()
        elif action == "equals":
            self.evaluate_current()
        elif action.startswith("token:"):
            self._insert_token(action.split(":", 1)[1])

    def evaluate_current(self) -> EvaluationResult | None:
        """Evaluate the active expression, update readout, and append to calculation tape."""
        raw_expr = self._expr_input.text().strip()
        if not raw_expr:
            return None

        try:
            result = self.engine.evaluate(raw_expr, update_ans=True)
        except CalculatorError as exc:
            self._show_inline_error(str(exc))
            return None

        self._clear_error_state()
        self._breadcrumb_label.setText(f"{result.expression} =")
        self._readout_label.setText(result.formatted)
        self._last_formatted_result = result.formatted
        self._just_evaluated = True

        self._append_to_tape(
            TapeEntry(
                expression=result.expression,
                formatted_result=result.formatted,
                numeric_value=result.value,
                angle_mode=result.angle_mode.value,
            )
        )
        self.set_status_message("Calculated", timeout_ms=2000)
        return result

    # ------------------------------------------------------------------
    # Memory Register Operations
    # ------------------------------------------------------------------

    def _current_numeric_value(self) -> float | None:
        raw_expr = self._expr_input.text().strip()
        if raw_expr and not self._just_evaluated:
            try:
                res = self.engine.evaluate(raw_expr, update_ans=False)
                return res.value
            except CalculatorError as exc:
                self._show_inline_error(str(exc))
                return None
        try:
            return float(self._last_formatted_result)
        except ValueError:
            return 0.0

    def _handle_memory_action(self, code: str) -> None:
        if code == "mc":
            self._memory_value = 0.0
            self._has_memory = False
            self._sync_memory_ui()
            self.set_status_message("Memory cleared", timeout_ms=2000)
            return

        if code == "mr":
            if not self._has_memory:
                return
            formatted_mem = format_number(self._memory_value)
            self._insert_token(formatted_mem)
            return

        val = self._current_numeric_value()
        if val is None:
            return

        if code == "ms":
            self._memory_value = val
            self._has_memory = True
        elif code == "m+":
            self._memory_value += val
            self._has_memory = True
        elif code == "m-":
            self._memory_value -= val
            self._has_memory = True

        self._sync_memory_ui()
        self.set_status_message(
            f"Memory: {format_number(self._memory_value)}",
            timeout_ms=2500,
        )

    def _sync_memory_ui(self) -> None:
        self._mem_badge.setVisible(self._has_memory)
        if self._has_memory:
            self._mem_badge.set_status(
                f"M: {format_number(self._memory_value)}",
                variant="info",
            )
        self._mem_buttons["mc"].setEnabled(self._has_memory)
        self._mem_buttons["mr"].setEnabled(self._has_memory)

    # ------------------------------------------------------------------
    # Calculation Tape & Clipboard
    # ------------------------------------------------------------------

    def _append_to_tape(self, entry: TapeEntry, persist: bool = True) -> None:
        self._tape_entries.insert(0, entry)
        if len(self._tape_entries) > _MAX_HISTORY_ITEMS:
            self._tape_entries = self._tape_entries[:_MAX_HISTORY_ITEMS]

        item = QListWidgetItem(f"{entry.expression}\n= {entry.formatted_result}")
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setToolTip(f"Angle mode: {entry.angle_mode} — Click to insert result")
        self._tape_list.insertItem(0, item)

        while self._tape_list.count() > _MAX_HISTORY_ITEMS:
            self._tape_list.takeItem(self._tape_list.count() - 1)

        self._sync_tape_visibility()
        if persist:
            self._save_history_to_settings()

    def _sync_tape_visibility(self) -> None:
        has_items = len(self._tape_entries) > 0
        self._tape_list.setVisible(has_items)
        self._empty_tape_lbl.setVisible(not has_items)

    def _on_tape_item_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, TapeEntry):
            self._insert_token(entry.formatted_result)

    def _on_tape_item_double_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, TapeEntry):
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(entry.formatted_result)
                self.notify(
                    "Copied Result",
                    f"Copied '{entry.formatted_result}' to clipboard.",
                    level=NotificationLevel.INFO,
                )

    def _clear_tape(self) -> None:
        self._tape_entries.clear()
        self._tape_list.clear()
        self._sync_tape_visibility()
        self._save_history_to_settings()
        self.set_status_message("Calculation tape cleared", timeout_ms=2000)

    def _save_history_to_settings(self) -> None:
        serialized = [
            {
                "expression": e.expression,
                "formatted_result": e.formatted_result,
                "numeric_value": e.numeric_value,
                "angle_mode": e.angle_mode,
            }
            for e in self._tape_entries
        ]
        self.settings.set("history_tape", serialized)

    def _restore_history_from_settings(self) -> None:
        raw = self.settings.get("history_tape", [])
        if isinstance(raw, list):
            for record in reversed(raw):
                if not isinstance(record, dict):
                    continue
                try:
                    entry = TapeEntry(
                        expression=str(record["expression"]),
                        formatted_result=str(record["formatted_result"]),
                        numeric_value=float(record["numeric_value"]),
                        angle_mode=str(record.get("angle_mode", AngleMode.DEG.value)),
                    )
                    self._append_to_tape(entry, persist=False)
                except (KeyError, ValueError, TypeError):
                    continue
        self._sync_tape_visibility()

    def _copy_if_no_selection(self) -> None:
        if not self._expr_input.hasSelectedText():
            self._copy_result()

    def _copy_result(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._last_formatted_result)
            self.set_status_message(
                f"Copied '{self._last_formatted_result}' to clipboard",
                timeout_ms=2200,
            )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Forward direct keyboard input to the expression bar when focus is elsewhere."""
        if not self._expr_input.hasFocus():
            key = event.key()
            text = event.text()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.evaluate_current()
                return
            if key == Qt.Key.Key_Backspace:
                self.backspace()
                return
            if text and text.isprintable() and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._expr_input.setFocus()
                self._insert_token(text)
                return
        super().keyPressEvent(event)

    def on_theme_updated(self, theme_name: str) -> None:
        """Refresh error label color if visible when theme switches."""
        _ = theme_name
        if self._error_label.isVisible() and self._error_label.text():
            self._show_inline_error(self._error_label.text())
        QTimer.singleShot(0, self.update)


def main() -> int:
    """Application entry point for SimpleCalculator.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleCalculatorWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())