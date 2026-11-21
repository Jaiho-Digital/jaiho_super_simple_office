"""Automated unit and GUI tests for SimpleNotes."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.simple_notes.main import SimpleNotesWindow
from apps.simple_notes.repository import NotesRepository, normalize_tags
from core.database.db_manager import DatabaseManager


def test_normalize_tags_deduplicates_and_cleans_prefixes() -> None:
    """Verify tag parsing strips '#', commas, whitespace, and duplicate entries."""
    assert normalize_tags("#Design, work, DESIGN,  #ideas ") == ("design", "work", "ideas")
    assert normalize_tags(["#Python", "python", "Qt6"]) == ("python", "qt6")
    assert normalize_tags("") == ()


def test_notes_repository_crud_fts5_search_and_collections(tmp_path: Path) -> None:
    """Verify SQLite WAL + FTS5 search, tag filtering, pinning, archiving, and deletion."""
    db = DatabaseManager(app_id="simple_notes", db_path=tmp_path / "notes_test.db")
    repo = NotesRepository(db=db)

    n1 = repo.create_note(
        title="Architecture Blueprint",
        content="Local-first SQLite WAL persistence with zero cloud dependencies.",
        tags=("architecture", "sqlite"),
        is_pinned=True,
    )
    n2 = repo.create_note(
        title="Grocery Checklist",
        content="Apples, oats, coffee beans, and archival notebooks.",
        tags=("personal",),
        is_pinned=False,
    )

    counts = repo.get_counts()
    assert counts == {"total": 2, "active": 2, "pinned": 1, "archived": 0}

    # Pinned notes sort first
    active_notes = repo.list_notes()
    assert [n.note_id for n in active_notes] == [n1.note_id, n2.note_id]

    # FTS5 prefix search across body & title
    fts_matches = repo.list_notes(search_query="persist")
    assert [n.note_id for n in fts_matches] == [n1.note_id]

    # Tag filtering
    tagged = repo.list_notes(tag_filter="#personal")
    assert [n.note_id for n in tagged] == [n2.note_id]
    assert dict(repo.list_all_tags()) == {"architecture": 1, "personal": 1, "sqlite": 1}

    # Archiving unpins automatically and hides from active list
    archived = repo.toggle_archive(n1.note_id)
    assert archived.is_archived is True
    assert archived.is_pinned is False
    assert [n.note_id for n in repo.list_notes()] == [n2.note_id]
    assert [n.note_id for n in repo.list_notes(only_archived=True)] == [n1.note_id]

    # Permanent deletion
    assert repo.delete_note(n2.note_id) is True
    assert repo.get_note(n2.note_id) is None


def test_simple_notes_window_editing_views_and_persistence(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify GUI note creation, live Markdown preview, auto-save flush, and restart reload."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleNotesWindow()
    qtbot.addWidget(window)
    window.show()

    # Welcome note is seeded on first launch
    assert window._notes_list.count() == 1

    # Create a new note and edit title, tags, and Markdown body
    window._create_new_note()
    assert window._notes_list.count() == 2

    window._title_input.setText("Phase 2 Design Spec")
    window._tags_input.setText("design, paperism")
    window._editor.setPlainText("# Overview\n\nCrafted with **Archival Paper** tokens.")
    window._flush_save()

    assert window._save_badge.text() == "Saved"
    assert "Overview" in window._preview_browser.toPlainText()

    # Cycle Write / Split / Preview modes
    window._set_view_mode("write")
    assert window._editor.isVisible()
    assert not window._preview_browser.isVisible()

    window._set_view_mode("preview")
    assert not window._editor.isVisible()
    assert window._preview_browser.isVisible()

    window._set_view_mode("split")
    assert window._editor.isVisible()
    assert window._preview_browser.isVisible()

    window.close()

    # Reopen and confirm full SQLite persistence
    reopened = SimpleNotesWindow()
    qtbot.addWidget(reopened)
    assert reopened._notes_list.count() == 2
    saved_notes = reopened.repo.list_notes(search_query="Paperism")
    assert len(saved_notes) == 1
    assert saved_notes[0].title == "Phase 2 Design Spec"
    assert saved_notes[0].tags == ("design", "paperism")
    reopened.close()