"""Unit expressions: symbols, ``*``, ``/``, parentheses, integer powers, literal 1.

A hand-written recursive-descent parser over a strict token set; no eval, no numeric literals
other than ``1`` (identity) and integer exponents. Hostile input fails with PARSE_ERROR/limits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from qorvexa.industrial.units.dimension import MAX_EXPONENT, Dimension, UnitError
from qorvexa.industrial.units.registry import UnitRegistry, check_size

MAX_LENGTH: Final = 200
MAX_TOKENS: Final = 64
MAX_DEPTH: Final = 8
_TOKEN = re.compile(r"\s*(?:(\*\*|\^|[*/()])|(-?\d+)|([A-Za-z_°][A-Za-z0-9_°]*))")


@dataclass(frozen=True)
class Unit:
    """Resolved unit: atoms (canonical symbol, exponent), exact dimension and exact scale."""

    atoms: tuple[tuple[str, int], ...]
    dimension: Dimension
    scale: Fraction

    @property
    def symbol(self) -> str:
        num = [f"{s}^{e}" if e != 1 else s for s, e in self.atoms if e > 0]
        den = [f"{s}^{-e}" if e != -1 else s for s, e in self.atoms if e < 0]
        top = "*".join(num) or "1"
        if not den:
            return top
        return f"{top}/{den[0]}" if len(den) == 1 else f"{top}/({'*'.join(den)})"

    def mul(self, other: Unit) -> Unit:
        merged = dict(self.atoms)
        for s, e in other.atoms:
            merged[s] = merged.get(s, 0) + e
        return Unit(
            _atoms(merged),
            self.dimension.mul(other.dimension),
            check_size(self.scale * other.scale),
        )

    def pow(self, n: int) -> Unit:
        if abs(n) > MAX_EXPONENT:
            raise UnitError("EXPONENT_OUT_OF_RANGE", f"exponent beyond ±{MAX_EXPONENT}")
        if n == 0:
            return ONE
        scale = self.scale**n
        return Unit(
            _atoms({s: e * n for s, e in self.atoms}), self.dimension.pow(n), check_size(scale)
        )

    def inverse(self) -> Unit:
        return self.pow(-1)

    def div(self, other: Unit) -> Unit:
        return self.mul(other.inverse())


def _atoms(m: dict[str, int]) -> tuple[tuple[str, int], ...]:
    for e in m.values():
        if abs(e) > MAX_EXPONENT:
            raise UnitError("EXPONENT_OUT_OF_RANGE", f"exponent beyond ±{MAX_EXPONENT}")
    return tuple(sorted((s, e) for s, e in m.items() if e != 0))


ONE: Final = Unit((), Dimension(), Fraction(1))


class _Parser:
    def __init__(self, text: str, reg: UnitRegistry) -> None:
        if not isinstance(text, str):
            raise UnitError("PARSE_ERROR", "unit expression must be a string")
        if not text.strip() or len(text) > MAX_LENGTH:
            raise UnitError("PARSE_ERROR", f"unit expression must be 1..{MAX_LENGTH} characters")
        self.reg = reg
        self.toks: list[tuple[str, str]] = []
        pos = 0
        while pos < len(text):
            if not text[pos:].strip():
                break
            m = _TOKEN.match(text, pos)
            if m is None:
                raise UnitError("PARSE_ERROR", f"unexpected character near position {pos}")
            op, num, sym = m.groups()
            self.toks.append(("op", op) if op else ("int", num) if num else ("sym", sym))
            pos = m.end()
            if len(self.toks) > MAX_TOKENS:
                raise UnitError("EXPRESSION_TOO_LARGE", f"more than {MAX_TOKENS} tokens")
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> tuple[str, str]:
        t = self.peek()
        if t is None:
            raise UnitError("PARSE_ERROR", "unexpected end of expression")
        self.i += 1
        return t

    def expr(self, depth: int) -> Unit:
        if depth > MAX_DEPTH:
            raise UnitError("EXPRESSION_TOO_LARGE", f"nesting deeper than {MAX_DEPTH}")
        left = self.term(depth)
        while (t := self.peek()) is not None and t in (("op", "*"), ("op", "/")):
            self.take()
            right = self.term(depth)
            left = left.mul(right) if t[1] == "*" else left.div(right)
        return left

    def term(self, depth: int) -> Unit:
        u = self.atom(depth)
        t = self.peek()
        if t is not None and t in (("op", "^"), ("op", "**")):
            self.take()
            kind, val = self.take()
            if kind != "int":
                raise UnitError("PARSE_ERROR", "exponent must be an integer")
            u = u.pow(int(val))
        return u

    def atom(self, depth: int) -> Unit:
        kind, val = self.take()
        if kind == "sym":
            d = self.reg.resolve(val)
            return Unit(((d.symbol, 1),), d.dimension, d.scale)
        if kind == "int":
            if val != "1":
                raise UnitError("PARSE_ERROR", "only the literal 1 is allowed as a number")
            return ONE
        if (kind, val) == ("op", "("):
            u = self.expr(depth + 1)
            if self.take() != ("op", ")"):
                raise UnitError("PARSE_ERROR", "missing closing parenthesis")
            return u
        raise UnitError("PARSE_ERROR", f"unexpected token {val!r}")


def parse_unit(text: str, registry: UnitRegistry | None = None) -> Unit:
    p = _Parser(text, registry or UnitRegistry.default())
    u = p.expr(0)
    if p.peek() is not None:
        raise UnitError("PARSE_ERROR", "unexpected trailing tokens")
    return u
