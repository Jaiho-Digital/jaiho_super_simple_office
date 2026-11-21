"""Automated unit and GUI tests for SimplePDF."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.simple_pdf.main import SimplePDFWindow
from apps.simple_pdf.pdf_engine import (
    PdfAnnotation,
    PdfDocumentService,
    build_sample_pdf_bytes,
    parse_page_range_spec,
)
from core.file_manager.file_ops import FileOperationError


def test_annotation_geometry_and_page_range_parsing() -> None:
    """Verify normalized annotation move/resize bounds, hit-testing, and page range parser."""
    ann = PdfAnnotation(
        kind="highlight",
        x=0.10,
        y=0.20,
        w=0.30,
        h=0.05,
        color_hex="#E8E0F7",
        text="Key metric",
    )
    assert ann.contains_norm_point(0.20, 0.22) is True
    assert ann.contains_norm_point(0.80, 0.80) is False
    assert ann.hits_bottom_right_handle(0.40, 0.25) is True
    assert "Highlight: Key metric" in ann.summary_label()

    # Translate within [0.0, 1.0] page bounds
    ann.move_by(0.80, 0.90)
    assert ann.x == pytest.approx(0.70)
    assert ann.y == pytest.approx(0.95)

    # Resize within remaining page space
    ann.move_by(-0.50, -0.70)
    ann.resize_to(0.45, 0.12)
    assert ann.w == pytest.approx(0.45)
    assert ann.h == pytest.approx(0.12)

    # Page range parsing
    assert parse_page_range_spec("1-2, 4, 2", total_pages=5) == [0, 1, 3]
    with pytest.raises(FileOperationError):
        parse_page_range_spec("", total_pages=5)
    with pytest.raises(FileOperationError):
        parse_page_range_spec("2-7", total_pages=5)
    with pytest.raises(FileOperationError):
        parse_page_range_spec("abc", total_pages=5)


def test_pdf_document_service_operations_and_merge(tmp_path: Path) -> None:
    """Verify PDF loading, appending, search, rotation, reordering, export, and merging."""
    pdf_a = tmp_path / "doc_a.pdf"
    pdf_a.write_bytes(
        build_sample_pdf_bytes(
            [
                ("Alpha Overview", ["First page discusses offline architecture."]),
                ("Beta Details", ["Second page covers Bengaluru studio metrics."]),
            ]
        )
    )

    pdf_b = tmp_path / "doc_b.pdf"
    pdf_b.write_bytes(
        build_sample_pdf_bytes(
            [
                ("Gamma Appendix", ["Third page summarizes quarterly goals."]),
            ]
        )
    )

    service = PdfDocumentService()
    try:
        assert service.load_file(pdf_a) == 2
        assert service.is_loaded is True

        # Append second PDF -> total 3 pages
        assert service.append_file(pdf_b) == 1
        assert service.page_count == 3

        # Full-text search across combined pages
        hits = service.search_text("Bengaluru")
        assert len(hits) == 1
        assert hits[0].display_page_index == 1

        # Rotate page 0 and reorder page 2 to front
        assert service.rotate_page(0, 90) == 90
        assert service.get_page_rotation(0) == 90
        assert service.move_page(2, 0) is True
        assert "Gamma Appendix" in service.get_page_text(0)

        # Duplicate & delete page
        assert service.duplicate_page(0) is True
        assert service.page_count == 4
        assert service.delete_page(0) is True
        assert service.page_count == 3

        # Add annotation & export flattened PDF
        ann = PdfAnnotation(kind="stamp", x=0.65, y=0.08, w=0.22, h=0.05, text="APPROVED")
        service.add_annotation(0, ann)
        assert service.total_annotation_count() == 1

        exported_pdf = tmp_path / "flattened_output.pdf"
        saved = service.export_pdf(exported_pdf, include_annotations=True)
        assert saved.exists()
        assert saved.stat().st_size > 1000
    finally:
        service.close()

    # Static multi-PDF merge helper
    merged_out = tmp_path / "merged_ab.pdf"
    merged_path = PdfDocumentService.merge_pdf_files([pdf_a, pdf_b], merged_out)
    assert merged_path.exists()

    verify_svc = PdfDocumentService()
    try:
        assert verify_svc.load_file(merged_path) == 3
    finally:
        verify_svc.close()

    # Reject non-PDF and corrupt PDF files
    bad_ext = tmp_path / "notes.txt"
    bad_ext.write_text("hello", encoding="utf-8")
    with pytest.raises(FileOperationError):
        verify_svc.load_file(bad_ext)

    corrupt_pdf = tmp_path / "corrupt.pdf"
    corrupt_pdf.write_bytes(b"not-a-valid-pdf-stream")
    with pytest.raises(FileOperationError):
        verify_svc.load_file(corrupt_pdf)


def test_simple_pdf_window_reader_annotations_and_tools(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SimplePDFWindow sample loading, navigation, annotations, search, and exports."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimplePDFWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.pdf_service.is_loaded is False
    assert window.open_sample_pdf() is True
    assert window.pdf_service.page_count == 3
    assert window._current_display_idx == 0

    # Navigate pages
    window.next_page()
    assert window._current_display_idx == 1
    window.previous_page()
    assert window._current_display_idx == 0

    # Add quick pastel annotations, inspect list, undo & delete
    a1 = window.add_quick_annotation(kind="highlight", text="Important")
    a2 = window.add_quick_annotation(kind="note", text="Follow up with team", color_hex="#F8EBCF")
    assert a1 is not None and a2 is not None
    assert window.pdf_service.total_annotation_count() == 2
    assert window._ann_list.count() == 2

    assert window.undo_last_annotation() is True
    assert window.pdf_service.total_annotation_count() == 1
    assert window._ann_list.count() == 1

    # Rotate & reorder pages
    window.rotate_current_page(90)
    assert window.pdf_service.get_page_rotation(0) == 90
    window._on_pages_reordered(0, 2)
    assert window._current_display_idx == 2

    # Search across document
    hits = window.run_text_search("SimplePDF")
    assert len(hits) >= 1

    # Export extracted text (.txt), split page range (.pdf), and full annotated PDF
    txt_out = tmp_path / "extracted.txt"
    assert window.extract_text_to_file(txt_out) is True
    assert txt_out.exists()
    assert "--- Page 1 ---" in txt_out.read_text(encoding="utf-8")

    split_out = tmp_path / "split_pages.pdf"
    assert window.extract_pages_to_pdf("1-2", split_out) is True
    assert split_out.exists()

    full_out = tmp_path / "annotated_full.pdf"
    assert window.export_current_pdf(full_out) is True
    assert full_out.exists()
    assert window._has_unsaved_edits is False

    window.close()