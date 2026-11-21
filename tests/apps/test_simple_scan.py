"""Automated unit and GUI tests for SimpleScan."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage
from pytestqt.qtbot import QtBot

from apps.simple_scan.main import SimpleScanWindow
from apps.simple_scan.scan_engine import (
    ScanBatchModel,
    ScannerAcquisitionService,
    ScanPageRecord,
    estimate_skew_angle,
    export_batch_as_images,
    export_scan_batch_to_pdf,
    render_processed_scan_image,
)
from core.file_manager.file_ops import FileOperationError


def test_scan_engine_acquisition_cleanup_and_exports(tmp_path: Path) -> None:
    """Verify scanner discovery, non-destructive crop/rotate/filter pipeline, and PDF/image export."""
    devices = ScannerAcquisitionService.discover_devices()
    assert len(devices) >= 1
    virtual_devs = [d for d in devices if d.backend == "virtual"]
    assert len(virtual_devs) == 1

    # Acquire two virtual A4 sheets (one slightly tilted for deskew estimation)
    raw_p1 = ScannerAcquisitionService.generate_virtual_scan_image(page_number=1, slight_skew=0.0)
    raw_p2 = ScannerAcquisitionService.generate_virtual_scan_image(page_number=2, slight_skew=2.5)
    assert raw_p1.width() == 1240
    assert raw_p1.height() == 1754

    detected_tilt = estimate_skew_angle(raw_p2)
    assert -6.0 <= detected_tilt <= 6.0

    page1 = ScanPageRecord(source_image=raw_p1, title="Contract Page 1", filter_preset="magic_color")
    page2 = ScanPageRecord(
        source_image=raw_p2,
        title="Contract Page 2",
        filter_preset="document_bw",
        rotation=90,
        crop_rect=(0.05, 0.05, 0.90, 0.90),
    )

    processed_p2 = render_processed_scan_image(page2, max_width=400)
    assert processed_p2.width() > 0
    assert processed_p2.height() > 0

    batch = ScanBatchModel([page1, page2])
    assert len(batch.pages) == 2

    # Export multi-page PDF
    pdf_out = tmp_path / "scanned_contract.pdf"
    saved_pdf = export_scan_batch_to_pdf(batch, pdf_out, page_size_mode="a4")
    assert saved_pdf.exists()
    assert saved_pdf.stat().st_size > 1500

    # Export batch PNG images
    img_dir = tmp_path / "png_pages"
    exported_imgs = export_batch_as_images(batch, img_dir, base_name="Contract", ext=".png")
    assert len(exported_imgs) == 2
    assert all(p.exists() for p in exported_imgs)

    # Reject unsupported file extensions on image import
    bad_file = tmp_path / "notes.txt"
    bad_file.write_text("not an image", encoding="utf-8")
    with pytest.raises(FileOperationError):
        ScannerAcquisitionService.load_image_file(bad_file)


def test_simple_scan_window_workflow_and_exports(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SimpleScanWindow scanning, image import, cleanup controls, reordering, and exports."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleScanWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.batch.is_empty is True

    # Scan two pages using the built-in Virtual Flatbed Scanner
    assert window.scan_new_page() is True
    assert window.scan_new_page() is True
    assert len(window.batch.pages) == 2
    assert window._thumb_list.count() == 2

    # Import an external PNG image into the batch
    sample_png = tmp_path / "receipt_photo.png"
    img = QImage(400, 560, QImage.Format.Format_ARGB32)
    img.fill(QColor("#EFECE6"))
    assert img.save(str(sample_png), "PNG") is True

    imported = window.import_image_files([sample_png])
    assert imported == 1
    assert len(window.batch.pages) == 3

    # Rotate, auto-deskew, switch filter preset, and reorder pages
    window.rotate_active_page(90)
    assert window.batch.active_page is not None
    assert window.batch.active_page.rotation == 90

    window.apply_auto_deskew()
    bw_idx = window._filter_combo.findData("document_bw")
    assert bw_idx >= 0
    window._filter_combo.setCurrentIndex(bw_idx)
    assert window.batch.active_page.filter_preset == "document_bw"

    window._on_pages_reordered(2, 0)
    assert window.batch.active_index == 0

    # Export multi-page PDF and batch JPEGs
    out_pdf = tmp_path / "batch_scan.pdf"
    assert window.export_pdf_to(out_pdf) is True
    assert out_pdf.exists()
    assert window._has_unsaved_scans is False

    out_jpg_dir = tmp_path / "jpg_export"
    assert window.export_images_to_dir(out_jpg_dir, ext=".jpg") is True
    assert len(list(out_jpg_dir.glob("*.jpg"))) == 3

    window.close()