"""Automated unit and GUI tests for SimpleTasks."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from apps.simple_tasks.main import SimpleTasksWindow
from apps.simple_tasks.repository import TaskPriority, TasksRepository
from core.database.db_manager import DatabaseManager


def test_tasks_repository_crud_smart_views_and_subtasks(tmp_path: Path) -> None:
    """Verify task creation, due-date smart views, priority filtering, and subtask cascade."""
    db = DatabaseManager(app_id="simple_tasks", db_path=tmp_path / "tasks_test.db")
    repo = TasksRepository(db=db)

    today = date(2026, 11, 21)
    yesterday = today - timedelta(days=1)
    next_week = today + timedelta(days=7)

    t_overdue = repo.create_task(
        title="Submit tax receipt",
        priority=TaskPriority.HIGH,
        due_date=yesterday,
    )
    t_today = repo.create_task(
        title="Build SimpleCalendar",
        priority=TaskPriority.NORMAL,
        due_date=today,
    )
    t_upcoming = repo.create_task(
        title="Prepare website release",
        priority=TaskPriority.LOW,
        due_date=next_week,
    )

    assert t_overdue.is_overdue(reference_date=today) is True
    assert t_today.is_due_today(reference_date=today) is True

    counts = repo.get_summary_counts(reference_date=today)
    assert counts == {"all": 3, "today": 2, "upcoming": 1, "high": 1, "completed": 0}

    # Smart view filtering
    assert [t.task_id for t in repo.list_tasks(smart_view="today", reference_date=today)] == [
        t_overdue.task_id,
        t_today.task_id,
    ]
    assert [t.task_id for t in repo.list_tasks(smart_view="upcoming", reference_date=today)] == [
        t_upcoming.task_id,
    ]
    assert [t.task_id for t in repo.list_tasks(smart_view="high", reference_date=today)] == [
        t_overdue.task_id,
    ]

    # Subtasks & cascade deletion
    sub1 = repo.add_subtask(t_today.task_id, "Write recurrence helper")
    repo.add_subtask(t_today.task_id, "Add .ics export")
    repo.toggle_subtask(sub1.subtask_id)

    hydrated = repo.get_task(t_today.task_id)
    assert hydrated is not None
    assert len(hydrated.subtasks) == 2
    assert hydrated.completed_subtask_count == 1

    # Complete and clear done tasks
    repo.toggle_task_completed(t_overdue.task_id)
    assert len(repo.list_tasks(smart_view="completed", reference_date=today)) == 1
    assert repo.clear_completed_tasks() == 1
    assert repo.get_task(t_overdue.task_id) is None

    # Deleting parent task removes subtasks cleanly
    assert repo.delete_task(t_today.task_id) is True
    assert repo.get_task(t_today.task_id) is None


def test_simple_tasks_window_workflow_and_persistence(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify single-card quick-add, Today/Star pills, check-off, and restart persistence."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleTasksWindow()
    qtbot.addWidget(window)
    window.show()

    assert window._tasks_list.count() == 0
    assert window._empty_lbl.isVisible()

    # Add a starred Today task
    window._task_input.setText("Ship SimpleTasks v1")
    window._today_pill.setChecked(True)
    window._star_pill.setChecked(True)
    window._add_task()

    assert window._tasks_list.count() == 1
    assert not window._today_pill.isChecked()
    assert not window._star_pill.isChecked()
    assert "All (1)" in window._tab_buttons["all"].text()
    assert "Today (1)" in window._tab_buttons["today"].text()
    assert "Important (1)" in window._tab_buttons["high"].text()

    # Add a normal backlog task
    window._task_input.setText("Refactor icon script")
    window._add_task()
    assert window._tasks_list.count() == 2

    # Filter to Important tab -> only starred task appears
    window._select_view("high")
    assert window._tasks_list.count() == 1
    item = window._tasks_list.item(0)
    assert item is not None
    assert "Ship SimpleTasks v1" in item.text()

    # Check the task off -> moves to Done tab
    item.setCheckState(Qt.CheckState.Checked)
    assert window._tasks_list.count() == 0

    window._select_view("completed")
    assert window._tasks_list.count() == 1

    window.close()

    # Reopen and confirm SQLite persistence
    reopened = SimpleTasksWindow()
    qtbot.addWidget(reopened)
    assert "All (1)" in reopened._tab_buttons["all"].text()
    assert "Done (1)" in reopened._tab_buttons["completed"].text()
    reopened.close()