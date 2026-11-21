"""Automated unit and GUI tests for SimpleCalculator."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from apps.simple_calculator.engine import AngleMode, CalculatorError, MathEngine, format_number
from apps.simple_calculator.main import SimpleCalculatorWindow


def test_math_engine_arithmetic_and_precedence() -> None:
    """Verify basic arithmetic, operator precedence, and clean decimal formatting."""
    engine = MathEngine()

    assert engine.evaluate("2 + 3 * 4").formatted == "14"
    assert engine.evaluate("(2 + 3) * 4").formatted == "20"
    assert engine.evaluate("0.1 + 0.2").formatted == "0.3"
    assert engine.evaluate("10 % 3").formatted == "1"
    assert engine.evaluate("2^10").formatted == "1024"
    assert format_number(-0.0) == "0"


def test_math_engine_unicode_and_implicit_multiplication() -> None:
    """Verify unicode operators, roots, factorials, percentages, and implicit multiplication."""
    engine = MathEngine()

    assert engine.evaluate("6 × 7 ÷ 2").formatted == "21"
    assert engine.evaluate("√16 + √(9)").formatted == "7"
    assert engine.evaluate("5!").formatted == "120"
    assert engine.evaluate("(2 + 3)!").formatted == "120"
    assert engine.evaluate("50%").formatted == "0.5"
    assert engine.evaluate("200 + 15%").formatted == "200.15"
    assert engine.evaluate("3(4 + 5)").formatted == "27"
    assert math.isclose(engine.evaluate("2pi").value, 2 * math.pi, rel_tol=1e-12)


def test_math_engine_trigonometry_deg_and_rad() -> None:
    """Verify DEG and RAD angle modes, cardinal angle snapping, and inverse trig."""
    engine = MathEngine()
    engine.angle_mode = AngleMode.DEG

    assert engine.evaluate("sin(30)").formatted == "0.5"
    assert engine.evaluate("cos(60)").formatted == "0.5"
    assert engine.evaluate("sin(180)").formatted == "0"
    assert engine.evaluate("asin(0.5)").formatted == "30"

    with pytest.raises(CalculatorError, match="undefined"):
        engine.evaluate("tan(90)")

    engine.angle_mode = AngleMode.RAD
    assert engine.evaluate("sin(pi / 6)").formatted == "0.5"
    assert engine.evaluate("cos(pi)").formatted == "-1"
    assert math.isclose(engine.evaluate("atan(1)").value, math.pi / 4, rel_tol=1e-12)


def test_math_engine_logarithms_roots_and_security_sandboxing() -> None:
    """Verify scientific functions, domain errors, and strict rejection of unsafe constructs."""
    engine = MathEngine()

    assert engine.evaluate("ln(e)").formatted == "1"
    assert engine.evaluate("log(1000)").formatted == "3"
    assert engine.evaluate("cbrt(-27)").formatted == "-3"

    # Ans chaining
    engine.evaluate("12 * 5")
    assert engine.evaluate("Ans + 40").formatted == "100"

    # Domain & division errors
    with pytest.raises(CalculatorError, match="divide by zero"):
        engine.evaluate("42 / 0")
    with pytest.raises(CalculatorError, match="negative number"):
        engine.evaluate("sqrt(-4)")
    with pytest.raises(CalculatorError, match="positive number"):
        engine.evaluate("ln(0)")
    with pytest.raises(CalculatorError, match="non-negative integers"):
        engine.evaluate("(-3)!")

    # Reject arbitrary Python execution attempts
    for malicious in (
        "__import__('os').system('echo hi')",
        "[x for x in range(5)]",
        "lambda x: x",
        "open('secret.txt')",
    ):
        with pytest.raises(CalculatorError):
            engine.evaluate(malicious)


def test_simple_calculator_window_interactions_and_persistence(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify UI evaluation, modes, memory register, inline errors, and tape persistence."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    window = SimpleCalculatorWindow()
    qtbot.addWidget(window)
    window.show()

    # Evaluate expression via input box
    window._expr_input.setText("sin(30) * 100")
    result = window.evaluate_current()
    assert result is not None
    assert result.formatted == "50"
    assert window._readout_label.text() == "50"
    assert window._tape_list.count() == 1

    # Memory store and recall
    window._handle_memory_action("ms")
    assert window._has_memory is True
    window.clear_all()
    window._handle_memory_action("mr")
    assert window._expr_input.text() == "50"

    # Inline error feedback on invalid expression
    window._expr_input.setText("9 / 0")
    assert window.evaluate_current() is None
    assert window._error_label.isVisible()
    assert "zero" in window._error_label.text().lower()

    # Mode toggle (Standard hides scientific panel)
    window._set_calculator_mode("standard")
    assert not window._sci_panel.isVisible()
    window._set_calculator_mode("scientific")
    assert window._sci_panel.isVisible()

    window.close()

    # Reopen window and confirm tape persistence
    reopened = SimpleCalculatorWindow()
    qtbot.addWidget(reopened)
    assert reopened._tape_list.count() == 1
    reopened.close()