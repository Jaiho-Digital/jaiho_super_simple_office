"""Safe AST-based scientific and standard math evaluation engine for SimpleCalculator."""

from __future__ import annotations

import ast
import io
import math
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class AngleMode(StrEnum):
    """Angular unit mode for trigonometric operations."""

    DEG = "DEG"
    RAD = "RAD"


class CalculatorError(Exception):
    """Raised when an expression has invalid syntax, domain errors, or overflow."""


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Structured output of a single calculation."""

    expression: str
    normalized_expression: str
    value: float
    formatted: str
    angle_mode: AngleMode


_MAX_EXPRESSION_LENGTH = 512
_MAX_FACTORIAL_INPUT = 170  # 170! is the largest factorial representable in IEEE-754 float64
_MAX_EXPONENT_MAGNITUDE = 10_000.0


def format_number(value: float, max_sig_figs: int = 12) -> str:
    """Format a floating-point number cleanly without binary representation artifacts."""
    if math.isnan(value):
        raise CalculatorError("Result is undefined (NaN).")
    if math.isinf(value):
        raise CalculatorError("Result exceeds maximum numerical range (Infinity).")

    # Normalize negative zero (-0.0 -> 0)
    if abs(value) < 1e-15:
        return "0"

    # Format with controlled significant digits to eliminate 0.30000000000000004 noise
    formatted = f"{value:.{max_sig_figs}g}"

    # Expand modest scientific notation into readable decimals where appropriate
    if "e" not in formatted and "E" not in formatted:
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

    return formatted.replace("e+", "e")


def _clean_trig_zero(result: float) -> float:
    """Snap near-zero trigonometric artifacts (e.g., sin(pi)) to exact 0.0."""
    return 0.0 if abs(result) < 1e-14 else result


def _safe_factorial(x: float) -> float:
    """Compute n! for non-negative integers within safe float64 bounds."""
    if not math.isfinite(x) or x < 0 or not float(x).is_integer():
        raise CalculatorError("Factorial is only defined for non-negative integers.")
    n = int(x)
    if n > _MAX_FACTORIAL_INPUT:
        raise CalculatorError(f"Factorial input exceeds maximum limit ({_MAX_FACTORIAL_INPUT}).")
    return float(math.factorial(n))


def _safe_sqrt(x: float) -> float:
    if x < 0:
        raise CalculatorError("Square root of a negative number is undefined in real numbers.")
    return math.sqrt(x)


def _safe_cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


def _safe_ln(x: float) -> float:
    if x <= 0:
        raise CalculatorError("Natural logarithm (ln) requires a positive number.")
    return math.log(x)


def _safe_log10(x: float) -> float:
    if x <= 0:
        raise CalculatorError("Logarithm (log) requires a positive number.")
    return math.log10(x)


def _safe_log2(x: float) -> float:
    if x <= 0:
        raise CalculatorError("Base-2 logarithm (log2) requires a positive number.")
    return math.log2(x)


def _safe_exp(x: float) -> float:
    if x > 709.78:
        raise CalculatorError("Exponential result exceeds maximum numerical range.")
    return math.exp(x)


def _safe_pow(base: float, exponent: float) -> float:
    if abs(base) > 1.0 and abs(exponent) > _MAX_EXPONENT_MAGNITUDE:
        raise CalculatorError("Exponent is too large to compute safely.")
    if base == 0.0 and exponent < 0.0:
        raise CalculatorError("Division by zero (zero raised to a negative power).")
    if base < 0.0 and not float(exponent).is_integer():
        raise CalculatorError("Negative base raised to a fractional power is undefined.")
    try:
        result = math.pow(base, exponent)
    except OverflowError as exc:
        raise CalculatorError("Result exceeds maximum numerical range.") from exc
    except ValueError as exc:
        raise CalculatorError("Invalid exponentiation domain.") from exc
    if not math.isfinite(result):
        raise CalculatorError("Result exceeds maximum numerical range.")
    return result


class MathEngine:
    """Stateless-capable safe math expression parser and evaluator."""

    CONSTANTS: frozenset[str] = frozenset({"pi", "e", "tau", "ans"})

    def __init__(self) -> None:
        self.angle_mode: AngleMode = AngleMode.DEG
        self.last_answer: float = 0.0

    def _build_functions(self) -> dict[str, tuple[int, Callable[..., float]]]:
        """Return mapping of function_name -> (expected_arg_count, callable)."""
        return {
            "sin": (1, self._trig_sin),
            "cos": (1, self._trig_cos),
            "tan": (1, self._trig_tan),
            "asin": (1, self._trig_asin),
            "acos": (1, self._trig_acos),
            "atan": (1, self._trig_atan),
            "sinh": (1, math.sinh),
            "cosh": (1, math.cosh),
            "tanh": (1, math.tanh),
            "sqrt": (1, _safe_sqrt),
            "cbrt": (1, _safe_cbrt),
            "ln": (1, _safe_ln),
            "log": (1, _safe_log10),
            "log10": (1, _safe_log10),
            "log2": (1, _safe_log2),
            "exp": (1, _safe_exp),
            "abs": (1, abs),
            "floor": (1, lambda x: float(math.floor(x))),
            "ceil": (1, lambda x: float(math.ceil(x))),
            "round": (1, lambda x: float(round(x))),
            "fact": (1, _safe_factorial),
            "pow": (2, _safe_pow),
        }

    # ------------------------------------------------------------------
    # Angle-Aware Trigonometric Functions
    # ------------------------------------------------------------------

    def _trig_sin(self, x: float) -> float:
        rad = math.radians(x) if self.angle_mode == AngleMode.DEG else x
        return _clean_trig_zero(math.sin(rad))

    def _trig_cos(self, x: float) -> float:
        rad = math.radians(x) if self.angle_mode == AngleMode.DEG else x
        return _clean_trig_zero(math.cos(rad))

    def _trig_tan(self, x: float) -> float:
        if self.angle_mode == AngleMode.DEG:
            mod = abs(x) % 180.0
            if math.isclose(mod, 90.0, abs_tol=1e-12):
                raise CalculatorError("Tangent is undefined at odd multiples of 90°.")
            rad = math.radians(x)
        else:
            half_pi = math.pi / 2.0
            if math.isclose((abs(x) - half_pi) % math.pi, 0.0, abs_tol=1e-12):
                raise CalculatorError("Tangent is undefined at odd multiples of π/2.")
            rad = x
        return _clean_trig_zero(math.tan(rad))

    def _trig_asin(self, x: float) -> float:
        if x < -1.0 or x > 1.0:
            raise CalculatorError("Inverse sine (asin) input must be between -1 and 1.")
        rad = math.asin(x)
        return math.degrees(rad) if self.angle_mode == AngleMode.DEG else rad

    def _trig_acos(self, x: float) -> float:
        if x < -1.0 or x > 1.0:
            raise CalculatorError("Inverse cosine (acos) input must be between -1 and 1.")
        rad = math.acos(x)
        return math.degrees(rad) if self.angle_mode == AngleMode.DEG else rad

    def _trig_atan(self, x: float) -> float:
        rad = math.atan(x)
        return math.degrees(rad) if self.angle_mode == AngleMode.DEG else rad

    # ------------------------------------------------------------------
    # Normalization & Token Preprocessing
    # ------------------------------------------------------------------

    def normalize_expression(self, raw_expression: str) -> str:
        """Convert display math notation into valid Python AST expression syntax."""
        expr = raw_expression.strip()
        if not expr:
            raise CalculatorError("Enter an expression to calculate.")
        if len(expr) > _MAX_EXPRESSION_LENGTH:
            raise CalculatorError(
                f"Expression is too long (maximum {_MAX_EXPRESSION_LENGTH} characters)."
            )

        # Normalize visual unicode operators and constants
        replacements = (
            ("×", "*"),
            ("÷", "/"),
            ("−", "-"),
            ("–", "-"),
            ("π", "pi"),
            ("τ", "tau"),
            ("^", "**"),
        )
        for old, new in replacements:
            expr = expr.replace(old, new)

        # Expand √(...) or √number into sqrt(...)
        expr = self._expand_square_roots(expr)

        # Expand postfix factorials (e.g., 5! -> fact(5), (2+3)! -> fact((2+3)))
        expr = self._expand_postfix_factorials(expr)

        # Expand postfix percentages (e.g., 50% -> (50 * 0.01)) while keeping modulo (10 % 3)
        expr = self._expand_percentages(expr)

        # Insert explicit '*' for implicit multiplication (e.g., 2pi -> 2 * pi, 3(4+5) -> 3 * (4+5))
        expr = self._insert_implicit_multiplication(expr)
        return expr

    @staticmethod
    def _expand_square_roots(expr: str) -> str:
        """Rewrite '√(x)' and '√9' into 'sqrt(x)' and 'sqrt(9)'."""
        out: list[str] = []
        i = 0
        n = len(expr)
        while i < n:
            if expr[i] == "√":
                j = i + 1
                while j < n and expr[j].isspace():
                    j += 1
                if j < n and expr[j] == "(":
                    out.append("sqrt")
                    i = j
                    continue
                # Consume number or constant identifier following √
                start = j
                while j < n and (expr[j].isalnum() or expr[j] == "."):
                    j += 1
                if j > start:
                    out.append(f"sqrt({expr[start:j]})")
                    i = j
                    continue
                raise CalculatorError("Invalid square root syntax after '√'.")
            out.append(expr[i])
            i += 1
        return "".join(out)

    @staticmethod
    def _expand_postfix_factorials(expr: str) -> str:
        """Rewrite postfix '!' into 'fact(...)' calls."""
        text = expr
        while "!" in text:
            bang_idx = text.find("!")
            if bang_idx == 0:
                raise CalculatorError("Factorial operator '!' requires a preceding number or group.")

            left = bang_idx - 1
            while left >= 0 and text[left].isspace():
                left -= 1
            if left < 0:
                raise CalculatorError("Factorial operator '!' requires a preceding value.")

            if text[left] == ")":
                depth = 1
                start = left - 1
                while start >= 0 and depth > 0:
                    if text[start] == ")":
                        depth += 1
                    elif text[start] == "(":
                        depth -= 1
                    start -= 1
                if depth != 0:
                    raise CalculatorError("Unmatched closing parenthesis before '!'.")
                operand_start = start + 1
                # Include preceding function name if present (e.g., sqrt(9)!)
                while operand_start > 0 and (
                    text[operand_start - 1].isalnum() or text[operand_start - 1] == "_"
                ):
                    operand_start -= 1
            else:
                operand_start = left
                while operand_start > 0 and (
                    text[operand_start - 1].isalnum() or text[operand_start - 1] in {".", "_"}
                ):
                    operand_start -= 1

            operand = text[operand_start : left + 1]
            if not operand:
                raise CalculatorError("Invalid operand before '!'.")
            text = f"{text[:operand_start]}fact({operand}){text[bang_idx + 1:]}"
        return text

    @staticmethod
    def _expand_percentages(expr: str) -> str:
        """Convert postfix '%' (e.g., '25%') into '(25 * 0.01)' while preserving modulo 'a % b'."""
        text = expr
        search_from = 0
        while True:
            pct_idx = text.find("%", search_from)
            if pct_idx == -1:
                break

            # Look ahead past whitespace: if followed by an operand start, treat as modulo
            right = pct_idx + 1
            while right < len(text) and text[right].isspace():
                right += 1
            if right < len(text) and (
                text[right].isalnum() or text[right] in {"(", "."}
            ):
                search_from = pct_idx + 1
                continue

            left = pct_idx - 1
            while left >= 0 and text[left].isspace():
                left -= 1
            if left < 0:
                raise CalculatorError("Percent operator '%' requires a preceding value.")

            if text[left] == ")":
                depth = 1
                start = left - 1
                while start >= 0 and depth > 0:
                    if text[start] == ")":
                        depth += 1
                    elif text[start] == "(":
                        depth -= 1
                    start -= 1
                if depth != 0:
                    raise CalculatorError("Unmatched parenthesis before '%'.")
                operand_start = start + 1
            else:
                operand_start = left
                while operand_start > 0 and (
                    text[operand_start - 1].isalnum() or text[operand_start - 1] in {".", "_"}
                ):
                    operand_start -= 1

            operand = text[operand_start : left + 1]
            if not operand:
                raise CalculatorError("Invalid operand before '%'.")
            replacement = f"({operand} * 0.01)"
            text = f"{text[:operand_start]}{replacement}{text[pct_idx + 1:]}"
            search_from = operand_start + len(replacement)
        return text

    def _insert_implicit_multiplication(self, expr: str) -> str:
        """Insert '*' between adjacent values/groups such as '2pi', '3(4+5)', or '(1+2)(3+4)'."""
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(expr).readline))
        except tokenize.TokenError as exc:
            raise CalculatorError("Incomplete expression or unmatched parenthesis.") from exc

        significant = [
            tok
            for tok in tokens
            if tok.type not in {tokenize.ENCODING, tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER}
        ]
        if not significant:
            raise CalculatorError("Enter an expression to calculate.")

        parts: list[str] = []
        for idx, tok in enumerate(significant):
            if idx > 0:
                prev = significant[idx - 1]
                prev_is_value = (
                    prev.type == tokenize.NUMBER
                    or (prev.type == tokenize.OP and prev.string == ")")
                    or (prev.type == tokenize.NAME and prev.string.lower() in self.CONSTANTS)
                )
                curr_starts_value = (
                    tok.type == tokenize.NUMBER
                    or (tok.type == tokenize.OP and tok.string == "(")
                    or tok.type == tokenize.NAME
                )
                if prev_is_value and curr_starts_value:
                    parts.append("*")
            parts.append(tok.string)

        return " ".join(parts)

    # ------------------------------------------------------------------
    # AST Parsing & Restricted Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, raw_expression: str, update_ans: bool = True) -> EvaluationResult:
        """Safely parse and evaluate `raw_expression` without Python eval()."""
        normalized = self.normalize_expression(raw_expression)

        try:
            tree = ast.parse(normalized, mode="eval")
        except SyntaxError as exc:
            raise CalculatorError("Invalid mathematical syntax. Check operators and parentheses.") from exc

        functions = self._build_functions()
        try:
            raw_value = self._eval_node(tree.body, functions)
        except RecursionError as exc:
            raise CalculatorError("Expression nesting depth is too deep.") from exc

        value = float(raw_value)
        formatted = format_number(value)

        if update_ans:
            self.last_answer = value

        return EvaluationResult(
            expression=raw_expression.strip(),
            normalized_expression=normalized,
            value=value,
            formatted=formatted,
            angle_mode=self.angle_mode,
        )

    def _eval_node(
        self,
        node: ast.AST,
        functions: dict[str, tuple[int, Callable[..., float]]],
    ) -> float:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise CalculatorError("Only numeric values are permitted.")
            return float(node.value)

        if isinstance(node, ast.Name):
            ident = node.id.lower()
            if ident == "pi":
                return math.pi
            if ident == "e":
                return math.e
            if ident == "tau":
                return math.tau
            if ident == "ans":
                return self.last_answer
            raise CalculatorError(f"Unknown symbol or function '{node.id}'.")

        if isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand, functions)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise CalculatorError("Unsupported unary operator.")

        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left, functions)
            right = self._eval_node(node.right, functions)
            return self._eval_binary_op(node.op, left, right)

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise CalculatorError("Invalid function call.")
            if node.keywords:
                raise CalculatorError("Keyword arguments are not supported.")

            func_name = node.func.id.lower()
            if func_name not in functions:
                raise CalculatorError(f"Unknown function '{node.func.id}'.")

            expected_argc, fn = functions[func_name]
            if len(node.args) != expected_argc:
                plural = "argument" if expected_argc == 1 else "arguments"
                raise CalculatorError(
                    f"Function '{func_name}()' expects {expected_argc} {plural}, got {len(node.args)}."
                )

            evaluated_args = [self._eval_node(arg, functions) for arg in node.args]
            try:
                return float(fn(*evaluated_args))
            except CalculatorError:
                raise
            except ValueError as exc:
                raise CalculatorError(f"Math domain error in '{func_name}()'.") from exc
            except OverflowError as exc:
                raise CalculatorError(f"Numeric overflow in '{func_name}()'.") from exc

        raise CalculatorError("Unsupported expression construct.")

    @staticmethod
    def _eval_binary_op(op: ast.operator, left: float, right: float) -> float:
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            if right == 0.0:
                raise CalculatorError("Cannot divide by zero.")
            return left / right
        if isinstance(op, ast.FloorDiv):
            if right == 0.0:
                raise CalculatorError("Cannot divide by zero.")
            return float(math.floor(left / right))
        if isinstance(op, ast.Mod):
            if right == 0.0:
                raise CalculatorError("Cannot compute modulo by zero.")
            return left % right
        if isinstance(op, ast.Pow):
            return _safe_pow(left, right)
        raise CalculatorError("Unsupported binary operator.")