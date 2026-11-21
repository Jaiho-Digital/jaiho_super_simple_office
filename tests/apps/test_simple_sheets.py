"""Automated unit and GUI tests for SimpleSheets."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.simple_sheets.formula_engine import (
    CellCoord,
    CellScalar,
    col_index_to_label,
    col_label_to_index,
    evaluate_cell_input,
    expand_range,
    format_cell_display,
    format_cell_ref,
    parse_cell_ref,
    parse_literal_value,
)
from apps.simple_sheets.main import SimpleSheetsWindow
from apps.simple_sheets.sheet_model import (
    DEFAULT_WORKBOOK_EXTENSION,
    WorkbookModel,
    load_workbook,
    save_workbook,
)
from core.file_manager.file_ops import FileOperationError


def test_formula_engine_coordinates_literals_and_functions() -> None:
    """Verify A1 notation conversion, literal parsing, display formats, and formula evaluation."""
    assert col_index_to_label(0) == "A"
    assert col_index_to_label(25) == "Z"
    assert col_index_to_label(26) == "AA"
    assert col_label_to_index("AA") == 26

    assert parse_cell_ref("$B$12") == (11, 1)
    assert format_cell_ref(11, 1) == "B12"
    assert expand_range("A1", "B2") == [(0, 0), (0, 1), (1, 0), (1, 1)]

    assert parse_literal_value("1,250.50") == 1250.5
    assert parse_literal_value("25%") == pytest.approx(0.25)
    assert parse_literal_value("true") is True
    assert parse_literal_value("Revenue") == "Revenue"

    assert format_cell_display(1250.5, number_format="currency") == "$1,250.50"
    assert format_cell_display(0.125, number_format="percent") == "12.50%"
    assert format_cell_display(42.0, number_format="general") == "42"

    grid: dict[CellCoord, CellScalar] = {
        (0, 0): 10.0,
        (1, 0): 20.0,
        (2, 0): 30.0,
        (0, 1): "Active",
    }

    def _resolve(coord: CellCoord) -> CellScalar:
        return grid.get(coord)

    assert evaluate_cell_input("=SUM(A1:A3)", _resolve) == 60.0
    assert evaluate_cell_input("=AVERAGE(A1:A3)", _resolve) == 20.0
    assert evaluate_cell_input("=MIN(A1:A3) + MAX(A1:A3)", _resolve) == 40.0
    assert evaluate_cell_input("=COUNT(A1:B3)", _resolve) == 3.0
    assert evaluate_cell_input("=COUNTA(A1:B3)", _resolve) == 4.0
    assert evaluate_cell_input('=IF(A3 > 25, "High", "Low")', _resolve) == "High"
    assert evaluate_cell_input('=CONCAT(B1, "-", A1)', _resolve) == "Active-10"

    # Standard spreadsheet error codes
    assert evaluate_cell_input("=10 / 0", _resolve) == "#DIV/0!"
    assert evaluate_cell_input("=UNKNOWN_FN(A1)", _resolve) == "#NAME?"
    assert evaluate_cell_input("=SUM(ZZZ9999999)", _resolve) == "#REF!"


def test_sheet_model_recalculation_cycles_and_xlsx_roundtrip(tmp_path: Path) -> None:
    """Verify transitive dependency recalculation, cycle detection, and .xlsx / .csv I/O."""
    assert DEFAULT_WORKBOOK_EXTENSION == ".xlsx"

    wb = WorkbookModel()
    s1 = wb.active_sheet
    s1.name = "Budget"

    s1.set_cell_input(0, 0, "Item")
    s1.set_cell_input(0, 1, "Cost")
    s1.update_cell_style(0, 0, bold=True, bg_tint="#E8E0F7")
    s1.update_cell_style(0, 1, bold=True, bg_tint="#E8E0F7")

    s1.set_cell_input(1, 0, "Hosting")
    s1.set_cell_input(1, 1, "120")
    s1.set_cell_input(2, 0, "Domain")
    s1.set_cell_input(2, 1, "30")
    s1.set_cell_input(3, 0, "Total")
    s1.set_cell_input(3, 1, "=SUM(B2:B3)")
    s1.update_cell_style(3, 1, bold=True, number_format="currency")

    assert s1.get_evaluated_value(3, 1) == 150.0
    assert s1.get_display_text(3, 1) == "$150.00"

    # Upstream edit automatically updates dependent formula cell
    changed = s1.set_cell_input(1, 1, "200")
    assert (3, 1) in changed
    assert s1.get_evaluated_value(3, 1) == 230.0

    # Circular dependency protection
    s1.set_cell_input(5, 0, "=B6")
    s1.set_cell_input(5, 1, "=A6")
    assert s1.get_evaluated_value(5, 1) == "#CYCLE!"
    s1.clear_cell(5, 0)
    s1.clear_cell(5, 1)

    # Add second worksheet
    s2 = wb.add_sheet("Summary")
    s2.set_cell_input(0, 0, "42")

    # Save without extension defaults to multi-sheet .xlsx
    saved_xlsx = save_workbook(tmp_path / "quarterly_budget", wb)
    assert saved_xlsx.suffix == ".xlsx"
    assert saved_xlsx.exists()

    with zipfile.ZipFile(saved_xlsx, "r") as zf:
        archive_files = set(zf.namelist())
        assert {
            "[Content_Types].xml",
            "xl/workbook.xml",
            "xl/styles.xml",
            "xl/sharedStrings.xml",
            "xl/worksheets/sheet1.xml",
            "xl/worksheets/sheet2.xml",
        }.issubset(archive_files)

    # Reload .xlsx and verify sheets, formulas, styles, and values survive intact
    reloaded = load_workbook(saved_xlsx)
    assert [s.name for s in reloaded.sheets] == ["Budget", "Summary"]
    r_budget = reloaded.sheets[0]
    assert r_budget.get_raw_input(3, 1) == "=SUM(B2:B3)"
    assert r_budget.get_evaluated_value(3, 1) == 230.0
    total_cell = r_budget.get_cell(3, 1)
    assert total_cell is not None
    assert total_cell.bold is True
    assert total_cell.number_format == "currency"
    header_cell = r_budget.get_cell(0, 0)
    assert header_cell is not None
    assert header_cell.bg_tint == "#E8E0F7"

    # CSV export & import roundtrip
    csv_path = save_workbook(tmp_path / "budget.csv", reloaded, sheet_index=0)
    csv_wb = load_workbook(csv_path)
    assert csv_wb.active_sheet.get_display_text(1, 0) == "Hosting"

    # Reject unsupported file types
    bad_file = tmp_path / "invalid.pdf"
    bad_file.write_bytes(b"%PDF-1.4")
    with pytest.raises(FileOperationError):
        load_workbook(bad_file)


def test_simple_sheets_window_formulas_charts_and_persistence(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify formula bar entry, Name Box jump, multi-sheet tabs, chart extraction, and .xlsx save."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleSheetsWindow()
    qtbot.addWidget(window)
    window.show()

    # Populate label + value columns via formula bar
    entries: tuple[tuple[str, str], ...] = (
        ("A1", "Month"),
        ("B1", "Sales"),
        ("A2", "Jan"),
        ("B2", "100"),
        ("A3", "Feb"),
        ("B3", "250"),
        ("B4", "=SUM(B2:B3)"),
    )
    for ref, val in entries:
        window._name_box.setText(ref)
        window._jump_to_name_box_ref()
        window._formula_input.setText(val)
        window._commit_formula_bar()

    assert window.active_sheet.get_evaluated_value(3, 1) == 350.0
    item_b4 = window._table.item(3, 1)
    assert item_b4 is not None
    assert item_b4.text() == "350"

    # Verify chart series extraction from the populated table
    labels, values, title = window.extract_chart_series_from_selection()
    assert title == "Sales"
    assert labels[:2] == ["Jan", "Feb"]
    assert values[:2] == [100.0, 250.0]

    # Add a second sheet tab
    window.add_new_sheet()
    assert len(window.workbook.sheets) == 2

    # Save workbook to .xlsx and reopen in a new window
    out_xlsx = tmp_path / "sales_tracker.xlsx"
    assert window._write_workbook_to(out_xlsx, set_as_active=True) is True
    assert window._is_dirty is False
    window.close()

    reopened = SimpleSheetsWindow()
    qtbot.addWidget(reopened)
    assert reopened.load_file(out_xlsx) is True
    assert len(reopened.workbook.sheets) == 2
    assert reopened.workbook.sheets[0].get_evaluated_value(3, 1) == 350.0
    reopened.close()