"""Automated unit and GUI tests for SimpleUnits."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.simple_units.converter import UnitConversionError, UnitConverter
from apps.simple_units.main import SimpleUnitsWindow


def test_unit_converter_linear_and_digital_scales() -> None:
    """Verify precision across length, mass, speed, pressure, and SI/binary storage."""
    converter = UnitConverter()

    # Length: 1 meter -> feet & inches
    m_to_ft = converter.convert("length", "m", "ft", 1.0, sig_figs=8)
    assert math.isclose(m_to_ft.output_value, 3.280839895, rel_tol=1e-8)

    in_to_cm = converter.convert("length", "in", "cm", 10.0)
    assert in_to_cm.formatted_output == "25.4"

    # Mass: 1 kg -> lb
    kg_to_lb = converter.convert("mass", "kg", "lb", 1.0, sig_figs=8)
    assert math.isclose(kg_to_lb.output_value, 2.20462262, rel_tol=1e-7)

    # Digital storage: 1 GiB == 1024 MiB, 1 GB == 1000 MB
    gib_to_mib = converter.convert("data", "gib", "mib", 1.0)
    assert gib_to_mib.formatted_output == "1024"

    gb_to_mb = converter.convert("data", "gb", "mb", 2.5)
    assert gb_to_mb.formatted_output == "2500"

    # Bandwidth: 100 Mbps == 12.5 MB/s
    mbps_to_mbs = converter.convert("bandwidth", "mbps", "mbs", 100.0)
    assert mbps_to_mbs.formatted_output == "12.5"


def test_unit_converter_affine_temperature_scales() -> None:
    """Verify non-linear offset conversions across Celsius, Fahrenheit, Kelvin, and Rankine."""
    converter = UnitConverter()

    assert converter.convert("temperature", "c", "f", 0.0).formatted_output == "32"
    assert converter.convert("temperature", "c", "f", 100.0).formatted_output == "212"
    assert converter.convert("temperature", "f", "c", -40.0).formatted_output == "-40"
    assert converter.convert("temperature", "c", "k", 25.0).formatted_output == "298.15"
    assert converter.convert("temperature", "k", "c", 0.0).formatted_output == "-273.15"

    r_to_f = converter.convert("temperature", "r", "f", 459.67)
    assert r_to_f.formatted_output == "0"


def test_unit_converter_all_equivalents_search_and_errors() -> None:
    """Verify category-wide conversions, cross-category search, and invalid lookups."""
    converter = UnitConverter()

    all_length = converter.convert_to_all("length", "km", 1.0)
    by_id = {item.to_unit.unit_id: item.formatted_output for item in all_length}
    assert by_id["m"] == "1000"
    assert by_id["cm"] == "100000"

    matches = converter.search_units("psi")
    assert any(cat.category_id == "pressure" and unit.unit_id == "psi" for cat, unit in matches)

    with pytest.raises(UnitConversionError):
        converter.convert("unknown_cat", "m", "ft", 1.0)

    with pytest.raises(UnitConversionError):
        converter.convert("length", "m", "nonexistent_unit", 1.0)

    with pytest.raises(UnitConversionError):
        converter.convert("length", "m", "ft", float("nan"))


def test_simple_units_window_workflow_and_persistence(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify live recalculation, unit swapping, search navigation, and pinned pair persistence."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleUnitsWindow()
    qtbot.addWidget(window)
    window.show()

    # Default length conversion: 10 m -> ft
    window._value_input.setText("10")
    assert "32.80839895" in window._result_lbl.text()
    assert window._equiv_table.rowCount() > 5

    # Swap units (ft -> m)
    window._swap_units()
    assert window._from_combo.currentData() == "ft"
    assert window._to_combo.currentData() == "m"
    assert "3.048" in window._result_lbl.text()

    # Pin pair and verify persistence across restart
    window._pin_current_pair()
    assert window._favorites_list.count() == 1

    # Search for 'psi' and click the match
    window._on_search_changed("psi")
    assert window._search_results_list.isVisible()
    first_match = window._search_results_list.item(0)
    assert first_match is not None
    window._on_search_result_clicked(first_match)
    assert window._active_category_id == "pressure"
    assert window._from_combo.currentData() == "psi"

    # Invalid text displays inline error cleanly
    window._value_input.setText("abc")
    assert window._error_lbl.isVisible()
    assert window._equiv_table.rowCount() == 0

    window.close()

    reopened = SimpleUnitsWindow()
    qtbot.addWidget(reopened)
    assert reopened._favorites_list.count() == 1
    reopened.close()