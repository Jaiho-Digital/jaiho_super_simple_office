"""SimpleCalendar — Clean offline monthly calendar & .ics agenda for Jaiho Super Simple Office."""

from __future__ import annotations

import calendar
import logging
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QRect, QRectF, Qt, QTime
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QCalendarWidget,
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSizePolicy,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from apps.simple_calendar.repository import (
    APP_ID,
    CalendarEventRecord,
    CalendarRepository,
)
from core.database.db_manager import DatabaseError
from core.file_manager.file_ops import FileOperationError
from core.notifications.notifier import NotificationLevel
from core.themes.tokens import LIGHT_THEME, ThemeTokens
from core.ui.base_window import BaseWindow, bootstrap_application
from core.ui.dialogs import show_error_dialog
from core.ui.widgets import CardFrame, HorizontalDivider, make_button

logger = logging.getLogger(__name__)


class PaperCalendarWidget(QCalendarWidget):
    """Custom-painted QCalendarWidget matching Archival Paper & Obsidian Ink tokens."""

    def __init__(self, tokens: ThemeTokens = LIGHT_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._event_counts: dict[str, int] = {}

        self.setNavigationBarVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.setGridVisible(False)
        self.setSelectionMode(QCalendarWidget.SelectionMode.SingleSelection)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_tokens(self, tokens: ThemeTokens) -> None:
        """Update palette tokens and repaint all visible cells."""
        self._tokens = tokens
        self.updateCells()

    def set_event_counts(self, counts: dict[str, int]) -> None:
        """Store ISO-date -> event-count mapping for rendering indicator dots."""
        self._event_counts = dict(counts)
        self.updateCells()

    def paintCell(self, painter: QPainter, rect: QRect, cell_date: QDate) -> None:  # noqa: N802
        """Render clean editorial day cells with selection pill, today ring, and event dots."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        iso_str = cell_date.toString("yyyy-MM-dd")
        is_selected = cell_date == self.selectedDate()
        is_today = cell_date == QDate.currentDate()
        is_current_month = cell_date.month() == self.monthShown()
        event_count = self._event_counts.get(iso_str, 0)

        # Cell background
        painter.fillRect(rect, QColor(self._tokens.bg_surface))

        # Inset rounded pill for selected or today cell
        pill_rect = QRectF(rect).adjusted(4.0, 3.0, -4.0, -3.0)
        if is_selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._tokens.accent_primary))
            painter.drawRoundedRect(pill_rect, 7.0, 7.0)
        elif is_today:
            painter.setPen(QPen(QColor(self._tokens.accent_primary), 1.4))
            painter.setBrush(QColor(self._tokens.bg_selected))
            painter.drawRoundedRect(pill_rect, 7.0, 7.0)

        # Day numeral typography
        font = QFont(
            "Segoe UI Variable Text",
            10,
            QFont.Weight.DemiBold if (is_selected or is_today) else QFont.Weight.Normal,
        )
        painter.setFont(font)

        if is_selected:
            text_color = self._tokens.accent_text
        elif is_today:
            text_color = self._tokens.accent_primary
        elif is_current_month:
            text_color = self._tokens.text_primary
        else:
            text_color = self._tokens.text_muted

        painter.setPen(QColor(text_color))
        text_rect = QRectF(rect.x(), rect.y() + 2.0, rect.width(), rect.height() - 12.0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, str(cell_date.day()))

        # Event indicator dots (up to 3 dots below day number)
        if event_count > 0:
            dot_count = min(3, event_count)
            dot_radius = 2.2
            spacing = 6.5
            total_w = (dot_count - 1) * spacing
            start_x = rect.center().x() - (total_w / 2.0)
            dot_y = float(rect.bottom()) - 7.5
            dot_color = (
                self._tokens.accent_text
                if is_selected
                else (
                    self._tokens.accent_primary
                    if is_current_month
                    else self._tokens.text_muted
                )
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(dot_color))
            for idx in range(dot_count):
                cx = start_x + idx * spacing
                painter.drawEllipse(QRectF(cx - dot_radius, dot_y - dot_radius, dot_radius * 2, dot_radius * 2))

        painter.restore()


class EventEditDialog(QDialog):
    """Compact modal dialog for editing an existing calendar event's details."""

    def __init__(self, event: CalendarEventRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Event")
        self.setModal(True)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.title_edit = QLineEdit(event.title, self)
        form.addRow("Title:", self.title_edit)

        self.date_edit = QDateEdit(self)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        parsed_date = QDate.fromString(event.event_date, "yyyy-MM-dd")
        self.date_edit.setDate(parsed_date if parsed_date.isValid() else QDate.currentDate())
        form.addRow("Date:", self.date_edit)

        self.all_day_cb = QCheckBox("All-day event", self)
        self.all_day_cb.setChecked(event.is_all_day)
        self.all_day_cb.toggled.connect(self._sync_time_enabled)
        form.addRow("", self.all_day_cb)

        time_row = QHBoxLayout()
        time_row.setSpacing(8)
        self.start_time_edit = QTimeEdit(self)
        self.start_time_edit.setDisplayFormat("HH:mm")
        self.start_time_edit.setTime(
            QTime.fromString(event.start_time, "HH:mm") if event.start_time else QTime(9, 0)
        )
        time_row.addWidget(self.start_time_edit)

        to_lbl = QLabel("to", self)
        to_lbl.setProperty("role", "muted")
        time_row.addWidget(to_lbl)

        self.end_time_edit = QTimeEdit(self)
        self.end_time_edit.setDisplayFormat("HH:mm")
        self.end_time_edit.setTime(
            QTime.fromString(event.end_time, "HH:mm") if event.end_time else QTime(10, 0)
        )
        time_row.addWidget(self.end_time_edit)
        form.addRow("Time:", time_row)

        self.location_edit = QLineEdit(event.location, self)
        self.location_edit.setPlaceholderText("Optional room, address, or link...")
        form.addRow("Location:", self.location_edit)

        self.notes_edit = QPlainTextEdit(event.notes, self)
        self.notes_edit.setPlaceholderText("Optional notes...")
        self.notes_edit.setMaximumHeight(85)
        form.addRow("Notes:", self.notes_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_time_enabled(event.is_all_day)

    def _sync_time_enabled(self, is_all_day: bool) -> None:
        self.start_time_edit.setEnabled(not is_all_day)
        self.end_time_edit.setEnabled(not is_all_day)


class SimpleCalendarWindow(BaseWindow):
    """Two-card offline calendar & day agenda window for SimpleCalendar.exe."""

    def __init__(self, repository: CalendarRepository | None = None) -> None:
        super().__init__(app_id=APP_ID, min_width=760, min_height=520)
        self.repo = repository or CalendarRepository()

        if self.settings.get("window_width") is None:
            self.resize(840, 560)

        self._selected_qdate: QDate = QDate.currentDate()

        self._build_workspace()
        self._bind_shortcuts()
        self._refresh_calendar_and_agenda()

    def _build_workspace(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(16)

        # ==============================================================
        # Left Card: Monthly Calendar Grid
        # ==============================================================
        cal_card = CardFrame(padding=16, spacing=12, parent=central)

        # Swiss-editorial Month Navigation Header
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)

        self._month_title_lbl = QLabel("", cal_card)
        self._month_title_lbl.setProperty("role", "title")
        nav_row.addWidget(self._month_title_lbl, 1)

        prev_btn = make_button(
            text="‹",
            variant="ghost",
            tooltip="Previous month",
            on_click=self._go_previous_month,
            parent=cal_card,
        )
        prev_btn.setFixedWidth(32)
        nav_row.addWidget(prev_btn)

        today_btn = make_button(
            text="Today",
            variant="ghost",
            tooltip="Jump to today (Ctrl+T)",
            on_click=self.jump_to_today,
            parent=cal_card,
        )
        nav_row.addWidget(today_btn)

        next_btn = make_button(
            text="›",
            variant="ghost",
            tooltip="Next month",
            on_click=self._go_next_month,
            parent=cal_card,
        )
        next_btn.setFixedWidth(32)
        nav_row.addWidget(next_btn)

        cal_card.body_layout.addLayout(nav_row)

        # Custom-painted Monthly Calendar
        self._calendar = PaperCalendarWidget(tokens=self.theme_manager.tokens, parent=cal_card)
        self._calendar.setSelectedDate(self._selected_qdate)
        self._calendar.selectionChanged.connect(self._on_calendar_selection_changed)
        self._calendar.currentPageChanged.connect(self._on_calendar_page_changed)
        cal_card.body_layout.addWidget(self._calendar, 1)

        cal_card.body_layout.addWidget(HorizontalDivider(cal_card))

        # Quiet Footer: Month summary + .ics Import/Export
        cal_footer = QHBoxLayout()
        cal_footer.setSpacing(6)

        self._month_summary_lbl = QLabel("0 events this month", cal_card)
        self._month_summary_lbl.setProperty("role", "caption")
        cal_footer.addWidget(self._month_summary_lbl)

        cal_footer.addStretch(1)

        import_btn = make_button(
            text="Import .ics",
            variant="ghost",
            tooltip="Import events from a standard .ics calendar file",
            on_click=self._import_ics,
            parent=cal_card,
        )
        import_btn.setStyleSheet("padding: 3px 8px; font-size: 12px;")
        cal_footer.addWidget(import_btn)

        export_btn = make_button(
            text="Export .ics",
            variant="ghost",
            tooltip="Export all calendar events to a standard .ics file",
            on_click=self._export_ics,
            parent=cal_card,
        )
        export_btn.setStyleSheet("padding: 3px 8px; font-size: 12px;")
        cal_footer.addWidget(export_btn)

        cal_card.body_layout.addLayout(cal_footer)
        root.addWidget(cal_card, 11)

        # ==============================================================
        # Right Card: Selected Day Agenda
        # ==============================================================
        agenda_card = CardFrame(padding=16, spacing=12, parent=central)

        day_header = QHBoxLayout()
        day_header.setSpacing(8)

        self._day_title_lbl = QLabel("", agenda_card)
        self._day_title_lbl.setProperty("role", "section-header")
        day_header.addWidget(self._day_title_lbl, 1)

        self._day_count_lbl = QLabel("0 events", agenda_card)
        self._day_count_lbl.setProperty("role", "caption")
        day_header.addWidget(self._day_count_lbl)

        agenda_card.body_layout.addLayout(day_header)

        # Quick-Add Event Well
        add_well = QFrame(agenda_card)
        add_well.setProperty("surface", "recessed")
        add_well.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        well_layout = QVBoxLayout(add_well)
        well_layout.setContentsMargins(10, 8, 10, 8)
        well_layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)

        self._event_title_input = QLineEdit(add_well)
        self._event_title_input.setPlaceholderText("Add event and press Enter...")
        self._event_title_input.returnPressed.connect(self._add_event)
        title_row.addWidget(self._event_title_input, 1)

        add_btn = make_button(
            text="Add",
            variant="primary",
            tooltip="Add event to selected date (Enter)",
            on_click=self._add_event,
            parent=add_well,
        )
        title_row.addWidget(add_btn)
        well_layout.addLayout(title_row)

        time_row = QHBoxLayout()
        time_row.setSpacing(6)

        self._all_day_cb = QCheckBox("All day", add_well)
        self._all_day_cb.setChecked(True)
        self._all_day_cb.toggled.connect(self._on_quick_all_day_toggled)
        time_row.addWidget(self._all_day_cb)

        self._quick_start_edit = QTimeEdit(add_well)
        self._quick_start_edit.setDisplayFormat("HH:mm")
        self._quick_start_edit.setTime(QTime(9, 0))
        self._quick_start_edit.setEnabled(False)
        time_row.addWidget(self._quick_start_edit)

        dash_lbl = QLabel("–", add_well)
        dash_lbl.setProperty("role", "muted")
        time_row.addWidget(dash_lbl)

        self._quick_end_edit = QTimeEdit(add_well)
        self._quick_end_edit.setDisplayFormat("HH:mm")
        self._quick_end_edit.setTime(QTime(10, 0))
        self._quick_end_edit.setEnabled(False)
        time_row.addWidget(self._quick_end_edit)

        time_row.addStretch(1)
        well_layout.addLayout(time_row)
        agenda_card.body_layout.addWidget(add_well)

        # Day Events List
        self._events_list = QListWidget(agenda_card)
        self._events_list.setAlternatingRowColors(True)
        self._events_list.setWordWrap(True)
        self._events_list.itemDoubleClicked.connect(self._edit_selected_event)
        self._events_list.currentItemChanged.connect(lambda *_: self._sync_agenda_buttons())
        agenda_card.body_layout.addWidget(self._events_list, 1)

        self._empty_day_lbl = QLabel(
            "No events scheduled for this day.\nType an event above and press Enter.",
            agenda_card,
        )
        self._empty_day_lbl.setProperty("role", "muted")
        self._empty_day_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_day_lbl.setWordWrap(True)
        agenda_card.body_layout.addWidget(self._empty_day_lbl, 1)

        agenda_card.body_layout.addWidget(HorizontalDivider(agenda_card))

        # Quiet Footer Actions
        agenda_footer = QHBoxLayout()
        agenda_footer.setSpacing(6)

        hint_lbl = QLabel("Double-click an event to edit details", agenda_card)
        hint_lbl.setProperty("role", "caption")
        agenda_footer.addWidget(hint_lbl)

        agenda_footer.addStretch(1)

        self._edit_btn = make_button(
            text="Edit",
            variant="ghost",
            tooltip="Edit selected event details",
            on_click=self._edit_selected_event,
            parent=agenda_card,
        )
        self._edit_btn.setStyleSheet("padding: 3px 8px; font-size: 12px;")
        agenda_footer.addWidget(self._edit_btn)

        self._delete_btn = make_button(
            text="Remove",
            variant="ghost",
            tooltip="Remove selected event (Delete)",
            on_click=self._delete_selected_event,
            parent=agenda_card,
        )
        self._delete_btn.setStyleSheet("padding: 3px 8px; font-size: 12px;")
        agenda_footer.addWidget(self._delete_btn)

        agenda_card.body_layout.addLayout(agenda_footer)
        root.addWidget(agenda_card, 10)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence.StandardKey.New, self).activated.connect(
            self._event_title_input.setFocus
        )
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self.jump_to_today)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(
            self._delete_selected_if_unfocused
        )

    def _delete_selected_if_unfocused(self) -> None:
        if not self._event_title_input.hasFocus():
            self._delete_selected_event()

    # ------------------------------------------------------------------
    # Navigation & Refresh
    # ------------------------------------------------------------------

    def _go_previous_month(self) -> None:
        self._calendar.showPreviousMonth()

    def _go_next_month(self) -> None:
        self._calendar.showNextMonth()

    def jump_to_today(self) -> None:
        """Select today's date and navigate the calendar grid to the current month."""
        today = QDate.currentDate()
        self._selected_qdate = today
        self._calendar.setSelectedDate(today)
        self._calendar.setCurrentPage(today.year(), today.month())
        self._refresh_calendar_and_agenda()

    def _on_calendar_selection_changed(self) -> None:
        self._selected_qdate = self._calendar.selectedDate()
        self._refresh_day_agenda()

    def _on_calendar_page_changed(self, _year: int, _month: int) -> None:
        self._refresh_month_grid()

    def _refresh_calendar_and_agenda(self) -> None:
        self._refresh_month_grid()
        self._refresh_day_agenda()

    def _refresh_month_grid(self) -> None:
        year = self._calendar.yearShown()
        month = self._calendar.monthShown()
        month_name = calendar.month_name[month]
        self._month_title_lbl.setText(f"{month_name} {year}")

        counts = self.repo.get_month_event_counts(year, month)
        self._calendar.set_event_counts(counts)

        total_in_month = sum(counts.values())
        ev_word = "event" if total_in_month == 1 else "events"
        self._month_summary_lbl.setText(f"{total_in_month} {ev_word} in {month_name}")

    def _refresh_day_agenda(self) -> None:
        py_date = date(
            self._selected_qdate.year(),
            self._selected_qdate.month(),
            self._selected_qdate.day(),
        )
        is_today = py_date == date.today()
        prefix = "Today  •  " if is_today else ""
        self._day_title_lbl.setText(f"{prefix}{py_date.strftime('%a, %b %d, %Y')}")

        events = self.repo.list_events_for_date(py_date)
        ev_word = "event" if len(events) == 1 else "events"
        self._day_count_lbl.setText(f"{len(events)} {ev_word}")

        self._events_list.clear()
        for ev in events:
            loc_suffix = f"  •  {ev.location}" if ev.location else ""
            label = f"{ev.time_span_label}  —  {ev.title}{loc_suffix}"
            if ev.notes:
                label += f"\n{ev.notes.splitlines()[0][:90]}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, ev.event_id)
            self._events_list.addItem(item)

        has_events = len(events) > 0
        self._events_list.setVisible(has_events)
        self._empty_day_lbl.setVisible(not has_events)
        self._sync_agenda_buttons()

    def _sync_agenda_buttons(self) -> None:
        has_sel = self._events_list.currentItem() is not None
        self._edit_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)

    # ------------------------------------------------------------------
    # Event Creation, Editing, Deletion & .ics
    # ------------------------------------------------------------------

    def _on_quick_all_day_toggled(self, checked: bool) -> None:
        self._quick_start_edit.setEnabled(not checked)
        self._quick_end_edit.setEnabled(not checked)

    def _add_event(self) -> None:
        title = self._event_title_input.text().strip()
        if not title:
            return

        py_date = date(
            self._selected_qdate.year(),
            self._selected_qdate.month(),
            self._selected_qdate.day(),
        )
        start_str: str | None = None
        end_str: str | None = None
        if not self._all_day_cb.isChecked():
            start_str = self._quick_start_edit.time().toString("HH:mm")
            end_str = self._quick_end_edit.time().toString("HH:mm")

        try:
            self.repo.create_event(
                title=title,
                event_date=py_date,
                start_time=start_str,
                end_time=end_str,
            )
        except DatabaseError as exc:
            show_error_dialog(self, "Could Not Add Event", str(exc))
            return

        self._event_title_input.clear()
        self._refresh_calendar_and_agenda()
        self.set_status_message("Event added", timeout_ms=1800)

    def _edit_selected_event(self, _item: QListWidgetItem | None = None) -> None:
        current = self._events_list.currentItem()
        if current is None:
            return
        event_id = int(current.data(Qt.ItemDataRole.UserRole))
        existing = self.repo.get_event(event_id)
        if existing is None:
            return

        dialog = EventEditDialog(existing, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_title = dialog.title_edit.text().strip()
        if not new_title:
            show_error_dialog(self, "Invalid Title", "Event title cannot be empty.")
            return

        qdate = dialog.date_edit.date()
        new_date = date(qdate.year(), qdate.month(), qdate.day())
        is_all_day = dialog.all_day_cb.isChecked()
        start_str = None if is_all_day else dialog.start_time_edit.time().toString("HH:mm")
        end_str = None if is_all_day else dialog.end_time_edit.time().toString("HH:mm")

        try:
            self.repo.update_event(
                event_id,
                title=new_title,
                event_date=new_date,
                start_time=start_str,
                end_time=end_str,
                location=dialog.location_edit.text(),
                notes=dialog.notes_edit.toPlainText(),
            )
        except DatabaseError as exc:
            show_error_dialog(self, "Could Not Update Event", str(exc))
            return

        self._selected_qdate = qdate
        self._calendar.setSelectedDate(qdate)
        self._refresh_calendar_and_agenda()
        self.set_status_message("Event updated", timeout_ms=2000)

    def _delete_selected_event(self) -> None:
        current = self._events_list.currentItem()
        if current is None:
            return
        event_id = int(current.data(Qt.ItemDataRole.UserRole))
        self.repo.delete_event(event_id)
        self._refresh_calendar_and_agenda()
        self.set_status_message("Event removed", timeout_ms=1800)

    def _import_ics(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import iCalendar File (.ics)",
            str(Path.home() / "Documents"),
            "iCalendar Files (*.ics)",
        )
        if not selected_path:
            return

        try:
            count = self.repo.import_ics_file(selected_path)
        except FileOperationError as exc:
            show_error_dialog(self, "Import Failed", str(exc))
            return

        self._refresh_calendar_and_agenda()
        self.notify(
            "Calendar Imported",
            f"Imported {count} event(s) from '{Path(selected_path).name}'.",
            level=NotificationLevel.SUCCESS,
        )

    def _export_ics(self) -> None:
        default_path = str(Path.home() / "Documents" / "simple_calendar.ics")
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Calendar as .ics",
            default_path,
            "iCalendar Files (*.ics)",
        )
        if not selected_path:
            return

        target = Path(selected_path)
        if target.suffix.lower() != ".ics":
            target = target.with_suffix(".ics")

        try:
            self.repo.export_ics_file(target)
        except FileOperationError as exc:
            show_error_dialog(self, "Export Failed", str(exc))
            return

        self.notify(
            "Calendar Exported",
            f"Saved '{target.name}' successfully.",
            level=NotificationLevel.SUCCESS,
        )

    def on_theme_updated(self, theme_name: str) -> None:
        _ = theme_name
        if hasattr(self, "_calendar"):
            self._calendar.set_tokens(self.theme_manager.tokens)


def main() -> int:
    """Application entry point for SimpleCalendar.exe."""
    return bootstrap_application(
        app_id=APP_ID,
        window_factory=SimpleCalendarWindow,
    )


if __name__ == "__main__":
    raise SystemExit(main())