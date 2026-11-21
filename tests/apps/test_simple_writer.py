"""Automated unit and GUI tests for SimpleWriter."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextDocument
from pytestqt.qtbot import QtBot

from apps.simple_writer.document_model import (
    DEFAULT_DOCUMENT_EXTENSION,
    build_minimal_docx_bytes,
    compute_document_stats,
    load_document_into_qtext,
    save_qtext_document,
)
from apps.simple_writer.main import SimpleWriterWindow
from core.file_manager.file_ops import FileOperationError


def test_compute_document_stats() -> None:
    """Verify word, character, paragraph, and reading-time calculations."""
    empty_stats = compute_document_stats("   \n\t  ")
    assert empty_stats.words == 0
    assert empty_stats.characters == 0
    assert empty_stats.paragraphs == 0
    assert empty_stats.reading_time_minutes == 0

    sample = "Jaiho Super Simple Office.\n\nCrafted with Soft Modern Pastel tokens."
    stats = compute_document_stats(sample)
    assert stats.words == 10
    assert stats.paragraphs == 2
    assert stats.characters == len(sample)
    assert stats.characters_no_spaces < stats.characters
    assert stats.reading_time_minutes == 1


def test_docx_default_roundtrip_and_format_validation(tmp_path: Path) -> None:
    """Verify default .docx archive creation, rich run preservation, and corrupt file rejection."""
    assert DEFAULT_DOCUMENT_EXTENSION == ".docx"

    doc = QTextDocument()
    cursor = QTextCursor(doc)

    bold_fmt = QTextCharFormat()
    bold_fmt.setFontWeight(QFont.Weight.Bold)
    cursor.insertText("Executive Summary", bold_fmt)

    cursor.insertBlock()

    italic_fmt = QTextCharFormat()
    italic_fmt.setFontItalic(True)
    cursor.insertText("All documents stay 100% local and offline.", italic_fmt)

    # Saving without an extension defaults automatically to .docx
    saved_path = save_qtext_document(
        destination=tmp_path / "executive_summary",
        title="Executive Summary",
        source_doc=doc,
    )
    assert saved_path.suffix == ".docx"
    assert saved_path.exists()

    with zipfile.ZipFile(saved_path, "r") as zf:
        names = set(zf.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}.issubset(names)

    # Reload the .docx into a fresh QTextDocument and verify text & formatting
    reloaded_doc = QTextDocument()
    loaded_path, inferred_title = load_document_into_qtext(saved_path, reloaded_doc)
    assert loaded_path == saved_path.resolve()
    assert inferred_title == "Executive Summary"
    assert "Executive Summary" in reloaded_doc.toPlainText()
    assert "100% local and offline." in reloaded_doc.toPlainText()

    first_block = reloaded_doc.begin()
    assert first_block.isValid()
    first_fragment = first_block.begin().fragment()
    assert first_fragment.charFormat().fontWeight() >= QFont.Weight.Bold

    # Minimal docx helper & Markdown roundtrip
    raw_docx = build_minimal_docx_bytes("Quick Note", "Line one\nLine two")
    assert len(raw_docx) > 200

    md_path = save_qtext_document(tmp_path / "notes.md", "Quick Note", reloaded_doc)
    assert "# Quick Note" in md_path.read_text(encoding="utf-8")

    # Reject unsupported extensions and corrupt .docx files
    bad_ext = tmp_path / "binary.exe"
    bad_ext.write_bytes(b"MZ")
    with pytest.raises(FileOperationError):
        load_document_into_qtext(bad_ext, reloaded_doc)

    corrupt_docx = tmp_path / "broken.docx"
    corrupt_docx.write_bytes(b"not-a-zip-archive")
    with pytest.raises(FileOperationError):
        load_document_into_qtext(corrupt_docx, reloaded_doc)


def test_simple_writer_window_pagination_outline_and_save(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify multi-page overflow, Heading outline navigation, Find & Replace, and .docx save."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleWriterWindow()
    qtbot.addWidget(window)
    window.show()

    assert window._editor.page_count == 1
    assert "Page 1 of 1" in window._stats_lbl.text()
    assert ".docx" in window._file_path_lbl.text()

    # Add a Heading 1 block and verify Outline sidebar updates
    window._doc_title_input.setText("Product Roadmap")
    window._editor.insertPlainText("Phase 3 Architecture")
    window._apply_style_code(1)
    assert window._outline_list.count() == 1
    outline_item = window._outline_list.item(0)
    assert outline_item is not None
    assert "Phase 3 Architecture" in outline_item.text()

    # Insert a page break and write on Page 2 -> verify multi-page pagination
    window.insert_page_break()
    window._editor.insertPlainText("Second page content with draft token.")
    assert window._editor.page_count >= 2
    assert "Page 2 of 2" in window._stats_lbl.text()

    # Test Find & Replace All
    window._find_input.setText("draft")
    window._replace_input.setText("final")
    window.replace_all()
    assert "final token." in window._editor.toPlainText()

    # Save to .docx and reload in a fresh window
    target_docx = tmp_path / "product_roadmap.docx"
    assert window._write_to_path(target_docx, set_as_active=True) is True
    assert window._is_dirty is False
    assert target_docx.exists()
    window.close()

    reopened = SimpleWriterWindow()
    qtbot.addWidget(reopened)
    assert reopened.load_file(target_docx) is True
    assert reopened._doc_title_input.text() == "Product Roadmap"
    assert "Phase 3 Architecture" in reopened._editor.toPlainText()
    assert "final token." in reopened._editor.toPlainText()
    reopened.close()