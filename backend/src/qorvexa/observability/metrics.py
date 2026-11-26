"""Dependency-free metrics registry with Prometheus text exposition (format 0.0.4).

Deliberately small: Counter, Gauge, Histogram with fixed label names. Label-value cardinality is
capped per metric so an unbounded label (an ID, a URL) cannot exhaust memory. Do not put sensitive
industrial values in labels; use aggregates.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Sequence
from typing import Final

_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
DEFAULT_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
MAX_SERIES_PER_METRIC: Final = 1000
OVERFLOW_VALUE: Final = "__overflow__"

LabelKey = tuple[str, ...]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return repr(float(value)) if not float(value).is_integer() else str(int(value))


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> None:
        if not _NAME.fullmatch(name):
            raise ValueError(f"invalid metric name: {name!r}")
        for label in label_names:
            if not _LABEL.fullmatch(label) or label == "le":
                raise ValueError(f"invalid label name: {label!r}")
        self.name = name
        self.help = help_text
        self.label_names = tuple(label_names)
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str], known: dict[LabelKey, object]) -> LabelKey:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"{self.name}: expected labels {self.label_names}, got {tuple(labels)}"
            )
        key = tuple(str(labels[n]) for n in self.label_names)
        if key not in known and len(known) >= MAX_SERIES_PER_METRIC:
            return tuple(OVERFLOW_VALUE for _ in self.label_names)
        return key

    def _labels_text(self, key: LabelKey, extra: str = "") -> str:
        parts = [f'{n}="{_escape(v)}"' for n, v in zip(self.label_names, key, strict=True)]
        if extra:
            parts.append(extra)
        return "{" + ",".join(parts) + "}" if parts else ""

    def _header(self) -> list[str]:
        return [f"# HELP {self.name} {_escape(self.help)}", f"# TYPE {self.name} {self.kind}"]

    def render(self) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, label_names)
        self._values: dict[LabelKey, float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0 or math.isnan(amount):
            raise ValueError("counters can only increase")
        with self._lock:
            key = self._key(labels, self._values)  # type: ignore[arg-type]
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        with self._lock:
            return self._values.get(tuple(str(labels[n]) for n in self.label_names), 0.0)

    def render(self) -> list[str]:
        with self._lock:
            return self._header() + [
                f"{self.name}{self._labels_text(k)} {_fmt(v)}"
                for k, v in sorted(self._values.items())
            ]


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, label_names)
        self._values: dict[LabelKey, float] = {}

    def set(self, value: float, **labels: str) -> None:
        with self._lock:
            self._values[self._key(labels, self._values)] = float(value)  # type: ignore[arg-type]

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        with self._lock:
            key = self._key(labels, self._values)  # type: ignore[arg-type]
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float | None:
        with self._lock:
            return self._values.get(tuple(str(labels[n]) for n in self.label_names))

    def render(self) -> list[str]:
        with self._lock:
            return self._header() + [
                f"{self.name}{self._labels_text(k)} {_fmt(v)}"
                for k, v in sorted(self._values.items())
            ]


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, label_names)
        ordered = sorted(float(b) for b in buckets)
        if not ordered or ordered != sorted(set(ordered)) or any(math.isinf(b) for b in ordered):
            raise ValueError("buckets must be unique, finite and non-empty")
        self.buckets = tuple(ordered)
        # per key: (bucket counts incl. +Inf, sum, count)
        self._series: dict[LabelKey, tuple[list[int], float, int]] = {}

    def observe(self, value: float, **labels: str) -> None:
        if math.isnan(value):
            raise ValueError("cannot observe NaN")
        with self._lock:
            key = self._key(labels, self._series)  # type: ignore[arg-type]
            counts, total, n = self._series.get(key, ([0] * (len(self.buckets) + 1), 0.0, 0))
            for i, upper in enumerate(self.buckets):
                if value <= upper:
                    counts[i] += 1
            counts[-1] += 1
            self._series[key] = (counts, total + value, n + 1)

    def count(self, **labels: str) -> int:
        with self._lock:
            series = self._series.get(tuple(str(labels[n]) for n in self.label_names))
            return series[2] if series else 0

    def render(self) -> list[str]:
        lines = self._header()
        with self._lock:
            for key, (counts, total, n) in sorted(self._series.items()):
                for upper, c in zip(self.buckets, counts[:-1], strict=True):
                    lines.append(
                        f"{self.name}_bucket{self._labels_text(key, f'le="{_fmt(upper)}"')} {c}"
                    )
                lines.append(
                    f"{self.name}_bucket{self._labels_text(key, 'le="+Inf"')} {counts[-1]}"
                )
                lines.append(f"{self.name}_sum{self._labels_text(key)} {_fmt(total)}")
                lines.append(f"{self.name}_count{self._labels_text(key)} {n}")
        return lines


class MetricsRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}
        self._lock = threading.Lock()

    def _register[M: _Metric](self, metric: M) -> M:
        with self._lock:
            existing = self._metrics.get(metric.name)
            if existing is not None:
                if type(existing) is not type(metric) or existing.label_names != metric.label_names:
                    raise ValueError(f"metric {metric.name!r} already registered differently")
                return existing
            self._metrics[metric.name] = metric
            return metric

    def counter(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> Counter:
        return self._register(Counter(name, help_text, label_names))

    def gauge(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> Gauge:
        return self._register(Gauge(name, help_text, label_names))

    def histogram(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> Histogram:
        return self._register(Histogram(name, help_text, label_names, buckets))

    def render(self) -> str:
        with self._lock:
            metrics = sorted(self._metrics.values(), key=lambda m: m.name)
        lines: list[str] = []
        for metric in metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n" if lines else ""


PROMETHEUS_CONTENT_TYPE: Final = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_REGISTRY: Final = MetricsRegistry()
