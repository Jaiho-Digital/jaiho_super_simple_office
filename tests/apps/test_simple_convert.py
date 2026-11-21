"""Automated unit and GUI tests for SimpleConvert."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage
from pytestqt.qtbot import QtBot

from apps.simple_convert.converter_engine import (
    convert_single_file,
    default_target_extension,
    detect_file_category,
    format_file_size,
)
from apps.simple_convert.main import SimpleConvertWindow
from apps.simple_pdf.pdf_engine import build_sample_pdf_bytes
from core.file_manager.file_ops import FileOperationError


def test_converter_category_detection_and_multi_domain_conversions(tmp_path: Path) -> None:
    """Verify category classification and conversions across documents, sheets, images, and PDFs."""
    assert detect_file_category("report.docx") == "document"
    assert detect_file_category("budget.xlsx") == "spreadsheet"
    assert detect_file_category("banner.png") == "image"
    assert detect_file_category("manual.pdf") == "pdf"

    assert default_target_extension("notes.md") == ".pdf"
    assert default_target_extension("notes.pdf") == ".png"
    assert format_file_size(512) == "512 B"
    assert format_file_size(2048) == "2.0 KB"

    out_dir = tmp_path / "converted"

    # 1. Document conversion: Markdown -> .docx and .pdf
    md_file = tmp_path / "architecture.md"
    md_file.write_text("# System Design\n\nOffline local-first execution.\n", encoding="utf-8")

    docx_outs = convert_single_file(md_file, ".docx", output_dir=out_dir)
    assert len(docx_outs) == 1
    assert docx_outs[0].suffix == ".docx"
    assert docx_outs[0].exists()

    pdf_outs = convert_single_file(md_file, ".pdf", output_dir=out_dir)
    assert len(pdf_outs) == 1
    assert pdf_outs[0].suffix == ".pdf"
    assert pdf_outs[0].stat().st_size > 500

    # 2. Spreadsheet conversion: CSV -> .xlsx, .json, and .html
    csv_file = tmp_path / "metrics.csv"
    csv_file.write_text("Quarter,Revenue\nQ1,1200\nQ2,1850\n", encoding="utf-8")

    xlsx_outs = convert_single_file(csv_file, ".xlsx", output_dir=out_dir)
    assert xlsx_outs[0].suffix == ".xlsx"
    assert xlsx_outs[0].exists()

    json_outs = convert_single_file(csv_file, ".json", output_dir=out_dir)
    parsed_records = json.loads(json_outs[0].read_text(encoding="utf-8"))
    assert len(parsed_records) == 2
    assert parsed_records[0]["Quarter"] == "Q1"

    html_outs = convert_single_file(csv_file, ".html", output_dir=out_dir)
    assert "<table>" in html_outs[0].read_text(encoding="utf-8")

    # 3. Image conversion: PNG -> .jpg and .ico
    png_file = tmp_path / "logo.png"
    img = QImage(320, 320, QImage.Format.Format_ARGB32)
    img.fill(QColor("#E8E0F7"))
    assert img.save(str(png_file), "PNG") is True

    jpg_outs = convert_single_file(png_file, ".jpg", output_dir=out_dir)
    assert jpg_outs[0].suffix == ".jpg"
    assert jpg_outs[0].exists()

    ico_outs = convert_single_file(png_file, ".ico", output_dir=out_dir)
    assert ico_outs[0].suffix == ".ico"
    assert ico_outs[0].exists()

    # 4. PDF conversion: multi-page PDF -> .txt and .png pages
    sample_pdf = tmp_path / "brief.pdf"
    sample_pdf.write_bytes(
        build_sample_pdf_bytes(
            [
                ("Page One", ["First page summary."]),
                ("Page Two", ["Second page summary."]),
            ]
        )
    )
    txt_outs = convert_single_file(sample_pdf, ".txt", output_dir=out_dir)
    assert "--- Page 2 ---" in txt_outs[0].read_text(encoding="utf-8")

    png_pages = convert_single_file(sample_pdf, ".png", output_dir=out_dir)
    assert len(png_pages) == 2

    # Reject unsupported file formats
    bad_file = tmp_path / "binary.exe"
    bad_file.write_bytes(b"MZ")
    with pytest.raises(FileOperationError):
        convert_single_file(bad_file, ".pdf", output_dir=out_dir)


def test_simple_convert_window_batch_queue(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify SimpleConvertWindow file queuing, target selection, and batch conversion."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleConvertWindow()
    qtbot.addWidget(window)
    window.show()

    out_folder = tmp_path / "batch_out"
    window._custom_output_dir = out_folder
    window._sync_output_dir_label()

    doc_in = tmp_path / "readme.txt"
    doc_in.write_text("SimpleConvert automated batch test.", encoding="utf-8")

    csv_in = tmp_path / "data.csv"
    csv_in.write_text("Name,Score\nAlice,95\n", encoding="utf-8")

    added = window.add_files([doc_in, csv_in])
    assert added == 2
    assert len(window.jobs) == 2
    assert window._queue_table.rowCount() == 2

    # Set explicit targets: readme.txt -> .md, data.csv -> .json
    window.jobs[0].target_ext = ".md"
    window.jobs[1].target_ext = ".json"

    succeeded = window.convert_all_jobs()
    assert succeeded == 2
    assert all(j.status == "done" for j in window.jobs)
    assert (out_folder / "readme.md").exists()
    assert (out_folder / "data.json").exists()

    window.clear_jobs()
    assert len(window.jobs) == 0
    window.close()