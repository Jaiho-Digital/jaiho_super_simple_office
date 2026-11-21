"""Core foundation tests for database, file ops, settings, pastel themes, and BaseWindow."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from core.database.db_manager import DatabaseManager, Migration
from core.file_manager.file_ops import (
    FileOperationError,
    atomic_write_json,
    atomic_write_text,
    sanitize_filename,
    validate_input_file,
)
from core.notifications.notifier import NotificationLevel
from core.settings.settings_manager import SettingsManager
from core.themes.theme_manager import ThemeManager
from core.themes.tokens import (
    BUILT_IN_THEMES,
    LAVENDER_DREAM,
    MIDNIGHT_ORCHID,
    resolve_theme_id,
)
from core.ui.base_window import BaseWindow
from core.ui.icons import get_lucide_icon, render_lucide_pixmap
from core.ui.widgets import (
    CardFrame,
    EmptyStateWidget,
    SearchInput,
    SectionHeader,
    StatusBadge,
    make_button,
)


def test_database_manager_wal_migrations_and_transactions(tmp_path: Path) -> None:
    """Verify SQLite WAL initialization, schema migrations, and atomic transactions."""
    db_path = tmp_path / "foundation_test.db"
    db = DatabaseManager(app_id="foundation_demo", db_path=db_path)

    def _up_v1(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);")

    db.apply_migrations((Migration(version=1, description="Create items", up=_up_v1),))

    with db.transaction() as conn:
        conn.execute("INSERT INTO items (name) VALUES (?);", ("Archival Paper",))

    with db.read_only() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert str(mode).lower() == "wal"
        row = conn.execute("SELECT name FROM items WHERE id = 1;").fetchone()
        assert row is not None
        assert row["name"] == "Archival Paper"


def test_atomic_file_operations_and_validation(tmp_path: Path) -> None:
    """Verify atomic text/JSON writes, filename sanitization, and input validation."""
    assert sanitize_filename("My:Draft*Report?.md") == "My_Draft_Report_.md"

    txt_file = tmp_path / "notes.md"
    atomic_write_text(txt_file, "# Hello Pastel World\n")
    assert txt_file.read_text(encoding="utf-8") == "# Hello Pastel World\n"

    json_file = tmp_path / "config.json"
    atomic_write_json(json_file, {"suite": "Jaiho Super Simple Office", "count": 14})
    loaded = json.loads(json_file.read_text(encoding="utf-8"))
    assert loaded["count"] == 14

    validated = validate_input_file(txt_file, allowed_extensions={".md"}, max_size_mb=5.0)
    assert validated == txt_file.resolve()

    with pytest.raises(FileOperationError):
        validate_input_file(txt_file, allowed_extensions={".pdf"})


def test_settings_manager_four_pastel_themes_and_aliases(tmp_path: Path) -> None:
    """Verify SettingsManager persists all 4 built-in themes and resolves legacy aliases."""
    settings = SettingsManager(
        app_id="simple_calculator",
        settings_path=tmp_path / "settings.json",
    )

    # Default theme is Lavender Dream
    assert settings.get_theme() == "lavender_dream"

    # Legacy aliases map cleanly to canonical pastel themes
    settings.set_theme("dark")
    assert settings.get_theme() == "midnight_orchid"

    settings.set_theme("light")
    assert settings.get_theme() == "lavender_dream"

    # All 4 built-in theme IDs persist cleanly
    for theme_id in ("lavender_dream", "sage_garden", "peach_blossom", "midnight_orchid"):
        settings.set_theme(theme_id)
        reloaded = SettingsManager(
            app_id="simple_calculator",
            settings_path=tmp_path / "settings.json",
        )
        assert reloaded.get_theme() == theme_id

    with pytest.raises(ValueError, match="Unsupported theme"):
        settings.set_theme("neon_cyberpunk")


def test_theme_manager_qss_compilation_and_custom_accent(tmp_path: Path) -> None:
    """Verify QSS template substitution leaves zero unresolved tokens and custom accents work."""
    assert resolve_theme_id("Sage Garden") == "sage_garden"
    assert len(BUILT_IN_THEMES) == 4

    settings = SettingsManager(
        app_id="simple_calculator",
        settings_path=tmp_path / "theme_settings.json",
    )
    manager = ThemeManager(settings=settings)
    assert manager.tokens == LAVENDER_DREAM

    for theme_id in BUILT_IN_THEMES:
        qss = manager.compile_stylesheet(theme_name=theme_id)
        assert "$" not in qss
        assert "QMainWindow" in qss

    # Custom accent derivation updates primary accent and persists
    tinted = manager.set_custom_accent("#3B82F6", persist=True)
    assert tinted.accent_primary == "#3B82F6"
    assert manager.custom_accent == "#3B82F6"

    # Resetting custom accent restores theme default
    restored = manager.set_custom_accent(None, persist=True)
    assert restored.accent_primary == LAVENDER_DREAM.accent_primary

    # Toggling switches between Lavender Dream and Midnight Orchid
    dark_tokens = manager.toggle_theme(persist=True)
    assert dark_tokens == MIDNIGHT_ORCHID
    assert manager.current_theme == "dark"


def test_lucide_vector_icons_render_cleanly() -> None:
    """Verify Lucide-style SVG icon renderer produces non-null High-DPI pixmaps and icons."""
    pixmap = render_lucide_pixmap("palette", color="#6F59C9", size=16)
    assert not pixmap.isNull()
    assert pixmap.width() == 32  # 2x High-DPI rasterization

    icon = get_lucide_icon("copy", color="#35343B", size=16)
    assert not icon.isNull()


def test_base_window_pastel_header_widgets_and_toasts(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify BaseWindow integrated header bar, theme switching, widgets, and toast stack."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = BaseWindow(app_id="simple_calculator", min_width=640, min_height=480)
    qtbot.addWidget(window)

    host = QWidget(window)
    layout = QVBoxLayout(host)

    card = CardFrame(parent=host)
    badge = StatusBadge("Lavender", variant="lavender", parent=card)
    header = SectionHeader("Section Title", subtitle="Subtitle", accessory=badge, parent=card)
    search = SearchInput(placeholder="Filter...", debounce_ms=0, parent=card)
    empty = EmptyStateWidget(
        title="All Clear",
        description="Nothing pending right now.",
        action_text="Create",
        parent=card,
    )
    btn = make_button("Action", variant="pastel", icon_name="sparkles", parent=card)

    card.body_layout.addWidget(header)
    card.body_layout.addWidget(search)
    card.body_layout.addWidget(empty)
    card.body_layout.addWidget(btn)
    card.body_layout.addWidget(QLabel("Body text", card))
    layout.addWidget(card)
    window.setCentralWidget(host)
    window.show()

    assert window._theme_btn.text() == "Lavender Dream"

    # Switch through all 4 themes via BaseWindow.select_theme
    window.select_theme("sage_garden")
    assert window._theme_btn.text() == "Sage Garden"

    window.select_theme("peach_blossom")
    assert window._theme_btn.text() == "Peach Blossom"

    window.select_theme("midnight_orchid")
    assert window._theme_btn.text() == "Midnight Orchid"

    # Transient header status pill & in-app toast
    window.set_status_message("Saved changes", timeout_ms=1500)
    assert window._transient_status_lbl.isVisible()

    window.notify("Test Toast", "Pastel toast notification works.", level=NotificationLevel.SUCCESS)
    assert any(
        "Pastel toast notification works." in lbl.text() and lbl.isVisible()
        for lbl in window.findChildren(QLabel)
    )

    window.close()