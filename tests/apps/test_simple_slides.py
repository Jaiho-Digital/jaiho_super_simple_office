"""Automated unit and GUI tests for SimpleSlides."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.simple_slides.main import SimpleSlidesWindow, SlideshowPresenterDialog
from apps.simple_slides.presentation_model import (
    DEFAULT_PRESENTATION_EXTENSION,
    SLIDE_LAYOUTS,
    SLIDE_THEMES,
    PresentationModel,
    SlideRecord,
    get_slide_theme,
    load_presentation,
    save_presentation,
)
from core.file_manager.file_ops import FileOperationError


def test_presentation_model_themes_layouts_and_slide_operations() -> None:
    """Verify 12 built-in themes, 5 slide layouts, cloning, reordering, and deletion guards."""
    assert len(SLIDE_THEMES) == 12
    assert len(SLIDE_LAYOUTS) == 5
    assert get_slide_theme("unknown_theme").theme_id == "lavender_snow"

    deck = PresentationModel(theme_id="sage_ivory")
    assert deck.palette.display_name == "Ivory & Sage"
    assert len(deck.slides) == 1
    assert deck.active_slide.layout == "title"

    # Add slides across distinct layouts
    s_body = deck.add_slide(layout="title_body")
    assert deck.active_index == 1
    assert s_body.layout == "title_body"

    s_cols = deck.add_slide(layout="two_column")
    assert deck.active_index == 2
    assert s_cols.layout == "two_column"

    # Duplicate active slide -> creates independent copy with a new slide_id
    cloned = deck.duplicate_slide()
    assert len(deck.slides) == 4
    assert deck.active_index == 3
    assert cloned.slide_id != s_cols.slide_id
    assert cloned.title == s_cols.title

    # Reorder slide
    assert deck.move_slide(3, 1) is True
    assert deck.active_index == 1
    assert deck.slides[1].slide_id == cloned.slide_id

    # Delete slides down to 1 -> last remaining slide cannot be deleted
    assert deck.delete_slide(1) is True
    assert deck.delete_slide(1) is True
    assert deck.delete_slide(1) is True
    assert len(deck.slides) == 1
    assert deck.delete_slide(0) is False


def test_pptx_default_roundtrip_speaker_notes_and_validation(tmp_path: Path) -> None:
    """Verify .pptx default archive creation, multi-layout roundtrip, speaker notes, and error handling."""
    assert DEFAULT_PRESENTATION_EXTENSION == ".pptx"

    slides = [
        SlideRecord(
            layout="title",
            title="Jaiho Super Simple Office",
            subtitle="Offline-First Desktop Productivity",
        ),
        SlideRecord(
            layout="title_body",
            title="Core Pillars",
            body_lines=["Zero telemetry", "Instant startup", "Native file formats"],
            speaker_notes="Emphasize 100% local SQLite and OpenXML support.",
        ),
        SlideRecord(
            layout="two_column",
            title="Architecture Comparison",
            body_lines=["Local desktop execution", "Predictable memory footprint"],
            right_lines=["No cloud lock-in", "Sub-second file I/O"],
        ),
        SlideRecord(
            layout="quote",
            title="“Everything you need. Nothing you don't.”",
            subtitle="— Jaiho Digital",
        ),
    ]
    deck = PresentationModel(slides=slides, theme_id="midnight_velvet")

    # Saving without an extension automatically appends .pptx
    saved_pptx = save_presentation(tmp_path / "product_overview", deck)
    assert saved_pptx.suffix == ".pptx"
    assert saved_pptx.exists()

    with zipfile.ZipFile(saved_pptx, "r") as zf:
        archive_members = set(zf.namelist())
        assert {
            "[Content_Types].xml",
            "_rels/.rels",
            "ppt/presentation.xml",
            "ppt/theme/theme1.xml",
            "ppt/slides/slide1.xml",
            "ppt/slides/slide2.xml",
            "ppt/slides/slide3.xml",
            "ppt/slides/slide4.xml",
            "ppt/notesSlides/notesSlide2.xml",
        }.issubset(archive_members)

    # Reload .pptx and verify slides, layouts, theme, and speaker notes survive intact
    reloaded = load_presentation(saved_pptx)
    assert reloaded.theme_id == "midnight_velvet"
    assert len(reloaded.slides) == 4
    assert reloaded.slides[0].title == "Jaiho Super Simple Office"
    assert reloaded.slides[1].body_lines == [
        "Zero telemetry",
        "Instant startup",
        "Native file formats",
    ]
    assert "100% local SQLite" in reloaded.slides[1].speaker_notes
    assert reloaded.slides[2].layout == "two_column"
    assert reloaded.slides[2].right_lines == ["No cloud lock-in", "Sub-second file I/O"]
    assert reloaded.slides[3].layout == "quote"

    # Reject unsupported extensions and corrupt .pptx archives
    bad_ext = tmp_path / "deck.pdf"
    bad_ext.write_bytes(b"%PDF-1.4")
    with pytest.raises(FileOperationError):
        load_presentation(bad_ext)

    corrupt_pptx = tmp_path / "broken.pptx"
    corrupt_pptx.write_bytes(b"invalid-zip-content")
    with pytest.raises(FileOperationError):
        load_presentation(corrupt_pptx)


def test_simple_slides_window_on_slide_editing_ribbon_and_export(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify direct on-slide canvas editing, ribbon theme/style controls, presenter, and .pptx/.pdf export."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleSlidesWindow()
    qtbot.addWidget(window)
    window.show()

    # Edit title & subtitle directly on the 16:9 stage
    window._canvas.title_box.setText("Q4 Engineering Review")
    window._canvas.title_box.textEdited.emit("Q4 Engineering Review")
    window._canvas.subtitle_box.setText("Bengaluru Product Studio")
    window._canvas.subtitle_box.textEdited.emit("Bengaluru Product Studio")

    assert window.deck.active_slide.title == "Q4 Engineering Review"
    assert window.deck.active_slide.subtitle == "Bengaluru Product Studio"
    assert window._is_dirty is True

    # Switch deck theme via ribbon combo to Ivory & Sage
    sage_idx = window._deck_theme_combo.findData("sage_ivory")
    assert sage_idx >= 0
    window._deck_theme_combo.setCurrentIndex(sage_idx)
    assert window.deck.theme_id == "sage_ivory"

    # Add a Two-Column slide and edit both columns directly on the canvas
    window.add_slide("two_column")
    assert len(window.deck.slides) == 2
    assert window._canvas.left_bullets_box.isVisible()
    assert window._canvas.right_bullets_box.isVisible()

    window._canvas.title_box.setText("Highlights & Next Steps")
    window._canvas.title_box.textEdited.emit("Highlights & Next Steps")
    window._canvas.left_bullets_box.setPlainText("Shipped Core Suite\nAll tests green")
    window._canvas.right_bullets_box.setPlainText("Build SimplePDF\nBuild SimpleScan")

    assert window.deck.active_slide.body_lines == ["Shipped Core Suite", "All tests green"]
    assert window.deck.active_slide.right_lines == ["Build SimplePDF", "Build SimpleScan"]

    # Add speaker notes
    window._notes_edit.setPlainText("Review phase 4 schedule.")
    assert window.deck.active_slide.speaker_notes == "Review phase 4 schedule."

    # Verify full-screen presenter dialog navigation
    presenter = SlideshowPresenterDialog(deck=window.deck, start_index=0, parent=window)
    qtbot.addWidget(presenter)
    assert presenter.current_slide_index == 0
    presenter.next_slide()
    assert presenter.current_slide_index == 1
    presenter.previous_slide()
    assert presenter.current_slide_index == 0
    presenter.close()

    # Export to 16:9 PDF and save to .pptx
    pdf_out = tmp_path / "q4_review.pdf"
    assert window._export_to_pdf(pdf_out) is True
    assert pdf_out.exists()
    assert pdf_out.stat().st_size > 500

    pptx_out = tmp_path / "q4_review.pptx"
    assert window._write_deck_to(pptx_out, set_as_active=True) is True
    assert window._is_dirty is False
    window.close()

    # Reopen saved .pptx in a fresh SimpleSlidesWindow
    reopened = SimpleSlidesWindow()
    qtbot.addWidget(reopened)
    assert reopened.load_file(pptx_out) is True
    assert reopened.deck.theme_id == "sage_ivory"
    assert len(reopened.deck.slides) == 2
    assert reopened.deck.slides[0].title == "Q4 Engineering Review"
    assert reopened.deck.slides[1].right_lines == ["Build SimplePDF", "Build SimpleScan"]
    reopened.close()