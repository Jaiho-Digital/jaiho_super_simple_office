"""Automated unit and GUI tests for SimpleCalendar."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QTime
from pytestqt.qtbot import QtBot

from apps.simple_calendar.main import SimpleCalendarWindow
from apps.simple_calendar.repository import CalendarRepository
from core.database.db_manager import DatabaseManager


def test_calendar_repository_crud_sorting_and_ics_roundtrip(tmp_path: Path) -> None:
    """Verify event CRUD, chronological ordering, month counts, and .ics export/import."""
    db = DatabaseManager(app_id="simple_calendar", db_path=tmp_path / "calendar_test.db")
    repo = CalendarRepository(db=db)

    target_day = date(2026, 11, 24)
    ev_afternoon = repo.create_event(
        title="Design Review",
        event_date=target_day,
        start_time="14:30",
        end_time="15:30",
        location="Studio B",
        notes="Review Soft Modern Pastel tokens.",
    )
    ev_all_day = repo.create_event(
        title="Release Freeze",
        event_date=target_day,
    )
    ev_morning = repo.create_event(
        title="Morning Standup",
        event_date=target_day,
        start_time="09:00",
        end_time="09:30",
    )

    # All-day events sort first, followed by chronological start times
    day_events = repo.list_events_for_date(target_day)
    assert [e.event_id for e in day_events] == [
        ev_all_day.event_id,
        ev_morning.event_id,
        ev_afternoon.event_id,
    ]
    assert day_events[0].time_span_label == "All Day"
    assert day_events[2].time_span_label == "14:30 – 15:30"

    assert repo.get_month_event_counts(2026, 11) == {"2026-11-24": 3}
    assert len(repo.search_events("Pastel")) == 1

    # Export to .ics and re-import into a fresh database
    ics_path = tmp_path / "schedule.ics"
    repo.export_ics_file(ics_path)
    assert ics_path.exists()

    db2 = DatabaseManager(app_id="simple_calendar", db_path=tmp_path / "calendar_import.db")
    repo2 = CalendarRepository(db=db2)
    imported_count = repo2.import_ics_file(ics_path)
    assert imported_count == 3

    imported_day = repo2.list_events_for_date(target_day)
    assert [e.title for e in imported_day] == [
        "Release Freeze",
        "Morning Standup",
        "Design Review",
    ]
    assert imported_day[2].location == "Studio B"

    # Update & delete
    updated = repo.update_event(ev_morning.event_id, title="Team Sync")
    assert updated.title == "Team Sync"
    assert repo.delete_event(ev_all_day.event_id) is True
    assert repo.get_event(ev_all_day.event_id) is None


def test_simple_calendar_window_events_and_persistence(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify GUI all-day & timed event creation, deletion, and SQLite persistence."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleCalendarWindow()
    qtbot.addWidget(window)
    window.show()

    assert window._events_list.count() == 0
    assert window._empty_day_lbl.isVisible()

    # Select a specific date and add an all-day event
    chosen_qdate = QDate(2026, 11, 25)
    window._calendar.setSelectedDate(chosen_qdate)
    window._event_title_input.setText("Architecture Milestone")
    window._all_day_cb.setChecked(True)
    window._add_event()

    # Add a timed event on the same date
    window._event_title_input.setText("Client Demo")
    window._all_day_cb.setChecked(False)
    window._quick_start_edit.setTime(QTime(11, 0))
    window._quick_end_edit.setTime(QTime(12, 0))
    window._add_event()

    assert window._events_list.count() == 2
    first_item = window._events_list.item(0)
    second_item = window._events_list.item(1)
    assert first_item is not None and "All Day" in first_item.text()
    assert second_item is not None and "11:00 – 12:00" in second_item.text()

    window.close()

    # Reopen and verify persistence
    reopened = SimpleCalendarWindow()
    qtbot.addWidget(reopened)
    reopened._calendar.setSelectedDate(chosen_qdate)
    assert reopened._events_list.count() == 2
    reopened.close()