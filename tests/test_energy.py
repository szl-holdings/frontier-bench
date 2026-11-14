"""Honest colocated-only energy metering for benchmark runs (stdlib unittest only)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness import energy as energy_mod
from harness.energy import (
    build_block,
    capability_summary,
    endpoint_colocated,
    engine_energy_meter,
)


class ColocatedTests(unittest.TestCase):
    def test_loopback_is_colocated(self):
        self.assertTrue(endpoint_colocated("http://127.0.0.1:8899/v1"))
        self.assertTrue(endpoint_colocated("http://localhost:8000"))
        self.assertTrue(endpoint_colocated("http://[::1]:8000"))
        self.assertTrue(endpoint_colocated("http://0.0.0.0:8000"))

    def test_remote_is_not_colocated(self):
        self.assertFalse(endpoint_colocated("http://gpu-node.example:8000"))
        self.assertFalse(endpoint_colocated("http://synthetic.invalid"))
        self.assertFalse(endpoint_colocated("not a url"))

    def test_empty_endpoint_is_not_colocated(self):
        self.assertFalse(endpoint_colocated(None))
        self.assertFalse(endpoint_colocated(""))


class UnmeasuredHonestyTests(unittest.TestCase):
    def test_disabled_env_is_unmeasured(self):
        os.environ["FRONTIER_BENCH_ENERGY"] = "off"
        try:
            meter = engine_energy_meter("http://127.0.0.1:8899")
            meter.start()
            meter.sample()
            meter.stop()
            block = meter.result(n_requests=2, completion_tokens=10)
        finally:
            del os.environ["FRONTIER_BENCH_ENERGY"]
        self.assertEqual(block["mode"], "unmeasured")
        self.assertIsNone(block["joules"])
        self.assertEqual(block["metering"], "unmeasured")
        self.assertIsNone(block["joules_per_request"])
        self.assertIsNone(block["tokens_per_joule"])
        self.assertIn("disabled", block["note"])

    def test_remote_endpoint_is_unmeasured_with_reason(self):
        meter = engine_energy_meter("http://gpu-node.example:8000")
        meter.start()
        meter.stop()
        block = meter.result(n_requests=1, completion_tokens=5)
        self.assertEqual(block["mode"], "unmeasured")
        self.assertIsNone(block["joules"])
        self.assertFalse(block["colocated"])
        self.assertIn("colocated", block["note"])

    def test_missing_package_is_unmeasured(self):
        saved = (energy_mod.EnergyMeter, energy_mod.nvml_available, energy_mod.capability_report)
        energy_mod.EnergyMeter = None
        energy_mod.nvml_available = None
        energy_mod.capability_report = None
        try:
            meter = engine_energy_meter("http://127.0.0.1:8899")
            meter.start()
            meter.stop()
            block = meter.result(n_requests=1, completion_tokens=1)
        finally:
            (energy_mod.EnergyMeter, energy_mod.nvml_available, energy_mod.capability_report) = saved
        self.assertEqual(block["mode"], "unmeasured")
        self.assertIsNone(block["joules"])
        self.assertIn("import", block["note"])

    def test_unmeasured_stop_result_never_fabricates(self):
        block = build_block(
            {"mode": "unmeasured", "joules": None, "wall_seconds": 1.0, "n_power_samples": 0, "note": "x"},
            n_requests=3,
            completion_tokens=99,
            colocated=True,
            device_index=0,
        )
        self.assertEqual(block["mode"], "unmeasured")
        self.assertEqual(block["metering"], "unmeasured")
        self.assertIsNone(block["joules"])
        self.assertIsNone(block["avg_power_w"])
        self.assertIsNone(block["joules_per_request"])
        self.assertIsNone(block["tokens_per_joule"])

    def test_measured_mode_without_valid_joules_is_forced_unmeasured(self):
        block = build_block(
            {"mode": "measured-energy", "joules": None, "wall_seconds": 1.0, "n_power_samples": 0, "note": None},
            n_requests=1,
            completion_tokens=4,
            colocated=True,
            device_index=0,
        )
        self.assertEqual(block["mode"], "unmeasured")
        self.assertIsNone(block["joules"])
        self.assertIn("no valid joules", block["note"])

    def test_invalid_counts_raise(self):
        stop = {"mode": "unmeasured", "joules": None, "wall_seconds": 0.0, "n_power_samples": 0, "note": None}
        with self.assertRaises(ValueError):
            build_block(stop, n_requests=-1, completion_tokens=0, colocated=True, device_index=0)
        with self.assertRaises(ValueError):
            build_block(stop, n_requests=1, completion_tokens="9", colocated=True, device_index=0)


class MeasuredMathTests(unittest.TestCase):
    def test_measured_derivations(self):
        stop = {
            "mode": "measured-energy",
            "joules": 100.0,
            "wall_seconds": 10.0,
            "n_power_samples": 0,
            "note": "hardware energy accumulator delta (board-level).",
        }
        block = build_block(stop, n_requests=10, completion_tokens=500, colocated=True, device_index=1)
        self.assertEqual(block["mode"], "measured-energy")
        self.assertEqual(block["metering"], "benchmark-node-nvml")
        self.assertEqual(block["joules"], 100.0)
        self.assertAlmostEqual(block["avg_power_w"], 10.0)
        self.assertAlmostEqual(block["joules_per_request"], 10.0)
        self.assertAlmostEqual(block["tokens_per_joule"], 5.0)
        self.assertTrue(block["colocated"])
        self.assertEqual(block["device_index"], 1)

    def test_power_integral_derivations(self):
        stop = {
            "mode": "measured-power-integral",
            "joules": 25.5,
            "wall_seconds": 5.0,
            "n_power_samples": 120,
            "note": "trapezoidal integral",
        }
        block = build_block(stop, n_requests=3, completion_tokens=51, colocated=True, device_index=0)
        self.assertEqual(block["mode"], "measured-power-integral")
        self.assertEqual(block["n_power_samples"], 120)
        self.assertAlmostEqual(block["avg_power_w"], 5.1)
        self.assertAlmostEqual(block["joules_per_request"], 8.5)
        self.assertAlmostEqual(block["tokens_per_joule"], 2.0)

    def test_zero_joules_has_no_division_errors(self):
        stop = {"mode": "measured-power-integral", "joules": 0.0, "wall_seconds": 0.5, "n_power_samples": 2, "note": "tiny"}
        block = build_block(stop, n_requests=1, completion_tokens=0, colocated=True, device_index=0)
        self.assertEqual(block["joules"], 0.0)
        self.assertEqual(block["joules_per_request"], 0.0)
        self.assertIsNone(block["tokens_per_joule"])
        self.assertEqual(block["avg_power_w"], 0.0)


class CapabilitySummaryTests(unittest.TestCase):
    def test_shape_is_honest(self):
        cap = capability_summary()
        self.assertEqual(cap["package"], "szl-energy-attest")
        self.assertIn("import_error", cap)
        self.assertIn("preferred_mode", cap)
        self.assertTrue(cap["colocated_only"])
        self.assertEqual(cap["disable_env"], "FRONTIER_BENCH_ENERGY=off")
        self.assertIsInstance(cap["importable"], bool)
        self.assertIsInstance(cap["nvml_available"], bool)


if __name__ == "__main__":
    unittest.main()
