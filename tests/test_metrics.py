"""Summary statistics for raw benchmark samples, using stdlib unittest only."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness.metrics import RunSample, summarize


class SummarizeTests(unittest.TestCase):
    def test_summarize_all_success(self):
        samples = [
            RunSample(ok=True, ttft_s=0.1, total_s=1.0, completion_tokens=100),
            RunSample(ok=True, ttft_s=0.2, total_s=1.5, completion_tokens=150),
            RunSample(ok=True, ttft_s=0.15, total_s=1.2, completion_tokens=120),
        ]
        s = summarize("test_engine", "test_model", samples)
        self.assertEqual(s.n_requests, 3)
        self.assertEqual(s.n_success, 3)
        self.assertEqual(s.n_failed, 0)
        self.assertIsNotNone(s.p50_ttft_ms)
        self.assertGreater(s.mean_throughput_tok_s, 0)

    def test_summarize_with_failures(self):
        samples = [
            RunSample(ok=True, ttft_s=0.1, total_s=1.0, completion_tokens=100),
            RunSample(ok=False, error="timeout"),
        ]
        s = summarize("test_engine", "test_model", samples)
        self.assertEqual(s.n_requests, 2)
        self.assertEqual(s.n_success, 1)
        self.assertEqual(s.n_failed, 1)
        self.assertEqual(s.errors, ["timeout"])

    def test_summarize_empty(self):
        s = summarize("test_engine", "test_model", [])
        self.assertEqual(s.n_requests, 0)
        self.assertIsNone(s.p50_ttft_ms)
        self.assertIsNone(s.mean_throughput_tok_s)

    def test_percentile_monotonic(self):
        samples = [RunSample(ok=True, ttft_s=i / 100, total_s=i / 10, completion_tokens=10) for i in range(1, 101)]
        s = summarize("e", "m", samples)
        self.assertLessEqual(s.p50_ttft_ms, s.p95_ttft_ms)
        self.assertLessEqual(s.p95_ttft_ms, s.p99_ttft_ms)


if __name__ == "__main__":
    unittest.main()
