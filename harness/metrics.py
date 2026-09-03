"""Statistics helpers for benchmark aggregation. No fabricated data:
every function here operates only on values actually measured by client.py.
"""
import statistics
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RunSample:
    ok: bool
    ttft_s: Optional[float] = None
    total_s: Optional[float] = None
    completion_tokens: Optional[int] = None
    error: Optional[str] = None


@dataclass
class EngineSummary:
    engine: str
    model: str
    n_requests: int
    n_success: int
    n_failed: int
    p50_ttft_ms: Optional[float] = None
    p95_ttft_ms: Optional[float] = None
    p99_ttft_ms: Optional[float] = None
    p50_total_ms: Optional[float] = None
    p95_total_ms: Optional[float] = None
    p99_total_ms: Optional[float] = None
    mean_throughput_tok_s: Optional[float] = None
    errors: List[str] = field(default_factory=list)

    def to_dict(self):
        return self.__dict__


def _pctl(values, q):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def summarize(engine: str, model: str, samples: List[RunSample]) -> EngineSummary:
    ok_samples = [s for s in samples if s.ok]
    failed = [s for s in samples if not s.ok]

    ttfts = [s.ttft_s * 1000 for s in ok_samples if s.ttft_s is not None]
    totals = [s.total_s * 1000 for s in ok_samples if s.total_s is not None]
    throughputs = [
        s.completion_tokens / s.total_s
        for s in ok_samples
        if s.completion_tokens and s.total_s and s.total_s > 0
    ]

    return EngineSummary(
        engine=engine,
        model=model,
        n_requests=len(samples),
        n_success=len(ok_samples),
        n_failed=len(failed),
        p50_ttft_ms=_pctl(ttfts, 0.50),
        p95_ttft_ms=_pctl(ttfts, 0.95),
        p99_ttft_ms=_pctl(ttfts, 0.99),
        p50_total_ms=_pctl(totals, 0.50),
        p95_total_ms=_pctl(totals, 0.95),
        p99_total_ms=_pctl(totals, 0.99),
        mean_throughput_tok_s=statistics.mean(throughputs) if throughputs else None,
        errors=[s.error for s in failed if s.error],
    )
