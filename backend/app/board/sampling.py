"""Safe, sandboxed math-expression evaluator used by ``board_plot_function``.

The tutor model may ask the backend to plot a function. We must never run model
output through :func:`eval`/:func:`exec` or any interpreter, so this module
implements a tiny expression language:

    expression := term (('+'|'-') term)*
    term       := power (('*'|'/') power)*
    power      := unary ('^' power)?          # right-associative
    unary      := ('+'|'-')* postfix
    postfix    := primary
    primary    := NUMBER | CONSTANT | function '(' expression ')'
                | '(' expression ')' | IDENTIFIER          # x = variable

Supported constants: ``x`` (the independent variable), ``pi``, ``e``, ``tau``.
Supported functions: ``sin cos tan asin acos atan sinh cosh tanh sqrt exp ln log
log10 log2 abs floor ceil round sign min max pow`` (``log`` is natural log).

Safety properties:
* no names/attributes/subscripts/calls beyond the whitelist above;
* tokenizer rejects every character outside the grammar (quotes, ``;``, ``_``,
  ``$``, backticks, unicode identifiers, ...);
* results must be finite; ``inf``/``nan`` (0/0, log(-1), sqrt(-1), overflow,
  division by zero) raise :class:`ExpressionError` instead of leaking into the
  board payload;
* bounded output: at most :data:`MAX_SAMPLES` (400) samples.

The public API used by the registry:

``evaluate_expression(expr, x=...) -> float``
``sample_function(expr, x_min, x_max, samples=...) -> list[list[float]]``
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

MAX_SAMPLES = 400
MIN_SAMPLES = 2
DEFAULT_SAMPLES = 160
MAX_EXPRESSION_LENGTH = 200
MAX_DEPTH = 40

#: Absolute magnitude allowed for x / y samples; larger values are rejected as
#: out-of-range instead of being clipped silently.
MAX_ABS_COORD = 1_000_000.0
#: Minimum output resolution: fewer usable points than this is an error.
MIN_USABLE_SAMPLES = 2

_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

_FUNCTIONS: dict[str, object] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "sqrt": math.sqrt,
    "exp": math.exp,
    "ln": math.log,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "sign": lambda value: float((value > 0) - (value < 0)),
    "min": min,
    "max": max,
    "pow": math.pow,
}

#: Names that can never be called with one argument.
_TWO_ARG_FUNCTIONS = frozenset({"min", "max", "pow"})

_TOKEN_RE = re.compile(
    r"""
    (?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<ident>[A-Za-z][A-Za-z0-9]*)
  | (?P<op>[+\-*/^(),])
    """,
    re.VERBOSE,
)

_ALLOWED_CHARS = re.compile(r"^[0-9A-Za-z_+\-*/^(),.\s]*$")


class ExpressionError(ValueError):
    """Raised when an expression is empty, malformed, unsafe or non-finite."""

    def __init__(self, message: str, *, code: str = "invalid_expression") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Token:
    kind: str  # number | ident | op | end
    value: str
    position: int


def tokenize(expression: str) -> list[Token]:
    """Split ``expression`` into tokens, rejecting anything not in the grammar."""
    if not isinstance(expression, str):
        raise ExpressionError("expression must be a string")
    expression = expression.strip()
    if not expression:
        raise ExpressionError("expression must not be empty")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError(f"expression is too long (max {MAX_EXPRESSION_LENGTH} chars)")
    if "_" in expression or "`" in expression or "$" in expression:
        raise ExpressionError("expression contains a disallowed character")
    if not _ALLOWED_CHARS.match(expression):
        raise ExpressionError("expression contains a disallowed character")

    tokens: list[Token] = []
    position = 0
    length = len(expression)
    while position < length:
        char = expression[position]
        if char.isspace():
            position += 1
            continue
        match = _TOKEN_RE.match(expression, position)
        if not match:
            raise ExpressionError(f"unexpected character {char!r} at position {position}")
        kind = match.lastgroup or ""
        value = match.group()
        tokens.append(Token(kind=kind, value=value, position=position))
        position = match.end()
    tokens.append(Token(kind="end", value="", position=length))
    return tokens


class _Parser:
    """Recursive-descent parser producing a float for a given ``x``."""

    def __init__(self, expression: str) -> None:
        self.expression = expression
        self.tokens = tokenize(expression)
        self.index = 0
        self.depth = 0
        self.variable_used = False

    # -- token helpers -----------------------------------------------------
    def _peek(self) -> Token:
        return self.tokens[self.index]

    def _take(self) -> Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _expect(self, value: str) -> Token:
        token = self._peek()
        if token.value != value:
            raise ExpressionError(
                f"expected {value!r} at position {token.position} in expression"
            )
        return self._take()

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError("expression is nested too deeply")

    def _leave(self) -> None:
        self.depth -= 1

    # -- grammar -----------------------------------------------------------
    def parse(self, x: float) -> float:
        value = self._expression(x)
        token = self._peek()
        if token.kind != "end":
            raise ExpressionError(
                f"unexpected token {token.value!r} at position {token.position}"
            )
        if not math.isfinite(value):
            raise ExpressionError("expression produced a non-finite value")
        return value

    def _expression(self, x: float) -> float:
        self._enter()
        try:
            value = self._term(x)
            while True:
                token = self._peek()
                if token.kind == "op" and token.value in {"+", "-"}:
                    self._take()
                    right = self._term(x)
                    value = value + right if token.value == "+" else value - right
                else:
                    return value
        finally:
            self._leave()

    def _term(self, x: float) -> float:
        value = self._power(x)
        while True:
            token = self._peek()
            if token.kind == "op" and token.value in {"*", "/"}:
                self._take()
                right = self._power(x)
                if token.value == "*":
                    value = value * right
                else:
                    if right == 0:
                        raise ExpressionError("division by zero in expression")
                    value = value / right
            else:
                return value

    def _power(self, x: float) -> float:
        value = self._unary(x)
        token = self._peek()
        if token.kind == "op" and token.value == "^":
            self._take()
            exponent = self._power(x)  # right-associative
            try:
                value = math.pow(value, exponent)
            except (OverflowError, ValueError) as exc:
                raise ExpressionError(f"invalid exponentiation in expression: {exc}") from None
        return value

    def _unary(self, x: float) -> float:
        token = self._peek()
        if token.kind == "op" and token.value in {"+", "-"}:
            self._take()
            value = self._unary(x)
            return -value if token.value == "-" else value
        return self._primary(x)

    def _primary(self, x: float) -> float:
        token = self._peek()
        if token.kind == "number":
            self._take()
            try:
                return float(token.value)
            except ValueError:  # pragma: no cover - regex guarantees a float
                raise ExpressionError("invalid number in expression") from None
        if token.kind == "op" and token.value == "(":
            self._take()
            value = self._expression(x)
            self._expect(")")
            return value
        if token.kind == "ident":
            name = self._take().value.lower()
            if name == "x":
                self.variable_used = True
                return x
            if name in _CONSTANTS:
                return _CONSTANTS[name]
            if name in _FUNCTIONS:
                return self._call(name, x)
            raise ExpressionError(f"unknown identifier {name!r} in expression")
        raise ExpressionError(
            f"unexpected token {token.value!r} at position {token.position} in expression"
        )

    def _call(self, name: str, x: float) -> float:
        self._expect("(")
        function = _FUNCTIONS[name]
        if name in _TWO_ARG_FUNCTIONS or name == "round":
            first = self._expression(x)
            self._expect(",")
            second = self._expression(x)
            self._expect(")")
            args: tuple[float, ...] = (first, second)
        else:
            argument = self._expression(x)
            self._expect(")")
            args = (argument,)
        try:
            result = float(function(*args))  # type: ignore[operator]
        except (ValueError, OverflowError, ZeroDivisionError, TypeError) as exc:
            raise ExpressionError(f"{name}() is undefined for that input: {exc}") from None
        if not math.isfinite(result):
            raise ExpressionError(f"{name}() produced a non-finite value")
        return result


def _evaluate(expression: str, x_value: float) -> float:
    parser = _Parser(expression)
    try:
        result = parser.parse(x_value)
    except RecursionError:  # pragma: no cover - MAX_DEPTH guards pathological input
        raise ExpressionError("expression is nested too deeply") from None
    if not math.isfinite(result):
        raise ExpressionError("expression produced a non-finite value")
    return result


def evaluate_expression(expression: str, x: float = 0.0) -> float:
    """Evaluate ``expression`` at ``x`` in a sandboxed interpreter."""
    if not math.isfinite(float(x)):
        raise ExpressionError("x must be finite")
    return _evaluate(expression, float(x))


def samples_for(expression: str) -> int:
    """Pick a sample count proportional to expression complexity.

    Deterministic and bounded by :data:`MAX_SAMPLES`; used when the caller does
    not request a specific count.
    """
    length = len(expression.strip())
    if length <= 12:
        return 120
    if length <= 40:
        return 180
    if length <= 90:
        return 240
    return MAX_SAMPLES


def sample_function(
    expression: str,
    x_min: float,
    x_max: float,
    *,
    samples: int | None = None,
) -> list[list[float]]:
    """Sample ``y = f(x)`` over ``[x_min, x_max]`` for the board.

    Returns ``[[x, y], ...]`` ordered by ascending x with finite, in-range
    values only (gaps in the domain are skipped, e.g. ``sqrt(x)`` for x<0).
    Raises :class:`ExpressionError` when the expression is invalid or when no
    usable samples remain.
    """
    for name, value in (("x_min", x_min), ("x_max", x_max)):
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ExpressionError(f"{name} must be a finite number")
        if abs(float(value)) > MAX_ABS_COORD:
            raise ExpressionError(f"{name} is out of range")
    x_min = float(x_min)
    x_max = float(x_max)
    if x_min >= x_max:
        raise ExpressionError("x_min must be less than x_max")
    if x_max - x_min < 1e-9:
        raise ExpressionError("x range is too small to plot")

    count = samples if samples is not None else samples_for(expression)
    if not isinstance(count, int) or isinstance(count, bool):
        raise ExpressionError("samples must be an integer")
    count = max(MIN_SAMPLES, min(MAX_SAMPLES, count))
    # Evaluate the grammar once before sampling so malformed expressions fail
    # fast with a clear error (and without 400 parse failures).
    _evaluate(expression, x_min)

    step = (x_max - x_min) / (count - 1)
    result: list[list[float]] = []
    for index in range(count):
        x_value = x_min + step * index
        if index == count - 1:  # kill floating-point drift on the last sample
            x_value = x_max
        try:
            y_value = _evaluate(expression, x_value)
        except ExpressionError:
            # Undefined point (domain gap / pole): skip it, the client renders a
            # broken polyline across the gap.
            continue
        if not math.isfinite(y_value) or abs(y_value) > MAX_ABS_COORD:
            continue
        if not math.isfinite(x_value) or abs(x_value) > MAX_ABS_COORD:
            continue
        result.append([round(x_value, 6), round(y_value, 6)])

    if len(result) < MIN_USABLE_SAMPLES:
        raise ExpressionError(
            "expression has no plottable values over the requested x range",
            code="expression_out_of_domain",
        )
    return result


__all__ = [
    "DEFAULT_SAMPLES",
    "ExpressionError",
    "MAX_SAMPLES",
    "sample_function",
    "samples_for",
    "evaluate_expression",
    "tokenize",
]
