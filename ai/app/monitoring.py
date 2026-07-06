from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterator


@dataclass
class _MetricsState:
    counters: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    latencies: Dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    gauges: Dict[str, float] = field(default_factory=dict)


class MetricsRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = _MetricsState()

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._state.counters[name] += amount

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._state.latencies[name].append(value)

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._state.gauges[name] = value

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": dict(self._state.counters),
                "latencies": {k: list(v) for k, v in self._state.latencies.items()},
                "gauges": dict(self._state.gauges),
            }

    def render_text(self) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        lines.append("# HELP ai_metrics_total Aggregated application metrics")
        lines.append("# TYPE ai_metrics_total counter")
        for key, value in sorted(snap["counters"].items()):
            lines.append(f'ai_metrics_total{{name="{key}"}} {value}')

        lines.append("# HELP ai_metrics_latency_seconds Request latency observations")
        lines.append("# TYPE ai_metrics_latency_seconds summary")
        for key, values in sorted(snap["latencies"].items()):
            if not values:
                continue
            count = len(values)
            avg = sum(values) / count
            p95 = sorted(values)[max(0, int(count * 0.95) - 1)]
            lines.append(f'ai_metrics_latency_seconds_count{{name="{key}"}} {count}')
            lines.append(f'ai_metrics_latency_seconds_avg{{name="{key}"}} {avg:.6f}')
            lines.append(f'ai_metrics_latency_seconds_p95{{name="{key}"}} {p95:.6f}')

        lines.append("# HELP ai_metrics_gauge Current gauge values")
        lines.append("# TYPE ai_metrics_gauge gauge")
        for key, value in sorted(snap["gauges"].items()):
            lines.append(f'ai_metrics_gauge{{name="{key}"}} {value}')
        return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()


def record_request(*, endpoint: str, status: str, duration_seconds: float, cached: bool = False) -> None:
    REGISTRY.inc(f"requests_total:{endpoint}:{status}")
    REGISTRY.observe(f"request_latency_seconds:{endpoint}", duration_seconds)
    if cached:
        REGISTRY.inc(f"cache_hits_total:{endpoint}")
    else:
        REGISTRY.inc(f"cache_misses_total:{endpoint}")


def record_cache_hit(namespace: str = "answer") -> None:
    REGISTRY.inc(f"cache_hit_total:{namespace}")


def record_cache_miss(namespace: str = "answer") -> None:
    REGISTRY.inc(f"cache_miss_total:{namespace}")


def record_cache_set(namespace: str = "answer") -> None:
    REGISTRY.inc(f"cache_set_total:{namespace}")


def record_error(kind: str, source: str = "gateway") -> None:
    REGISTRY.inc(f"errors_total:{source}:{kind}")


def record_llm_tokens(*, school_name: str, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0) -> None:
    REGISTRY.inc(f"llm_prompt_tokens_total:{school_name}", prompt_tokens)
    REGISTRY.inc(f"llm_completion_tokens_total:{school_name}", completion_tokens)
    REGISTRY.inc(f"llm_total_tokens_total:{school_name}", total_tokens)


@contextmanager
def timer(name: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        REGISTRY.observe(name, time.perf_counter() - start)
