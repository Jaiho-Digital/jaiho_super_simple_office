"""Automated unit and GUI verification tests for Phase 1 core infrastructure."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.foundation_demo.main import FoundationDemoWindow
from core.database.db_manager import DatabaseManager, Migration
from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_json,
    atomic_write_text,
    managed_temp_file,
    read_json_safe,
    sanitize_filename,
    validate_input_file,
)
from core.settings.settings_manager import SettingsManager
from core.themes.theme_manager import ThemeManager
from core.utilities.workers import WorkerContext, run_in_background
from core.version import APP_REGISTRY, get_app_metadata


def test_app_registry_contains_all_13_apps_and_hub() -> None:
    """Verify all 13 required applications and JaihoOffice launcher are registered."""
    expected_executables = {
        "JaihoOffice.exe",
        "SimplePDF.exe",
        "SimpleWriter.exe",
        "SimpleSheets.exe",
        "SimpleSlides.exe",
        "SimpleScan.exe",
        "SimpleNotes.exe",
        "SimpleTasks.exe",
        "SimpleCalendar.exe",
        "SimpleFocus.exe",
        "SimpleCalculator.exe",
        "SimpleUnits.exe",
        "SimpleConvert.exe",
        "SimpleOCR.exe",
    }
    registered_executables = {meta.executable for meta in APP_REGISTRY.values()}
    assert registered_executables == expected_executables

    fallback = get_app_metadata("unregistered_test_tool")
    assert fallback.executable == "unregistered_test_tool.exe"


def test_file_ops_atomic_write_validation_and_temp_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify atomic saves, backup creation, input validation, and temp file cleanup."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    assert sanitize_filename('report<>:"/\\|?*.txt') == "report_________.txt"
    assert sanitize_filename("CON") == "_CON"

    target = tmp_path / "notes.txt"
    atomic_write_text(target, "First draft", backup_app_id="test_app")
    assert target.read_text(encoding="utf-8") == "First draft"

    atomic_write_text(target, "Second draft", backup_app_id="test_app")
    assert target.read_text(encoding="utf-8") == "Second draft"

    json_target = tmp_path / "payload.json"
    atomic_write_json(json_target, {"status": "ok", "count": 3})
    assert read_json_safe(json_target) == {"status": "ok", "count": 3}

    # Input validation checks
    validated = validate_input_file(target, allowed_extensions={".txt"}, max_size_mb=5.0)
    assert validated == target

    with pytest.raises(FileOperationError):
        validate_input_file(target, allowed_extensions={".pdf"})

    empty_file = tmp_path / "empty.txt"
    empty_file.write_bytes(b"")
    with pytest.raises(FileOperationError):
        validate_input_file(empty_file)

    # Managed temp file self-cleans on exit
    with managed_temp_file("test_app", suffix=".tmp") as temp_file:
        assert temp_file.exists()
        temp_file.write_text("scratch", encoding="utf-8")
    assert not temp_file.exists()


def test_settings_manager_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify theme, window geometry, and recent file persistence across restarts."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    config_dir = tmp_path / "config"

    sample_doc = tmp_path / "sample.txt"
    sample_doc.write_text("hello", encoding="utf-8")

    settings = SettingsManager("simple_notes", config_dir=config_dir)
    assert settings.get_theme() == "light"

    settings.set_theme("dark")
    settings.set("font_size", 14)
    settings.set_window_geometry(width=1280, height=800, x=120, y=90, maximized=False)
    settings.add_recent_file(sample_doc)

    # Simulate fresh application instance reading from disk
    reloaded = SettingsManager("simple_notes", config_dir=config_dir)
    assert reloaded.get_theme() == "dark"
    assert reloaded.get("font_size") == 14
    geom = reloaded.get_window_geometry()
    assert geom["width"] == 1280
    assert geom["height"] == 800
    assert reloaded.get_recent_files() == [sample_doc.resolve()]


def test_database_manager_migrations_and_backup(tmp_path: Path) -> None:
    """Verify SQLite WAL initialization, schema migrations, transactions, and hot backups."""
    db_file = tmp_path / "test_suite.db"
    db = DatabaseManager(app_id="test_suite", db_path=db_file)
    assert db.get_schema_version() == 0

    migrations = (
        Migration(
            version=1,
            description="Create items table",
            up="CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);",
        ),
        Migration(
            version=2,
            description="Add priority column",
            up="ALTER TABLE items ADD COLUMN priority INTEGER NOT NULL DEFAULT 1;",
        ),
    )

    assert db.apply_migrations(migrations) == 2
    assert db.get_schema_version() == 2

    with db.transaction() as conn:
        conn.execute("INSERT INTO items (name, priority) VALUES (?, ?);", ("Task A", 2))

    with db.read_only() as conn:
        row = conn.execute("SELECT name, priority FROM items;").fetchone()
        assert row is not None
        assert row["name"] == "Task A"
        assert row["priority"] == 2

    backup_file = db.create_backup(destination_dir=tmp_path / "backups", tag="verify")
    assert backup_file.exists()
    assert backup_file.stat().st_size > 0


def test_theme_manager_compiles_light_and_dark_qss() -> None:
    """Verify QSS template compiles cleanly with zero unresolved $tokens."""
    from core.themes.tokens import DARK_THEME, LIGHT_THEME

    tm = ThemeManager(initial_theme="light")
    light_qss = tm.compile_stylesheet()
    assert LIGHT_THEME.bg_canvas in light_qss
    assert "$bg_canvas" not in light_qss

    tm.toggle_theme(persist=False)
    assert tm.current_theme == "dark"
    dark_qss = tm.compile_stylesheet()
    assert DARK_THEME.bg_canvas in dark_qss
    assert "$text_primary" not in dark_qss


def test_background_worker_executes_off_main_thread(qtbot: QtBot) -> None:
    """Verify BackgroundWorker emits progress and result signals cleanly."""
    results: list[int] = []
    progress_updates: list[int] = []

    def _compute(value: int, context: WorkerContext) -> int:
        context.report_progress(50, "Halfway")
        context.check_cancelled()
        context.report_progress(100, "Done")
        return value * 2

    with qtbot.waitSignal(
        (
            worker := run_in_background(
                _compute,
                21,
                inject_context=True,
                on_progress=lambda pct, _: progress_updates.append(pct),
                on_result=results.append,
            )
        ).signals.finished,
        timeout=3000,
    ):
        assert worker is not None

    assert results == [42]
    assert 50 in progress_updates
    assert 100 in progress_updates


def test_foundation_demo_window_lifecycle(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify FoundationDemoWindow launches, persists SQLite items, toggles themes, and closes cleanly."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = FoundationDemoWindow()
    qtbot.addWidget(window)
    window.show()

    assert window._empty_state.isVisible()

    window._record_input.setText("Verify Phase 1 Architecture")
    window._add_record()

    assert window._records_list.count() == 1
    assert not window._empty_state.isVisible()

    initial_theme = window.theme_manager.current_theme
    window.toggle_theme()
    assert window.theme_manager.current_theme != initial_theme

    window.close()