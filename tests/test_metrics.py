import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness.metrics import RunSample, summarize

def test_summarize_all_success():
    samples = [
        RunSample(ok=True, ttft_s=0.1, total_s=1.0, completion_tokens=100),
        RunSample(ok=True, ttft_s=0.2, total_s=1.5, completion_tokens=150),
        RunSample(ok=True, ttft_s=0.15, total_s=1.2, completion_tokens=120),
    ]
    s = summarize("test_engine", "test_model", samples)
    assert s.n_requests == 3
    assert s.n_success == 3
    assert s.n_failed == 0
    assert s.p50_ttft_ms is not None
    assert s.mean_throughput_tok_s > 0

def test_summarize_with_failures():
    samples = [
        RunSample(ok=True, ttft_s=0.1, total_s=1.0, completion_tokens=100),
        RunSample(ok=False, error="timeout"),
    ]
    s = summarize("test_engine", "test_model", samples)
    assert s.n_requests == 2
    assert s.n_success == 1
    assert s.n_failed == 1
    assert s.errors == ["timeout"]

def test_summarize_empty():
    s = summarize("test_engine", "test_model", [])
    assert s.n_requests == 0
    assert s.p50_ttft_ms is None
    assert s.mean_throughput_tok_s is None

def test_percentile_monotonic():
    samples = [RunSample(ok=True, ttft_s=i/100, total_s=i/10, completion_tokens=10) for i in range(1, 101)]
    s = summarize("e", "m", samples)
    assert s.p50_ttft_ms <= s.p95_ttft_ms <= s.p99_ttft_ms

if __name__ == "__main__":
    test_summarize_all_success()
    test_summarize_with_failures()
    test_summarize_empty()
    test_percentile_monotonic()
    print("ALL METRICS TESTS PASSED")
