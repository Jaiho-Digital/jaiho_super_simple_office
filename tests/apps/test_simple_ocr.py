"""Automated unit and GUI tests for SimpleOCR."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage
from pytestqt.qtbot import QtBot

from apps.simple_ocr.main import SimpleOCRWindow
from apps.simple_ocr.ocr_engine import (
    OcrPageItem,
    discover_ocr_backends,
    export_ocr_text_document,
    generate_sample_ocr_image,
    load_ocr_items_from_file,
    preprocess_image_for_ocr,
    recognize_image_text,
)
from apps.simple_pdf.pdf_engine import build_sample_pdf_bytes
from core.file_manager.file_ops import FileOperationError


def test_ocr_engine_backends_roi_and_exports(tmp_path: Path) -> None:
    """Verify OCR backend discovery, ROI preprocessing, recognition pipeline, and exports."""
    backends = discover_ocr_backends()
    assert len(backends) >= 1
    assert any(b.backend_id == "builtin_sample" for b in backends)

    sample_img = generate_sample_ocr_image()
    assert sample_img.width() == 1100
    assert sample_img.height() == 760

    roi_cropped = preprocess_image_for_ocr(
        sample_img,
        crop_rect=(0.05, 0.07, 0.90, 0.15),
        enhance_contrast=True,
    )
    assert roi_cropped.width() < sample_img.width()
    assert roi_cropped.height() < sample_img.height()

    text_out, backend_used, elapsed_ms = recognize_image_text(
        sample_img,
        backend_id="builtin_sample",
        crop_rect=(0.0, 0.0, 1.0, 1.0),
        enhance_contrast=True,
    )
    assert isinstance(text_out, str)
    assert len(text_out.strip()) > 0
    assert backend_used == "Built-in Fallback"
    assert elapsed_ms >= 1

    # Load a PNG image and a multi-page PDF into OcrPageItem entries
    png_file = tmp_path / "note_sheet.png"
    qimg = QImage(640, 480, QImage.Format.Format_ARGB32)
    qimg.fill(QColor("#FAF9F5"))
    assert qimg.save(str(png_file), "PNG") is True

    img_items = load_ocr_items_from_file(png_file)
    assert len(img_items) == 1
    assert img_items[0].label == "note_sheet.png"

    pdf_file = tmp_path / "scanned_pages.pdf"
    pdf_file.write_bytes(
        build_sample_pdf_bytes(
            [
                ("First Page", ["Offline OCR page one."]),
                ("Second Page", ["Offline OCR page two."]),
            ]
        )
    )
    pdf_items = load_ocr_items_from_file(pdf_file, pdf_dpi=120)
    assert len(pdf_items) == 2

    # Export recognized text to .txt, .md, and .docx
    sample_text = "Extracted invoice line items and totals."
    txt_out = export_ocr_text_document(tmp_path / "out.txt", sample_text)
    assert txt_out.read_text(encoding="utf-8") == sample_text

    md_out = export_ocr_text_document(tmp_path / "out.md", sample_text, title="Invoice OCR")
    assert md_out.exists()

    docx_out = export_ocr_text_document(tmp_path / "out.docx", sample_text, title="Invoice OCR")
    assert docx_out.exists()

    with pytest.raises(FileOperationError):
        export_ocr_text_document(tmp_path / "out.csv", sample_text)


def test_simple_ocr_window_workflow_and_export(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SimpleOCRWindow sample loading, ROI selection, extraction, and text export."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleOCRWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.load_sample_image() is True
    assert len(window.items) == 1
    assert window._page_list.count() == 1

    # Select custom ROI and reset back to full image
    window._on_region_selected((0.1, 0.1, 0.8, 0.25))
    assert window.items[0].crop_rect == pytest.approx((0.1, 0.1, 0.8, 0.25))
    window.reset_region_selection()
    assert window.items[0].crop_rect == (0.0, 0.0, 1.0, 1.0)

    # Extract text using built-in offline backend
    fb_idx = window._backend_combo.findData("builtin_sample")
    assert fb_idx >= 0
    window._backend_combo.setCurrentIndex(fb_idx)

    extracted = window.extract_active_page()
    assert len(extracted.strip()) > 0
    assert window._text_editor.toPlainText().strip() != ""

    # Append a second page and run Extract All
    window.items.append(
        OcrPageItem(source_image=generate_sample_ocr_image(), label="Page_2.png")
    )
    all_text = window.extract_all_pages()
    assert "--- Page_2.png ---" in all_text

    # Export extracted text to .txt
    exported_txt = tmp_path / "ocr_result.txt"
    assert window.export_text_to(exported_txt, ext=".txt") is True
    assert exported_txt.exists()
    assert len(exported_txt.read_text(encoding="utf-8").strip()) > 0

    window.close()