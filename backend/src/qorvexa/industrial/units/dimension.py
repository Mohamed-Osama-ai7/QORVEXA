"""Dimension vectors (integer exponents) over base dimensions; each currency is its own base."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAX_EXPONENT: Final = 12
PHYSICAL_BASES: Final = ("L", "M", "T", "N")  # length, mass, time, count
CURRENCY_PREFIX: Final = "CUR:"


class UnitError(ValueError):
    """Stable machine-readable error for every units failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Dimension:
    """Sorted tuple of (base, non-zero integer exponent). Equality is structural and exact."""

    exps: tuple[tuple[str, int], ...] = ()

    @staticmethod
    def of(mapping: dict[str, int]) -> Dimension:
        clean = tuple(sorted((k, v) for k, v in mapping.items() if v != 0))
        for _, e in clean:
            if abs(e) > MAX_EXPONENT:
                raise UnitError(
                    "EXPONENT_OUT_OF_RANGE", f"dimension exponent beyond ±{MAX_EXPONENT}"
                )
        return Dimension(clean)

    def _map(self) -> dict[str, int]:
        return dict(self.exps)

    def mul(self, other: Dimension) -> Dimension:
        m = self._map()
        for k, v in other.exps:
            m[k] = m.get(k, 0) + v
        return Dimension.of(m)

    def div(self, other: Dimension) -> Dimension:
        return self.mul(other.pow(-1))

    def pow(self, n: int) -> Dimension:
        return Dimension.of({k: v * n for k, v in self.exps})

    @property
    def is_dimensionless(self) -> bool:
        return not self.exps

    def currency_part(self) -> Dimension:
        return Dimension(tuple(x for x in self.exps if x[0].startswith(CURRENCY_PREFIX)))

    def physical_part(self) -> Dimension:
        return Dimension(tuple(x for x in self.exps if not x[0].startswith(CURRENCY_PREFIX)))

    def __str__(self) -> str:
        return "·".join(f"{k}^{v}" if v != 1 else k for k, v in self.exps) or "1"


def base(symbol: str, exponent: int = 1) -> Dimension:
    return Dimension.of({symbol: exponent})


LENGTH: Final = base("L")
MASS: Final = base("M")
TIME: Final = base("T")
COUNT: Final = base("N")
AREA: Final = LENGTH.pow(2)
VOLUME: Final = LENGTH.pow(3)
ENERGY: Final = MASS.mul(LENGTH.pow(2)).div(TIME.pow(2))
POWER: Final = ENERGY.div(TIME)
