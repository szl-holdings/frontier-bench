"""Synthetic evidence controls. Never import provider/registry implementations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify.verifier import GENESIS, check_receipt, digest, verify, verify_snapshots
from harness.metrics import RunSample, summarize


def receipt():
    return {"plane": "engine", "status": "MEASURED",
            "machine": {"cpu": "synthetic", "gpu": "none", "ram_gb": 1},
            "measured_at": "2000-01-01T00:00:00Z", "method": "synthetic-only",
            "metrics": {"score": 0.5}, "prev_hash": GENESIS}


def legacy_hash(value):
    return hashlib.sha256(json.dumps(
        {k: v for k, v in value.items() if k != "hash"},
        sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


class ReceiptAdversarialTests(unittest.TestCase):
    def check_rejected(self, value):
        if isinstance(value, dict):
            value["hash"] = legacy_hash(value)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            errors, paths = verify([path])
            self.assertTrue(errors)
            self.assertEqual([], paths)
            self.assertEqual([], verify_snapshots([path])[1])

    def test_correctly_self_hashed_invalid_values_are_rejected(self):
        changes = [
            ("metrics", "measured"), ("metrics", []), ("metrics", {}),
            ("metrics", {"score": True}), ("metrics", {"score": None}),
            ("metrics", {"score": float("nan")}), ("metrics", {"score": float("inf")}),
            ("metrics", {"score": -float("inf")}), ("metrics", {"score": 10 ** 400}),
            ("metrics", {"nested": {"score": 1}}), ("plane", []), ("status", []),
            ("machine", []), ("machine", {"cpu": "x", "gpu": "none", "ram_gb": -1}),
            ("machine", {"cpu": "x", "gpu": "none", "ram_gb": True}),
            ("machine", {"cpu": [], "gpu": "none", "ram_gb": 1}),
            ("method", {}), ("method", " "), ("prev_hash", []),
            ("measured_at", "yesterday"), ("measured_at", "2000-02-30"),
            ("measured_at", "2000-01-01T00:00:00"),
            ("measured_at", "2000-01-01T00:00:00+01:00"),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                candidate = receipt()
                candidate[field] = value
                self.check_rejected(candidate)

    def test_nonobjects_and_missing_fields_fail_closed(self):
        for value in ([], None, "receipt", 12, True, {}):
            with self.subTest(value=value):
                self.check_rejected(value)

    def test_nonmeasured_values_and_nonpath_inputs_fail_closed(self):
        candidate = receipt()
        candidate["status"] = "BLOCKED"
        self.check_rejected(candidate)
        for paths in ([0], [True], [None], "not-a-collection"):
            with self.subTest(paths=paths):
                errors, measured = verify(paths)
                self.assertTrue(errors)
                self.assertEqual([], measured)

    def test_bad_chain_suppresses_even_valid_measured_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a.json", Path(directory) / "b.json"
            candidate = receipt()
            candidate["hash"] = digest(candidate)
            first.write_text(json.dumps(candidate))
            second.write_text(json.dumps(candidate))
            errors, paths = verify([first, second])
            self.assertTrue(errors)
            self.assertEqual([], paths)

    def test_bad_hash_and_nonfinite_extensions_fail_closed(self):
        candidate = receipt()
        candidate["hash"] = "f" * 64
        self.assertTrue(check_receipt(candidate, 0))
        candidate["extension"] = {"x": float("nan")}
        self.check_rejected(candidate)
        with self.assertRaises(ValueError):
            digest(candidate)

    def test_duplicate_keys_overflow_and_invalid_utf8_are_rejected(self):
        for raw in (b'{"plane":"engine","plane":"quant"}',
                    b'{"nested":{"x":1,"x":2}}', b'{"x":1e999}', b'\xff'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "synthetic.json"
                path.write_bytes(raw)
                errors, paths = verify([path])
                self.assertTrue(errors)
                self.assertEqual([], paths)

    def test_valid_dates_and_utc_times_preserve_digest_compatibility(self):
        for timestamp in ("2000-01-01", "2000-01-01T00:00:00Z",
                          "2000-01-01T00:00:00.123+00:00"):
            with self.subTest(timestamp=timestamp), tempfile.TemporaryDirectory() as directory:
                candidate = receipt()
                candidate["measured_at"] = timestamp
                candidate["hash"] = legacy_hash(candidate)
                self.assertEqual(candidate["hash"], digest(candidate))
                path = Path(directory) / "synthetic.json"
                path.write_text(json.dumps(candidate))
                self.assertEqual(([], [path]), verify([path]))
                snapshot = verify_snapshots([path])[1][0][1]
                path.write_text("{}")
                self.assertEqual(candidate, snapshot)


class AggregationAdversarialTests(unittest.TestCase):
    def test_invalid_timings_and_tokens_are_rejected(self):
        for field in ("ttft_s", "total_s"):
            for value in (-1, float("nan"), float("inf"), True, "1", 1e308, 10 ** 400):
                with self.subTest(field=field, value=value):
                    sample = RunSample(ok=True, total_s=1, completion_tokens=1)
                    setattr(sample, field, value)
                    with self.assertRaisesRegex(ValueError, "invalid sample timing"):
                        summarize("synthetic", "synthetic", [sample])
        for value in (-1, True, 1.2, "1"):
            with self.subTest(tokens=value), self.assertRaisesRegex(ValueError, "token count"):
                summarize("synthetic", "synthetic", [RunSample(ok=True, completion_tokens=value)])
        with self.assertRaisesRegex(ValueError, "success flag"):
            summarize("synthetic", "synthetic", [RunSample(ok="yes")])
        with self.assertRaisesRegex(ValueError, "throughput"):
            summarize("synthetic", "synthetic", [RunSample(ok=True, total_s=1e-308, completion_tokens=100)])

    def test_empty_summary_keeps_unknown_metrics(self):
        summary = summarize("synthetic", "synthetic", [])
        self.assertEqual(0, summary.n_requests)
        self.assertEqual(0, summary.n_success)
        self.assertIsNone(summary.p50_total_ms)
        self.assertIsNone(summary.mean_throughput_tok_s)


class RunnerPlanTests(unittest.TestCase):
    def setUp(self):
        client = types.ModuleType("harness.client")
        client.health_check = unittest.mock.Mock(return_value=True)
        client.chat_completion = unittest.mock.Mock(return_value=RunSample(ok=True, total_s=1, completion_tokens=1))
        registry = types.ModuleType("harness.engine_registry")
        registry.REGISTRY = {}
        registry.available_engines = unittest.mock.Mock(return_value={})
        registry.unavailable_engines = unittest.mock.Mock(return_value={})
        spec = importlib.util.spec_from_file_location("synthetic_runner", ROOT / "harness/runner.py")
        self.runner = importlib.util.module_from_spec(spec)
        with unittest.mock.patch.dict(sys.modules, {"harness.client": client, "harness.engine_registry": registry}):
            spec.loader.exec_module(self.runner)
        self.client = client
        self.spec = types.SimpleNamespace(resolve_endpoint=unittest.mock.Mock(return_value="synthetic://no-network"))

    def test_invalid_plan_never_resolves_endpoint_or_calls_client(self):
        for prompts, repeats in (([], 1), (["fixture"], 0), (["fixture"], -1),
                                 (["fixture"], True), ("fixture", 1), ([""], 1)):
            with self.subTest(prompts=prompts, repeats=repeats):
                result = self.runner.run_engine("fixture", self.spec, prompts, "fixture", repeats, 1)
                self.assertEqual("INVALID", result["status"])
        self.spec.resolve_endpoint.assert_not_called()
        self.client.health_check.assert_not_called()
        self.client.chat_completion.assert_not_called()

    def test_no_success_and_invalid_samples_cannot_be_measured(self):
        for sample, expected in ((RunSample(ok=False), "FAILED"),
                                 (RunSample(ok=True), "FAILED"),
                                 (RunSample(ok=True, total_s=float("nan")), "INVALID")):
            with self.subTest(expected=expected):
                self.client.chat_completion.return_value = sample
                result = self.runner.run_engine("fixture", self.spec, ["fixture"], "fixture", 1, 1)
                self.assertEqual(expected, result["status"])

    def test_positive_synthetic_control(self):
        result = self.runner.run_engine("fixture", self.spec, ["fixture"], "fixture", 2, 1)
        self.assertEqual("MEASURED", result["status"])
        self.assertEqual(2, result["summary"]["n_success"])
        self.assertEqual(2, self.client.chat_completion.call_count)


if __name__ == "__main__":
    unittest.main()
