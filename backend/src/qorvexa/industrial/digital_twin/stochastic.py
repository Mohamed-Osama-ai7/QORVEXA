"""Seeded random streams, explicit sampling and replication statistics.

* Only DETERMINISTIC, TRIANGULAR and EXPONENTIAL are supported, with explicit parameters.
* Every (seed, replication, stream) triple gets an independent ``random.Random`` derived through
  SHA-256, so replays are bit-identical and adding a stream never shifts another stream.
* Sampled seconds become ticks by the declared policy ``CEIL_TO_TICK`` (minimum one tick for a
  positive duration); this is recorded as an assumption of every stochastic run.
* Confidence intervals are reported only for >= 10 independent replications of a stochastic model
  (Student-t on the mean); otherwise the reason is given instead of a number.
"""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections.abc import Sequence
from fractions import Fraction
from typing import Final

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.digital_twin.contracts import DistKind
from qorvexa.industrial.digital_twin.validation import CDist

F = ConfigDict(frozen=True, extra="forbid")
SAMPLE_ROUNDING: Final = "CEIL_TO_TICK"
MIN_REPLICATIONS_FOR_CI: Final = 10
MAX_REPLICATIONS: Final = 1000
# two-sided 95% Student-t critical values
_T975: Final = {
    9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120,
    17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
    25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000,
    120: 1.980,
}  # fmt: skip


def stream_seed(seed: int, replication: int, stream: str) -> int:
    h = hashlib.sha256(f"{seed}|{replication}|{stream}".encode()).digest()
    return int.from_bytes(h[:8], "big")


class Streams:
    """Lazily created independent generators, one per named stream."""

    def __init__(self, seed: int, replication: int) -> None:
        self.seed, self.replication = seed, replication
        self._rngs: dict[str, random.Random] = {}

    def rng(self, stream: str) -> random.Random:
        r = self._rngs.get(stream)
        if r is None:
            r = random.Random(stream_seed(self.seed, self.replication, stream))  # noqa: S311 - simulation, not crypto
            self._rngs[stream] = r
        return r

    def ticks(self, d: CDist, stream: str) -> int:
        if d.kind is DistKind.DETERMINISTIC:
            return int(d.ticks or 0)
        r = self.rng(stream)
        if d.kind is DistKind.TRIANGULAR:
            assert d.low is not None and d.mode is not None and d.high is not None  # noqa: S101
            x = r.triangular(float(d.low), float(d.high), float(d.mode))
        else:
            assert d.mean is not None  # noqa: S101
            x = r.expovariate(1.0 / float(d.mean))
        return max(1, math.ceil(x))

    def bernoulli(self, p: Fraction, stream: str) -> bool:
        return self.rng(stream).random() < float(p)


class Summary(BaseModel):
    """Statistics of one metric across replications (estimates, not measurements)."""

    model_config = F
    metric: str
    n: int
    mean: float
    median: float
    p10: float
    p90: float
    minimum: float
    maximum: float
    variance: float | None
    std_error: float | None
    ci95: tuple[float, float] | None
    ci_note: str


def _pct(xs: list[float], p: float) -> float:
    """Nearest-rank percentile (no interpolation)."""
    k = max(1, math.ceil(p * len(xs)))
    return xs[k - 1]


def summarize(metric: str, values: Sequence[float], *, stochastic: bool) -> Summary:
    xs = sorted(float(v) for v in values)
    n = len(xs)
    if n == 0:
        raise ValueError("no values to summarize")
    mean = statistics.fmean(xs)
    var = statistics.variance(xs) if n >= 2 else None
    se = math.sqrt(var / n) if var is not None else None
    ci: tuple[float, float] | None = None
    if not stochastic:
        note = "deterministic model: replications are identical, no interval applies"
    elif n < MIN_REPLICATIONS_FOR_CI:
        note = f"fewer than {MIN_REPLICATIONS_FOR_CI} replications: no confidence interval reported"
    elif se is not None:
        df = n - 1
        key = min((k for k in _T975 if k >= df), default=None)
        t = _T975[key] if key is not None else 1.960
        ci = (mean - t * se, mean + t * se)
        note = f"95% Student-t interval on the mean of {n} independent replications"
    else:
        note = "no variance"
    return Summary(
        metric=metric, n=n, mean=mean, median=statistics.median(xs), p10=_pct(xs, 0.10),
        p90=_pct(xs, 0.90), minimum=xs[0], maximum=xs[-1], variance=var, std_error=se, ci95=ci,
        ci_note=note,
    )  # fmt: skip
