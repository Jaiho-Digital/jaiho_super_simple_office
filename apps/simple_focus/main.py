"""SimpleFocus — Minimalist Braun-inspired Pomodoro & focus timer for Jaiho Super Simple Office."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPaintEvent, QPen, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from apps.simple_focus.audio import (
    BUILTIN_SOUND_OPTIONS,
    SOUND_CHIME,
    SOUND_CUSTOM,
    SOUND_SILENT,
    AlarmAudioPlayer,
    has_custom_wav,
    import_custom_wav,
)
from apps.simple_focus.repository import APP_ID, FocusPhase, FocusRepository
from core.file_manager.file_ops import FileOperationError
from core.notifications.notifier import NotificationLevel
from core.themes.tokens import LIGHT_THEME, ThemeTokens
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import show_error_dialog
from core.ui.widgets import CardFrame, make_button

logger = logging.getLogger(__name__)

_ACTION_INSERT_CUSTOM = "__insert_custom__"

_PHASE_PRESETS: dict[FocusPhase, tuple[int, ...]] = {
    FocusPhase.FOCUS: (15, 25, 45, 60),
    FocusPhase.SHORT_BREAK: (5, 10, 15),
    FocusPhase.LONG_BREAK: (15, 20, 30),
}


def format_mm_ss(total_seconds: float) -> str:
    """Format remaining seconds cleanly as zero-padded MM:SS."""
    whole = max(0, math.ceil(total_seconds))
    minutes, seconds = divmod(whole, 60)
    return f"{minutes:02d}:{seconds:02d}"


class SimpleDialWidget(QWidget):
    """Minimalist Braun-inspired circular timer face with single progress arc."""

    def __init__(self, tokens: ThemeTokens = LIGHT_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._phase = FocusPhase.FOCUS
        self._progress_ratio = 1.0
        self._time_text = "25:00"
        self._cycle_text = "SESSION 1 OF 4"
        self._ringing = False

        self.setMinimumSize(235, 235)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def set_state(
        self,
        *,
        phase: FocusPhase,
        progress_ratio: float,
        time_text: str,
        cycle_text: str,
        ringing: bool = False,
    ) -> None:
        self._phase = phase
        self._progress_ratio = max(0.0, min(1.0, float(progress_ratio)))
        self._time_text = time_text
        self._cycle_text = cycle_text
        self._ringing = ringing
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())
        side = min(w, h) - 12.0
        if side <= 40.0:
            painter.end()
            return

        cx = w / 2.0
        cy = h / 2.0
        radius = side / 2.0

        # Recessed dial plate
        dial_rect = QRectF(cx - radius, cy - radius, side, side)
        border_col = self._tokens.danger if self._ringing else self._tokens.border_strong
        painter.setPen(QPen(QColor(border_col), 1.6 if self._ringing else 1.2))
        painter.setBrush(QColor(self._tokens.bg_recessed))
        painter.drawEllipse(dial_rect)

        # 12 subtle hour-style index markers
        for idx in range(12):
            angle = math.radians(idx * 30.0 - 90.0)
            r1 = radius - 14.0
            r2 = radius - 8.0
            painter.setPen(
                QPen(
                    QColor(self._tokens.border_strong),
                    1.5,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(
                int(round(cx + r1 * math.cos(angle))),
                int(round(cy + r1 * math.sin(angle))),
                int(round(cx + r2 * math.cos(angle))),
                int(round(cy + r2 * math.sin(angle))),
            )

        # Track ring
        arc_r = radius - 22.0
        arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2.0, arc_r * 2.0)
        painter.setPen(
            QPen(
                QColor(self._tokens.bg_elevated),
                6.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(arc_rect, 0, 360 * 16)

        # Active remaining arc
        if self._progress_ratio > 0.001:
            accent = (
                self._tokens.accent_primary
                if self._phase == FocusPhase.FOCUS
                else self._tokens.success
            )
            painter.setPen(QPen(QColor(accent), 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(arc_rect, 90 * 16, int(round(-360.0 * 16.0 * self._progress_ratio)))

        # Upper phase label
        phase_font = QFont("Segoe UI Variable Text", 9, QFont.Weight.DemiBold)
        phase_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.3)
        painter.setFont(phase_font)
        upper_color = (
            self._tokens.danger
            if self._ringing
            else (
                self._tokens.accent_primary
                if self._phase == FocusPhase.FOCUS
                else self._tokens.success
            )
        )
        painter.setPen(QColor(upper_color))
        upper_text = "TIME'S UP" if self._ringing else self._phase.display_name.upper()
        painter.drawText(
            QRectF(cx - arc_r, cy - arc_r * 0.56, arc_r * 2.0, 22.0),
            Qt.AlignmentFlag.AlignCenter,
            upper_text,
        )

        # Large centered MM:SS readout
        digit_px = max(26, int(arc_r * 0.38))
        time_font = QFont("Cascadia Code", digit_px, QFont.Weight.Bold)
        painter.setFont(time_font)
        painter.setPen(QColor(self._tokens.text_primary))
        painter.drawText(
            QRectF(cx - arc_r, cy - digit_px * 0.85, arc_r * 2.0, digit_px * 1.8),
            Qt.AlignmentFlag.AlignCenter,
            self._time_text,
        )

        # Subtle cycle caption below digits
        sub_font = QFont("Segoe UI Variable Text", 9)
        painter.setFont(sub_font)
        painter.setPen(
            QColor(self._tokens.danger if self._ringing else self._tokens.text_muted)
        )
        painter.drawText(
            QRectF(cx - arc_r, cy + arc_r * 0.32, arc_r * 2.0, 20.0),
            Qt.AlignmentFlag.AlignCenter,
            "CLICK STOP ALARM" if self._ringing else self._cycle_text,
        )

        painter.end()


class SimpleFocusWindow(BaseWindow):
    """Streamlined single-card focus timer with multi-sound alarms and tray background support."""

    def __init__(
        self,
        repository: FocusRepository | None = None,
        audio_player: AlarmAudioPlayer | None = None,
    ) -> None:
        super().__init__(app_id=APP_ID, min_width=450, min_height=580)
        self.repo = repository or FocusRepository()
        self.audio = audio_player or AlarmAudioPlayer()

        if self.settings.get("window_width") is None:
            self.resize(480, 620)

        self._durations: dict[FocusPhase, int] = {
            FocusPhase.FOCUS: int(self.settings.get("focus_minutes", 25)),
            FocusPhase.SHORT_BREAK: int(self.settings.get("short_break_minutes", 5)),
            FocusPhase.LONG_BREAK: int(self.settings.get("long_break_minutes", 15)),
        }
        self._selected_sound: str = str(self.settings.get("alarm_sound", SOUND_CHIME))
        self._custom_sound_label: str = str(self.settings.get("custom_sound_name", "Custom Sound"))
        if self._selected_sound == SOUND_CUSTOM and not has_custom_wav():
            self._selected_sound = SOUND_CHIME

        self._long_break_interval: int = 4
        self._current_phase: FocusPhase = FocusPhase.FOCUS
        self._completed_focus_in_cycle: int = 0
        self._timer_state: str = "idle"  # 'idle' | 'running' | 'paused'
        self._phase_total_seconds: int = self._durations[FocusPhase.FOCUS] * 60
        self._remaining_seconds: float = float(self._phase_total_seconds)
        self._last_tick_mono: float | None = None
        self._session_started_at: datetime | None = None
        self._force_quit: bool = False
        self._tray_hint_shown: bool = False

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(200)
        self._tick_timer.timeout.connect(self._on_timer_tick)

        self._build_workspace()
        self._populate_sound_selector()
        self._setup_tray_behavior()
        self._bind_shortcuts()

        self._sync_phase_ui()
        self._sync_controls()
        self._refresh_dial()
        self._refresh_today_summary()

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(12)

        # --- Top Phase Segmented Selector ---
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self._phase_group = QButtonGroup(self)
        self._phase_group.setExclusive(True)

        self._focus_btn = QPushButton("Focus", central)
        self._focus_btn.setProperty("variant", "segmented")
        self._focus_btn.setCheckable(True)
        self._focus_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._focus_btn.clicked.connect(lambda: self.select_phase(FocusPhase.FOCUS))

        self._short_btn = QPushButton("Short Break", central)
        self._short_btn.setProperty("variant", "segmented")
        self._short_btn.setCheckable(True)
        self._short_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._short_btn.clicked.connect(lambda: self.select_phase(FocusPhase.SHORT_BREAK))

        self._long_btn = QPushButton("Long Break", central)
        self._long_btn.setProperty("variant", "segmented")
        self._long_btn.setCheckable(True)
        self._long_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._long_btn.clicked.connect(lambda: self.select_phase(FocusPhase.LONG_BREAK))

        for btn in (self._focus_btn, self._short_btn, self._long_btn):
            self._phase_group.addButton(btn)
            top_row.addWidget(btn, 1)

        root.addLayout(top_row)

        # --- Single Tactile Focus Card ---
        card = CardFrame(padding=18, spacing=12, parent=central)

        self._task_input = QLineEdit(card)
        self._task_input.setPlaceholderText("What are you focusing on? (optional)")
        self._task_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._task_input.setText(str(self.settings.get("last_intention", "")))
        self._task_input.textChanged.connect(lambda txt: self.settings.set("last_intention", txt))
        card.body_layout.addWidget(self._task_input)

        self._dial = SimpleDialWidget(tokens=self.theme_manager.tokens, parent=card)
        card.body_layout.addWidget(self._dial, 1)

        # Compact Duration Preset Pills
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        preset_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preset_buttons: list[QPushButton] = []
        for _ in range(4):
            p_btn = QPushButton("", card)
            p_btn.setProperty("variant", "segmented")
            p_btn.setCheckable(True)
            p_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            p_btn.setStyleSheet("padding: 4px 12px; font-size: 12px;")
            p_btn.clicked.connect(lambda _=False, b=p_btn: self._on_preset_clicked(b))
            preset_row.addWidget(p_btn)
            self._preset_buttons.append(p_btn)
        card.body_layout.addLayout(preset_row)

        # Compact Alarm Sound Selector + Preview + Custom Insert
        sound_row = QHBoxLayout()
        sound_row.setSpacing(6)

        sound_lbl = QLabel("Alarm:", card)
        sound_lbl.setProperty("role", "muted")
        sound_row.addWidget(sound_lbl)

        self._sound_combo = QComboBox(card)
        self._sound_combo.setToolTip("Choose built-in alarm tone or insert your own .wav file")
        self._sound_combo.currentIndexChanged.connect(self._on_sound_selection_changed)
        sound_row.addWidget(self._sound_combo, 1)

        self._preview_btn = make_button(
            text="▶",
            variant="ghost",
            tooltip="Preview selected alarm sound",
            on_click=self._preview_selected_sound,
            parent=card,
        )
        self._preview_btn.setFixedWidth(34)
        sound_row.addWidget(self._preview_btn)

        card.body_layout.addLayout(sound_row)

        # Primary Transport Controls
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        self._stop_alarm_btn = make_button(
            text="Stop Alarm",
            variant="danger",
            tooltip="Silence the continuous alarm sound (Space or Esc)",
            on_click=self.stop_alarm,
            parent=card,
        )
        self._stop_alarm_btn.setMinimumHeight(44)
        self._stop_alarm_btn.setVisible(False)
        controls_row.addWidget(self._stop_alarm_btn, 1)

        self._primary_btn = make_button(
            text="Start",
            variant="primary",
            tooltip="Start or pause timer (Space)",
            on_click=self.toggle_start_pause,
            parent=card,
        )
        self._primary_btn.setMinimumHeight(44)
        controls_row.addWidget(self._primary_btn, 2)

        self._reset_btn = make_button(
            text="Reset",
            tooltip="Reset current timer (Ctrl+R)",
            on_click=self.reset_timer,
            parent=card,
        )
        self._reset_btn.setMinimumHeight(44)
        controls_row.addWidget(self._reset_btn, 1)

        self._skip_btn = make_button(
            text="Skip",
            variant="ghost",
            tooltip="Skip to next phase",
            on_click=self.skip_phase,
            parent=card,
        )
        self._skip_btn.setMinimumHeight(44)
        controls_row.addWidget(self._skip_btn)

        card.body_layout.addLayout(controls_row)
        root.addWidget(card, 1)

        # --- Quiet Single-Line Daily Summary ---
        self._summary_lbl = QLabel("Today: 0 sessions  •  0 min focused", central)
        self._summary_lbl.setProperty("role", "muted")
        self._summary_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._summary_lbl)

    def _setup_tray_behavior(self) -> None:
        self.exit_action.triggered.disconnect()
        self.exit_action.triggered.connect(self.quit_application)

        tray = self.notifier._tray_icon
        if tray is None:
            return

        tray.activated.connect(self._on_tray_activated)
        tray_menu = QMenu(self)

        open_act = QAction("Open SimpleFocus", self)
        open_act.triggered.connect(self._restore_from_tray)
        tray_menu.addAction(open_act)

        toggle_act = QAction("Start / Pause", self)
        toggle_act.triggered.connect(self.toggle_start_pause)
        tray_menu.addAction(toggle_act)

        stop_alarm_act = QAction("Stop Alarm", self)
        stop_alarm_act.triggered.connect(self.stop_alarm)
        tray_menu.addAction(stop_alarm_act)

        tray_menu.addSeparator()
        quit_act = QAction("Quit SimpleFocus", self)
        quit_act.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_act)

        tray.setContextMenu(tray_menu)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.reset_timer)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self).activated.connect(
            self._handle_space_shortcut
        )
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self.stop_alarm)

    def _handle_space_shortcut(self) -> None:
        if self._task_input.hasFocus():
            return
        if self.audio.is_ringing:
            self.stop_alarm()
        else:
            self.toggle_start_pause()

    # ------------------------------------------------------------------
    # Multi-Sound Selection, Custom WAV Import & Alarm Controls
    # ------------------------------------------------------------------

    def _populate_sound_selector(self) -> None:
        self._sound_combo.blockSignals(True)
        try:
            self._sound_combo.clear()
            for sound_id, label in BUILTIN_SOUND_OPTIONS:
                self._sound_combo.addItem(label, sound_id)

            if has_custom_wav():
                self._sound_combo.addItem(f"Custom: {self._custom_sound_label}", SOUND_CUSTOM)

            self._sound_combo.addItem("+ Insert Custom (.wav)...", _ACTION_INSERT_CUSTOM)

            target_idx = self._sound_combo.findData(self._selected_sound)
            self._sound_combo.setCurrentIndex(target_idx if target_idx >= 0 else 0)
        finally:
            self._sound_combo.blockSignals(False)

        self._preview_btn.setEnabled(self._selected_sound != SOUND_SILENT)

    def _on_sound_selection_changed(self, index: int) -> None:
        if index < 0:
            return
        chosen = str(self._sound_combo.itemData(index) or SOUND_CHIME)

        if chosen == _ACTION_INSERT_CUSTOM:
            self._prompt_insert_custom_sound()
            return

        self.stop_alarm()
        self._selected_sound = chosen
        self.settings.set("alarm_sound", chosen)
        self._preview_btn.setEnabled(chosen != SOUND_SILENT)

        if chosen != SOUND_SILENT:
            self.audio.play_preview(chosen)
            self.set_status_message(
                f"Alarm sound: {self._sound_combo.currentText()}",
                timeout_ms=2200,
            )
        else:
            self.set_status_message("Alarm sound muted", timeout_ms=2000)

    def _prompt_insert_custom_sound(self) -> None:
        selected_file, _ = QFileDialog.getOpenFileName(
            self,
            "Select Custom Alarm Sound (.wav)",
            str(Path.home() / "Music"),
            "WAV Audio Files (*.wav)",
        )
        if not selected_file:
            # Revert combo selection back to previous sound
            self._populate_sound_selector()
            return

        try:
            import_custom_wav(selected_file)
        except FileOperationError as exc:
            show_error_dialog(self, "Invalid Audio File", str(exc))
            self._populate_sound_selector()
            return

        stem_name = Path(selected_file).stem[:32] or "Custom Sound"
        self._custom_sound_label = stem_name
        self._selected_sound = SOUND_CUSTOM
        self.settings.set("custom_sound_name", stem_name)
        self.settings.set("alarm_sound", SOUND_CUSTOM)

        self._populate_sound_selector()
        self.audio.play_preview(SOUND_CUSTOM)
        self.notify(
            "Custom Alarm Added",
            f"Loaded '{stem_name}.wav' as your completion alarm.",
            level=NotificationLevel.SUCCESS,
        )

    def _preview_selected_sound(self) -> None:
        if self.audio.is_ringing:
            self.stop_alarm()
            return
        if self._selected_sound != SOUND_SILENT:
            self.audio.play_preview(self._selected_sound)

    def stop_alarm(self) -> None:
        """Silence any continuously ringing completion alarm and restore standard controls."""
        was_ringing = self.audio.is_ringing
        self.audio.stop()
        if was_ringing:
            self._sync_controls()
            self._refresh_dial()
            self.set_status_message("Alarm stopped", timeout_ms=2000)

    # ------------------------------------------------------------------
    # Phase, Duration Presets & Timer Controls
    # ------------------------------------------------------------------

    def select_phase(self, phase: FocusPhase) -> None:
        self.audio.stop()
        self._tick_timer.stop()
        self._current_phase = phase
        self._phase_total_seconds = self._durations[phase] * 60
        self._remaining_seconds = float(self._phase_total_seconds)
        self._last_tick_mono = None
        self._session_started_at = None
        self._timer_state = "idle"

        self._sync_phase_ui()
        self._sync_controls()
        self._refresh_dial()

    def _sync_phase_ui(self) -> None:
        self._focus_btn.setChecked(self._current_phase == FocusPhase.FOCUS)
        self._short_btn.setChecked(self._current_phase == FocusPhase.SHORT_BREAK)
        self._long_btn.setChecked(self._current_phase == FocusPhase.LONG_BREAK)

        presets = _PHASE_PRESETS[self._current_phase]
        active_mins = self._durations[self._current_phase]
        for idx, btn in enumerate(self._preset_buttons):
            if idx < len(presets):
                mins = presets[idx]
                btn.setVisible(True)
                btn.setText(f"{mins}m")
                btn.setProperty("minutes", mins)
                btn.setChecked(mins == active_mins)
            else:
                btn.setVisible(False)

    def _on_preset_clicked(self, btn: QPushButton) -> None:
        self.stop_alarm()
        mins = int(btn.property("minutes") or 25)
        self._durations[self._current_phase] = mins
        key_map = {
            FocusPhase.FOCUS: "focus_minutes",
            FocusPhase.SHORT_BREAK: "short_break_minutes",
            FocusPhase.LONG_BREAK: "long_break_minutes",
        }
        self.settings.set(key_map[self._current_phase], mins)
        self._sync_phase_ui()

        if self._timer_state == "idle":
            self._phase_total_seconds = mins * 60
            self._remaining_seconds = float(self._phase_total_seconds)
            self._sync_controls()
            self._refresh_dial()

    def toggle_start_pause(self) -> None:
        self.stop_alarm()
        if self._timer_state == "running":
            self.pause_timer()
        else:
            self.start_timer()

    def start_timer(self) -> None:
        self.stop_alarm()
        if self._timer_state == "running":
            return
        if self._remaining_seconds <= 0.0:
            self._phase_total_seconds = self._durations[self._current_phase] * 60
            self._remaining_seconds = float(self._phase_total_seconds)

        if self._session_started_at is None:
            self._session_started_at = datetime.now()

        self._last_tick_mono = time.monotonic()
        self._timer_state = "running"
        self._tick_timer.start()
        self._sync_controls()
        self._refresh_dial()
        self.set_status_message(f"{self._current_phase.display_name} started", timeout_ms=2000)

    def pause_timer(self) -> None:
        if self._timer_state != "running":
            return
        self._consume_elapsed_monotonic()
        self._tick_timer.stop()
        self._last_tick_mono = None
        self._timer_state = "paused"
        self._sync_controls()
        self._refresh_dial()
        self.set_status_message("Timer paused", timeout_ms=2000)

    def reset_timer(self) -> None:
        self.stop_alarm()
        self._tick_timer.stop()
        self._phase_total_seconds = self._durations[self._current_phase] * 60
        self._remaining_seconds = float(self._phase_total_seconds)
        self._last_tick_mono = None
        self._session_started_at = None
        self._timer_state = "idle"
        self._sync_controls()
        self._refresh_dial()
        self.set_status_message("Timer reset", timeout_ms=1800)

    def skip_phase(self) -> None:
        self.stop_alarm()
        next_phase = self._compute_next_phase(increment_focus_cycle=False)
        self.select_phase(next_phase)
        self.set_status_message(f"Switched to {next_phase.display_name}", timeout_ms=2000)

    def _sync_controls(self) -> None:
        ringing = self.audio.is_ringing
        self._stop_alarm_btn.setVisible(ringing)
        self._primary_btn.setVisible(not ringing)
        self._reset_btn.setVisible(not ringing)
        self._skip_btn.setVisible(not ringing)

        if self._timer_state == "running":
            self._primary_btn.setText("Pause")
        elif self._timer_state == "paused":
            self._primary_btn.setText("Resume")
        else:
            self._primary_btn.setText("Start")

        self._reset_btn.setEnabled(
            self._timer_state != "idle"
            or self._remaining_seconds < float(self._phase_total_seconds)
        )

    def _consume_elapsed_monotonic(self) -> None:
        now_mono = time.monotonic()
        if self._last_tick_mono is not None:
            delta = max(0.0, now_mono - self._last_tick_mono)
            self._remaining_seconds = max(0.0, self._remaining_seconds - delta)
        self._last_tick_mono = now_mono

    def _on_timer_tick(self) -> None:
        if self._timer_state != "running":
            return
        self._consume_elapsed_monotonic()
        self._refresh_dial()
        if self._remaining_seconds <= 0.0:
            self._complete_current_phase()

    def _compute_next_phase(self, increment_focus_cycle: bool) -> FocusPhase:
        if self._current_phase == FocusPhase.FOCUS:
            if increment_focus_cycle:
                self._completed_focus_in_cycle += 1
            if (
                self._completed_focus_in_cycle > 0
                and self._completed_focus_in_cycle % self._long_break_interval == 0
            ):
                return FocusPhase.LONG_BREAK
            return FocusPhase.SHORT_BREAK
        return FocusPhase.FOCUS

    def _complete_current_phase(self) -> None:
        self._tick_timer.stop()
        self._timer_state = "idle"
        self._last_tick_mono = None

        finished_phase = self._current_phase
        planned_mins = self._durations[finished_phase]

        self.repo.record_session(
            task_label=self._task_input.text().strip(),
            phase=finished_phase,
            planned_minutes=planned_mins,
            actual_seconds=self._phase_total_seconds,
            completed=True,
            started_at=self._session_started_at,
            completed_at=datetime.now(),
        )
        self._session_started_at = None

        next_phase = self._compute_next_phase(
            increment_focus_cycle=(finished_phase == FocusPhase.FOCUS)
        )

        # Advance dial to next phase, then start continuous alarm if sound is enabled
        self._current_phase = next_phase
        self._phase_total_seconds = self._durations[next_phase] * 60
        self._remaining_seconds = float(self._phase_total_seconds)
        self._sync_phase_ui()

        if self._selected_sound != SOUND_SILENT:
            self.audio.start_continuous_alarm(self._selected_sound)

        self._sync_controls()
        self._refresh_dial()
        self._refresh_today_summary()

        self.notify(
            title=f"{finished_phase.display_name} Complete",
            message=f"{planned_mins} min finished. Click 'Stop Alarm' when ready for {next_phase.display_name}.",
            level=NotificationLevel.SUCCESS,
            desktop_banner=True,
            play_sound=False,
        )

        if not self.isVisible():
            self._restore_from_tray()

    def _refresh_dial(self) -> None:
        ratio = (
            self._remaining_seconds / float(self._phase_total_seconds)
            if self._phase_total_seconds > 0
            else 0.0
        )
        cycle_num = (self._completed_focus_in_cycle % self._long_break_interval) + 1
        formatted = format_mm_ss(self._remaining_seconds)

        self._dial.set_state(
            phase=self._current_phase,
            progress_ratio=ratio,
            time_text=formatted,
            cycle_text=f"SESSION {cycle_num} OF {self._long_break_interval}",
            ringing=self.audio.is_ringing,
        )
        title_str = f"{formatted} — {self._current_phase.display_name} | {self.meta.display_name}"
        self.setWindowTitle(title_str)
        if self.notifier._tray_icon is not None:
            self.notifier._tray_icon.setToolTip(title_str)

    def _refresh_today_summary(self) -> None:
        stats = self.repo.get_daily_stats()
        sess_word = "session" if stats.completed_focus_sessions == 1 else "sessions"
        self._summary_lbl.setText(
            f"Today: {stats.completed_focus_sessions} {sess_word}  •  {stats.total_focus_minutes} min focused"
        )

    # ------------------------------------------------------------------
    # Window Close vs. Background Tray Execution
    # ------------------------------------------------------------------

    def quit_application(self) -> None:
        """Explicitly stop audio/timer and exit the application completely."""
        self._force_quit = True
        self.audio.stop()
        self._tick_timer.stop()
        self.close()

    def can_close(self) -> bool:
        """Keep running in the Windows system tray if a focus/break timer is actively counting down."""
        if not self._force_quit and self._timer_state == "running" and self.notifier.tray_supported:
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.notify(
                    title="SimpleFocus Running in Background",
                    message="Your timer is still running. Click the JSF tray icon anytime to reopen.",
                    level=NotificationLevel.INFO,
                    desktop_banner=True,
                )
            return False

        self.audio.stop()
        self._tick_timer.stop()
        return True

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        if hasattr(self, "_dial"):
            self._dial.set_tokens(self.theme_manager.tokens)


def main() -> int:
    """Application entry point for SimpleFocus.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleFocusWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())