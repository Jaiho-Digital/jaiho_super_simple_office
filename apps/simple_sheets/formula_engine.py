"""Safe, zero-dependency Excel/Sheets-compatible formula parser & evaluator for SimpleSheets."""

from __future__ import annotations

import contextlib
import math
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass

CellCoord = tuple[int, int]  # (0-based row, 0-based col)
CellScalar = float | int | str | bool | None

_CELL_REF_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)$")
_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<NUMBER>\d+(?:\.\d*)?|\.\d+)
      | (?P<STRING>"(?:""|[^"])*")
      | (?P<CELL_RANGE>\$?[A-Za-z]{1,3}\$?[1-9][0-9]{0,5}\s*:\s*\$?[A-Za-z]{1,3}\$?[1-9][0-9]{0,5})
      | (?P<CELL_REF>\$?[A-Za-z]{1,3}\$?[1-9][0-9]{0,5})(?!\s*\()
      | (?P<IDENT>[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<OP><>|!=|<=|>=|==|[+\-*/^%()=,<>;])
    )
    """,
    re.VERBOSE,
)


class FormulaEvaluationError(Exception):
    """Raised when a formula encounters a syntax, reference, or math error."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ----------------------------------------------------------------------
# A1 Notation & Range Coordinate Helpers
# ----------------------------------------------------------------------


def col_index_to_label(col_idx: int) -> str:
    """Convert a 0-based column index into an Excel column letter (0 -> 'A', 26 -> 'AA')."""
    if col_idx < 0:
        raise ValueError(f"Column index must be non-negative, got {col_idx}.")
    n = col_idx + 1
    chars: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


def col_label_to_index(label: str) -> int:
    """Convert an Excel column letter into a 0-based index ('A' -> 0, 'AA' -> 26)."""
    cleaned = label.strip().upper()
    if not cleaned or not cleaned.isalpha():
        raise FormulaEvaluationError("#REF!", f"Invalid column label '{label}'.")
    total = 0
    for ch in cleaned:
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total - 1


def parse_cell_ref(ref: str) -> CellCoord:
    """Parse an A1-style reference ('B4' or '$B$4') into a 0-based (row, col) tuple."""
    match = _CELL_REF_RE.match(ref.strip())
    if not match:
        raise FormulaEvaluationError("#REF!", f"Invalid cell reference '{ref}'.")
    col_str, row_str = match.group(1), match.group(2)
    col_idx = col_label_to_index(col_str)
    row_idx = int(row_str) - 1
    if col_idx >= 16384 or row_idx >= 1_048_576:
        raise FormulaEvaluationError(
            "#REF!",
            f"Cell reference '{ref}' is out of sheet bounds.",
        )

    return row_idx, col_idx

def format_cell_ref(row: int, col: int) -> str:
    """Format a 0-based (row, col) coordinate into A1 notation ((0, 0) -> 'A1')."""
    if row < 0 or col < 0:
        raise ValueError(f"Cell coordinates must be non-negative: ({row}, {col}).")
    return f"{col_index_to_label(col)}{row + 1}"


def expand_range(start_ref: str, end_ref: str, max_cells: int = 25000) -> list[CellCoord]:
    """Expand 'A1:C3' into a row-major list of 0-based (row, col) coordinates."""
    r1, c1 = parse_cell_ref(start_ref)
    r2, c2 = parse_cell_ref(end_ref)
    r_min, r_max = min(r1, r2), max(r1, r2)
    c_min, c_max = min(c1, c2), max(c1, c2)

    count = (r_max - r_min + 1) * (c_max - c_min + 1)
    if count > max_cells:
        raise FormulaEvaluationError("#REF!", f"Range exceeds maximum of {max_cells} cells.")

    coords: list[CellCoord] = []
    for r in range(r_min, r_max + 1):
        for c in range(c_min, c_max + 1):
            coords.append((r, c))
    return coords


def extract_dependencies(raw_input: str) -> set[CellCoord]:
    """Return all cell coordinates referenced by a raw cell input string."""
    stripped = raw_input.strip()
    if not stripped.startswith("="):
        return set()

    deps: set[CellCoord] = set()
    expr = stripped[1:]
    pos = 0
    while pos < len(expr):
        match = _TOKEN_RE.match(expr, pos)
        if not match:
            pos += 1
            continue
        pos = match.end()
        if match.lastgroup == "CELL_RANGE":
            left, right = match.group("CELL_RANGE").split(":", 1)
            with contextlib.suppress(FormulaEvaluationError):
                deps.update(expand_range(left.strip(), right.strip()))
        elif match.lastgroup == "CELL_REF":
            with contextlib.suppress(FormulaEvaluationError):
                deps.add(parse_cell_ref(match.group("CELL_REF")))
    return deps


# ----------------------------------------------------------------------
# Literal Parsing & Display Formatting
# ----------------------------------------------------------------------


def parse_literal_value(raw_input: str) -> CellScalar:
    """Convert a non-formula string into a float, bool, None, or plain str."""
    if raw_input == "":
        return None
    trimmed = raw_input.strip()
    if trimmed == "":
        return raw_input

    upper = trimmed.upper()
    if upper == "TRUE":
        return True
    if upper == "FALSE":
        return False

    # Support percentage entry like "12.5%" -> 0.125
    if trimmed.endswith("%") and len(trimmed) > 1:
        num_part = trimmed[:-1].replace(",", "").strip()
        try:
            val = float(num_part) / 100.0
            if math.isfinite(val):
                return val
        except ValueError:
            pass

    # Support plain or comma-grouped numbers like "1,250.75"
    numeric_candidate = trimmed.replace(",", "")
    try:
        val = float(numeric_candidate)
        if math.isfinite(val):
            return val
    except ValueError:
        pass

    return raw_input


def format_cell_display(value: CellScalar, number_format: str = "general") -> str:
    """Format an evaluated CellScalar for crisp display in the spreadsheet grid."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return value

    num = float(value)
    if not math.isfinite(num):
        return "#NUM!"

    # Avoid "-0" display artifact
    if abs(num) < 1e-12:
        num = 0.0

    fmt = number_format.strip().lower()
    if fmt == "currency":
        return f"${num:,.2f}"
    if fmt == "percent":
        return f"{num * 100.0:.2f}%"
    if fmt == "number":
        return f"{num:,.2f}"
    if fmt == "integer":
        return f"{round(num):,}"

    # General format: clean integer if whole number, otherwise up to 10 significant decimals
    if num.is_integer() and abs(num) < 1e15:
        return str(int(round(num)))
    formatted = f"{num:.10f}".rstrip("0").rstrip(".")
    return "0" if formatted == "-0" else formatted


# ----------------------------------------------------------------------
# Tokenizer & Recursive-Descent Formula Evaluator
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: str


def _tokenize_formula(expr: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    length = len(expr)
    while pos < length:
        if expr[pos].isspace():
            pos += 1
            continue
        match = _TOKEN_RE.match(expr, pos)
        if not match or match.lastgroup is None:
            raise FormulaEvaluationError("#ERROR!", f"Unexpected character '{expr[pos]}'.")
        tokens.append(_Token(kind=match.lastgroup, value=match.group(match.lastgroup)))
        pos = match.end()
    tokens.append(_Token(kind="EOF", value=""))
    return tokens


def _to_number(val: CellScalar) -> float:
    """Coerce a scalar to float for arithmetic operations."""
    if val is None or val == "":
        return 0.0
    if isinstance(val, bool):
        return 1.0 if val else 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        if val.startswith("#"):
            raise FormulaEvaluationError(val)
        parsed = parse_literal_value(val)
        if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
            return float(parsed)
    raise FormulaEvaluationError("#VALUE!", f"Cannot convert '{val}' to a number.")


def _to_bool(val: CellScalar) -> bool:
    """Coerce a scalar to boolean for logical functions."""
    if val is None or val == "":
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return abs(float(val)) > 1e-12
    if isinstance(val, str):
        if val.startswith("#"):
            raise FormulaEvaluationError(val)
        up = val.strip().upper()
        if up == "TRUE":
            return True
        if up == "FALSE":
            return False
    raise FormulaEvaluationError("#VALUE!", f"Cannot convert '{val}' to boolean.")


class _RangeValue:
    """Internal wrapper distinguishing a rectangular cell range from a single scalar."""

    def __init__(self, items: list[CellScalar]) -> None:
        self.items = items


def _collect_numeric_args(args: list[CellScalar | _RangeValue]) -> list[float]:
    """Flatten scalar and range arguments into a list of floats (ignoring empty/text in ranges)."""
    numbers: list[float] = []
    for arg in args:
        if isinstance(arg, _RangeValue):
            for cell_val in arg.items:
                if isinstance(cell_val, str) and cell_val.startswith("#"):
                    raise FormulaEvaluationError(cell_val)
                if isinstance(cell_val, (int, float)) and not isinstance(cell_val, bool):
                    numbers.append(float(cell_val))
        else:
            if isinstance(arg, str) and arg.startswith("#"):
                raise FormulaEvaluationError(arg)
            if arg is not None and arg != "":
                numbers.append(_to_number(arg))
    return numbers


def _fn_sum(args: list[CellScalar | _RangeValue]) -> float:
    return sum(_collect_numeric_args(args))


def _fn_average(args: list[CellScalar | _RangeValue]) -> float:
    nums = _collect_numeric_args(args)
    if not nums:
        raise FormulaEvaluationError("#DIV/0!", "AVERAGE requires at least one numeric value.")
    return sum(nums) / float(len(nums))


def _fn_min(args: list[CellScalar | _RangeValue]) -> float:
    nums = _collect_numeric_args(args)
    return min(nums) if nums else 0.0


def _fn_max(args: list[CellScalar | _RangeValue]) -> float:
    nums = _collect_numeric_args(args)
    return max(nums) if nums else 0.0


def _fn_count(args: list[CellScalar | _RangeValue]) -> float:
    return float(len(_collect_numeric_args(args)))


def _fn_counta(args: list[CellScalar | _RangeValue]) -> float:
    count = 0
    for arg in args:
        if isinstance(arg, _RangeValue):
            for item in arg.items:
                if item is not None and item != "":
                    count += 1
        elif arg is not None and arg != "":
            count += 1
    return float(count)


def _fn_median(args: list[CellScalar | _RangeValue]) -> float:
    nums = _collect_numeric_args(args)
    if not nums:
        raise FormulaEvaluationError("#NUM!", "MEDIAN requires at least one numeric value.")
    return float(statistics.median(nums))


def _fn_stdev(args: list[CellScalar | _RangeValue]) -> float:
    nums = _collect_numeric_args(args)
    if len(nums) < 2:
        raise FormulaEvaluationError("#DIV/0!", "STDEV requires at least two numeric values.")
    return float(statistics.stdev(nums))


def _fn_product(args: list[CellScalar | _RangeValue]) -> float:
    nums = _collect_numeric_args(args)
    if not nums:
        return 0.0
    return float(math.prod(nums))


def _fn_if(args: list[CellScalar | _RangeValue]) -> CellScalar:
    if len(args) not in {2, 3}:
        raise FormulaEvaluationError("#VALUE!", "IF expects 2 or 3 arguments.")
    if any(isinstance(a, _RangeValue) for a in args):
        raise FormulaEvaluationError("#VALUE!", "IF does not accept cell ranges directly.")
    cond = _to_bool(args[0])  # type: ignore[arg-type]
    if cond:
        return args[1]  # type: ignore[return-value]
    return args[2] if len(args) == 3 else False  # type: ignore[return-value]


def _fn_round(args: list[CellScalar | _RangeValue]) -> float:
    if len(args) not in {1, 2} or any(isinstance(a, _RangeValue) for a in args):
        raise FormulaEvaluationError("#VALUE!", "ROUND expects 1 or 2 scalar arguments.")
    val = _to_number(args[0])  # type: ignore[arg-type]
    digits = int(round(_to_number(args[1]))) if len(args) == 2 else 0  # type: ignore[arg-type]
    return float(round(val, digits))


def _fn_abs(args: list[CellScalar | _RangeValue]) -> float:
    if len(args) != 1 or isinstance(args[0], _RangeValue):
        raise FormulaEvaluationError("#VALUE!", "ABS expects 1 numeric argument.")
    return abs(_to_number(args[0]))


def _fn_sqrt(args: list[CellScalar | _RangeValue]) -> float:
    if len(args) != 1 or isinstance(args[0], _RangeValue):
        raise FormulaEvaluationError("#VALUE!", "SQRT expects 1 numeric argument.")
    val = _to_number(args[0])
    if val < 0:
        raise FormulaEvaluationError("#NUM!", "SQRT of negative number.")
    return math.sqrt(val)


def _fn_power(args: list[CellScalar | _RangeValue]) -> float:
    if len(args) != 2 or any(isinstance(a, _RangeValue) for a in args):
        raise FormulaEvaluationError("#VALUE!", "POWER expects 2 numeric arguments.")
    base = _to_number(args[0])  # type: ignore[arg-type]
    exp = _to_number(args[1])  # type: ignore[arg-type]
    return float(math.pow(base, exp))


def _fn_mod(args: list[CellScalar | _RangeValue]) -> float:
    if len(args) != 2 or any(isinstance(a, _RangeValue) for a in args):
        raise FormulaEvaluationError("#VALUE!", "MOD expects 2 numeric arguments.")
    num = _to_number(args[0])  # type: ignore[arg-type]
    div = _to_number(args[1])  # type: ignore[arg-type]
    if abs(div) < 1e-15:
        raise FormulaEvaluationError("#DIV/0!", "Division by zero in MOD.")
    return float(num % div)


def _fn_int(args: list[CellScalar | _RangeValue]) -> float:
    if len(args) != 1 or isinstance(args[0], _RangeValue):
        raise FormulaEvaluationError("#VALUE!", "INT expects 1 numeric argument.")
    return float(math.floor(_to_number(args[0])))


def _fn_concat(args: list[CellScalar | _RangeValue]) -> str:
    parts: list[str] = []
    for arg in args:
        if isinstance(arg, _RangeValue):
            for item in arg.items:
                parts.append(format_cell_display(item))
        else:
            parts.append(format_cell_display(arg))
    return "".join(parts)


def _fn_len(args: list[CellScalar | _RangeValue]) -> float:
    if len(args) != 1 or isinstance(args[0], _RangeValue):
        raise FormulaEvaluationError("#VALUE!", "LEN expects 1 scalar argument.")
    return float(len(format_cell_display(args[0])))


def _fn_upper(args: list[CellScalar | _RangeValue]) -> str:
    if len(args) != 1 or isinstance(args[0], _RangeValue):
        raise FormulaEvaluationError("#VALUE!", "UPPER expects 1 scalar argument.")
    return format_cell_display(args[0]).upper()


def _fn_lower(args: list[CellScalar | _RangeValue]) -> str:
    if len(args) != 1 or isinstance(args[0], _RangeValue):
        raise FormulaEvaluationError("#VALUE!", "LOWER expects 1 scalar argument.")
    return format_cell_display(args[0]).lower()


def _fn_trim(args: list[CellScalar | _RangeValue]) -> str:
    if len(args) != 1 or isinstance(args[0], _RangeValue):
        raise FormulaEvaluationError("#VALUE!", "TRIM expects 1 scalar argument.")
    return " ".join(format_cell_display(args[0]).split())


def _fn_and(args: list[CellScalar | _RangeValue]) -> bool:
    if not args:
        raise FormulaEvaluationError("#VALUE!", "AND requires at least 1 argument.")
    for arg in args:
        items = arg.items if isinstance(arg, _RangeValue) else [arg]
        for item in items:
            if item is not None and item != "" and not _to_bool(item):
                return False
    return True


def _fn_or(args: list[CellScalar | _RangeValue]) -> bool:
    if not args:
        raise FormulaEvaluationError("#VALUE!", "OR requires at least 1 argument.")
    for arg in args:
        items = arg.items if isinstance(arg, _RangeValue) else [arg]
        for item in items:
            if item is not None and item != "" and _to_bool(item):
                return True
    return False


def _fn_not(args: list[CellScalar | _RangeValue]) -> bool:
    if len(args) != 1 or isinstance(args[0], _RangeValue):
        raise FormulaEvaluationError("#VALUE!", "NOT expects 1 boolean argument.")
    return not _to_bool(args[0])


_FUNCTIONS: dict[str, Callable[[list[CellScalar | _RangeValue]], CellScalar]] = {
    "SUM": _fn_sum,
    "AVERAGE": _fn_average,
    "AVG": _fn_average,
    "MIN": _fn_min,
    "MAX": _fn_max,
    "COUNT": _fn_count,
    "COUNTA": _fn_counta,
    "MEDIAN": _fn_median,
    "STDEV": _fn_stdev,
    "PRODUCT": _fn_product,
    "IF": _fn_if,
    "ROUND": _fn_round,
    "ABS": _fn_abs,
    "SQRT": _fn_sqrt,
    "POWER": _fn_power,
    "MOD": _fn_mod,
    "INT": _fn_int,
    "CONCAT": _fn_concat,
    "CONCATENATE": _fn_concat,
    "LEN": _fn_len,
    "UPPER": _fn_upper,
    "LOWER": _fn_lower,
    "TRIM": _fn_trim,
    "AND": _fn_and,
    "OR": _fn_or,
    "NOT": _fn_not,
}


class _FormulaParser:
    """Recursive-descent parser for Excel/Sheets formulas."""

    def __init__(
        self,
        tokens: list[_Token],
        cell_resolver: Callable[[CellCoord], CellScalar],
    ) -> None:
        self._tokens = tokens
        self._pos = 0
        self._resolve_cell = cell_resolver

    def _peek(self) -> _Token:
        return self._tokens[self._pos]

    def _consume(self) -> _Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def parse(self) -> CellScalar:
        result = self._parse_comparison(allow_range=False)
        if isinstance(result, _RangeValue):
            raise FormulaEvaluationError("#VALUE!", "A cell range cannot be used as a standalone value.")
        if self._peek().kind != "EOF":
            raise FormulaEvaluationError("#ERROR!", f"Unexpected token '{self._peek().value}'.")
        return result

    def _parse_comparison(self, allow_range: bool) -> CellScalar | _RangeValue:
        left = self._parse_additive(allow_range=allow_range)
        comp_ops = {"=", "==", "<>", "!=", "<", "<=", ">", ">="}
        while self._peek().kind == "OP" and self._peek().value in comp_ops:
            op = self._consume().value
            right = self._parse_additive(allow_range=False)
            if isinstance(left, _RangeValue) or isinstance(right, _RangeValue):
                raise FormulaEvaluationError("#VALUE!", "Cannot compare cell ranges directly.")
            left = self._apply_comparison(op, left, right)
        return left

    @staticmethod
    def _apply_comparison(op: str, left: CellScalar, right: CellScalar) -> bool:
        if isinstance(left, str) and left.startswith("#"):
            raise FormulaEvaluationError(left)
        if isinstance(right, str) and right.startswith("#"):
            raise FormulaEvaluationError(right)

        # Normalize None to 0 or "" matching other operand
        if left is None and isinstance(right, (int, float)):
            left = 0.0
        elif right is None and isinstance(left, (int, float)):
            right = 0.0
        elif left is None and isinstance(right, str):
            left = ""
        elif right is None and isinstance(left, str):
            right = ""

        if op in {"=", "=="}:
            if isinstance(left, str) and isinstance(right, str):
                return left.lower() == right.lower()
            return left == right
        if op in {"<>", "!="}:
            if isinstance(left, str) and isinstance(right, str):
                return left.lower() != right.lower()
            return left != right

        l_num = _to_number(left)
        r_num = _to_number(right)
        if op == "<":
            return l_num < r_num
        if op == "<=":
            return l_num <= r_num
        if op == ">":
            return l_num > r_num
        return l_num >= r_num

    def _parse_additive(self, allow_range: bool) -> CellScalar | _RangeValue:
        left = self._parse_multiplicative(allow_range=allow_range)
        while self._peek().kind == "OP" and self._peek().value in {"+", "-"}:
            op = self._consume().value
            right = self._parse_multiplicative(allow_range=False)
            if isinstance(left, _RangeValue) or isinstance(right, _RangeValue):
                raise FormulaEvaluationError("#VALUE!", "Cannot perform arithmetic on ranges.")
            l_val = _to_number(left)
            r_val = _to_number(right)
            left = (l_val + r_val) if op == "+" else (l_val - r_val)
        return left

    def _parse_multiplicative(self, allow_range: bool) -> CellScalar | _RangeValue:
        left = self._parse_power(allow_range=allow_range)
        while self._peek().kind == "OP" and self._peek().value in {"*", "/"}:
            op = self._consume().value
            right = self._parse_power(allow_range=False)
            if isinstance(left, _RangeValue) or isinstance(right, _RangeValue):
                raise FormulaEvaluationError("#VALUE!", "Cannot perform arithmetic on ranges.")
            l_val = _to_number(left)
            r_val = _to_number(right)
            if op == "*":
                left = l_val * r_val
            else:
                if abs(r_val) < 1e-15:
                    raise FormulaEvaluationError("#DIV/0!", "Division by zero.")
                left = l_val / r_val
        return left

    def _parse_power(self, allow_range: bool) -> CellScalar | _RangeValue:
        base = self._parse_unary(allow_range=allow_range)
        if self._peek().kind == "OP" and self._peek().value == "^":
            self._consume()
            exp = self._parse_power(allow_range=False)
            if isinstance(base, _RangeValue) or isinstance(exp, _RangeValue):
                raise FormulaEvaluationError("#VALUE!", "Cannot exponentiate a range.")
            return float(math.pow(_to_number(base), _to_number(exp)))
        return base

    def _parse_unary(self, allow_range: bool) -> CellScalar | _RangeValue:
        if self._peek().kind == "OP" and self._peek().value in {"+", "-"}:
            op = self._consume().value
            operand = self._parse_unary(allow_range=False)
            if isinstance(operand, _RangeValue):
                raise FormulaEvaluationError("#VALUE!", "Cannot negate a cell range.")
            num = _to_number(operand)
            return -num if op == "-" else num
        return self._parse_postfix(allow_range=allow_range)

    def _parse_postfix(self, allow_range: bool) -> CellScalar | _RangeValue:
        primary = self._parse_primary(allow_range=allow_range)
        while self._peek().kind == "OP" and self._peek().value == "%":
            self._consume()
            if isinstance(primary, _RangeValue):
                raise FormulaEvaluationError("#VALUE!", "Cannot apply '%' to a range.")
            primary = _to_number(primary) / 100.0
        return primary

    def _parse_primary(self, allow_range: bool) -> CellScalar | _RangeValue:
        tok = self._peek()

        if tok.kind == "NUMBER":
            self._consume()
            return float(tok.value)

        if tok.kind == "STRING":
            self._consume()
            return tok.value[1:-1].replace('""', '"')

        if tok.kind == "CELL_RANGE":
            self._consume()
            if not allow_range:
                raise FormulaEvaluationError("#VALUE!", f"Range '{tok.value}' is only valid inside a function.")
            left_ref, right_ref = tok.value.split(":", 1)
            coords = expand_range(left_ref.strip(), right_ref.strip())
            return _RangeValue([self._resolve_cell(coord) for coord in coords])

        if tok.kind == "CELL_REF":
            self._consume()
            coord = parse_cell_ref(tok.value)
            return self._resolve_cell(coord)

        if tok.kind == "IDENT":
            self._consume()
            upper_name = tok.value.upper()
            if upper_name == "TRUE":
                return True
            if upper_name == "FALSE":
                return False

            if self._peek().kind == "OP" and self._peek().value == "(":
                self._consume()  # consume '('
                fn = _FUNCTIONS.get(upper_name)
                if fn is None:
                    raise FormulaEvaluationError("#NAME?", f"Unknown function '{upper_name}'.")
                args: list[CellScalar | _RangeValue] = []
                if not (self._peek().kind == "OP" and self._peek().value == ")"):
                    while True:
                        args.append(self._parse_comparison(allow_range=True))
                        if self._peek().kind == "OP" and self._peek().value in {",", ";"}:
                            self._consume()
                            continue
                        break
                if not (self._peek().kind == "OP" and self._peek().value == ")"):
                    raise FormulaEvaluationError("#ERROR!", f"Missing ')' in call to {upper_name}.")
                self._consume()  # consume ')'
                return fn(args)

            raise FormulaEvaluationError("#NAME?", f"Unknown identifier '{tok.value}'.")

        if tok.kind == "OP" and tok.value == "(":
            self._consume()
            inner = self._parse_comparison(allow_range=False)
            if not (self._peek().kind == "OP" and self._peek().value == ")"):
                raise FormulaEvaluationError("#ERROR!", "Mismatched parentheses.")
            self._consume()
            return inner

        raise FormulaEvaluationError("#ERROR!", f"Unexpected token '{tok.value or 'end of formula'}'.")


def evaluate_cell_input(
    raw_input: str,
    cell_resolver: Callable[[CellCoord], CellScalar],
) -> CellScalar:
    """Evaluate a raw cell string (formula starting with '=' or literal value)."""
    if not raw_input.strip().startswith("="):
        return parse_literal_value(raw_input)

    formula_body = raw_input.strip()[1:].strip()
    if not formula_body:
        return ""

    try:
        tokens = _tokenize_formula(formula_body)
        parser = _FormulaParser(tokens=tokens, cell_resolver=cell_resolver)
        return parser.parse()
    except FormulaEvaluationError as exc:
        return exc.code
    except Exception:
        return "#ERROR!"