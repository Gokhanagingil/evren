"""Metrikler (U8.9, ADR-0040; F1-15): Prometheus metin biçimi (0.0.4), bağımlılıksız.

Servisler ``/metrics`` ile sayaç, gösterge ve histogram verir; kurumun Prometheus'u, Grafana Agent'ı veya
OpenTelemetry Collector'ı (prometheus alıcısı) kazır. Etiket değerleri kaçışlanır; etiket adları ve metrik adları
sabittir (kullanıcı girdisi ad olmaz). İş parçacığı güvenlidir (uvicorn iş parçacığı havuzu).
"""
from __future__ import annotations

import math
import re
import threading
from collections.abc import Iterable, Mapping, Sequence

__all__ = ["DEFAULT_BUCKETS", "Counter", "Gauge", "Histogram", "Registry", "render_samples"]

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: Sequence[str], values: Sequence[object], extra: Mapping[str, object] | None = None) -> str:
    pairs = [*zip(names, values, strict=True), *(extra or {}).items()]
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in pairs) + "}" if pairs else ""


def _num(v: float) -> str:
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    return repr(float(v)) if not float(v).is_integer() else str(int(v))


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        if not _NAME.match(name) or not all(_LABEL.match(label) for label in labels):
            raise ValueError(f"geçersiz metrik veya etiket adı: {name}")
        self.name, self.help, self.label_names = name, help_text, tuple(labels)
        self._lock = threading.Lock()

    def _key(self, labels: Mapping[str, object]) -> tuple[str, ...]:
        if set(labels) != set(self.label_names):
            raise ValueError(f"{self.name}: etiketler {self.label_names} olmalı")
        return tuple(str(labels[n]) for n in self.label_names)

    def header(self) -> list[str]:
        return [f"# HELP {self.name} {_escape(self.help)}", f"# TYPE {self.name} {self.kind}"]


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **labels: object) -> None:
        if amount < 0:
            raise ValueError("sayaç azalamaz")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def lines(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        return [*self.header(), *(f"{self.name}{_labels(self.label_names, k)} {_num(v)}" for k, v in items)]


class Gauge(_Metric):
    kind = "gauge"

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, **labels: object) -> None:
        with self._lock:
            self._values[self._key(labels)] = float(value)

    def lines(self) -> list[str]:
        with self._lock:
            items = sorted(self._values.items())
        return [*self.header(), *(f"{self.name}{_labels(self.label_names, k)} {_num(v)}" for k, v in items)]


class Histogram(_Metric):
    kind = "histogram"

    def __init__(self, name: str, help_text: str, labels: Sequence[str] = (),
                 buckets: Sequence[float] = DEFAULT_BUCKETS) -> None:
        super().__init__(name, help_text, labels)
        self.buckets = tuple(sorted(buckets))
        self._values: dict[tuple[str, ...], tuple[list[int], float, int]] = {}

    def observe(self, value: float, **labels: object) -> None:
        key = self._key(labels)
        with self._lock:
            counts, total, n = self._values.get(key, ([0] * len(self.buckets), 0.0, 0))
            for i, b in enumerate(self.buckets):
                if value <= b:
                    counts[i] += 1
            self._values[key] = (counts, total + value, n + 1)

    def lines(self) -> list[str]:
        with self._lock:
            items = sorted((k, (list(c), s, n)) for k, (c, s, n) in self._values.items())
        out = self.header()
        for key, (counts, total, n) in items:
            for b, c in zip(self.buckets, counts, strict=True):
                out.append(f"{self.name}_bucket{_labels(self.label_names, key, {'le': _num(b)})} {c}")
            out.append(f"{self.name}_bucket{_labels(self.label_names, key, {'le': '+Inf'})} {n}")
            out.append(f"{self.name}_sum{_labels(self.label_names, key)} {_num(total)}")
            out.append(f"{self.name}_count{_labels(self.label_names, key)} {n}")
        return out


class Registry:
    def __init__(self) -> None:
        self._metrics: list[_Metric] = []

    def register(self, metric: _Metric) -> _Metric:
        if any(m.name == metric.name for m in self._metrics):
            raise ValueError(f"metrik iki kez kaydedildi: {metric.name}")
        self._metrics.append(metric)
        return metric

    def counter(self, name: str, help_text: str, labels: Sequence[str] = ()) -> Counter:
        return self.register(Counter(name, help_text, labels))  # type: ignore[return-value]

    def histogram(self, name: str, help_text: str, labels: Sequence[str] = (),
                  buckets: Sequence[float] = DEFAULT_BUCKETS) -> Histogram:
        return self.register(Histogram(name, help_text, labels, buckets))  # type: ignore[return-value]

    def render(self, extra: Iterable[str] = ()) -> str:
        lines: list[str] = []
        for m in self._metrics:
            lines += m.lines()  # type: ignore[attr-defined]
        lines += list(extra)
        return "\n".join(lines) + "\n"


def render_samples(name: str, help_text: str, kind: str, samples: Iterable[tuple[Mapping[str, object], float]]) -> list[str]:
    """Kazıma anında hesaplanan bir göstergenin satırları (ör. veritabanından okunan değerler)."""
    if not _NAME.match(name) or kind not in ("gauge", "counter"):
        raise ValueError(f"geçersiz metrik: {name}")
    out = [f"# HELP {name} {_escape(help_text)}", f"# TYPE {name} {kind}"]
    for labels, value in samples:
        if not all(_LABEL.match(k) for k in labels):
            raise ValueError(f"geçersiz etiket: {name}")
        out.append(f"{name}{_labels(list(labels), list(labels.values()))} {_num(value)}")
    return out
