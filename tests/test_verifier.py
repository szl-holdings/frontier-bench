"""Native receipt API controls, using stdlib unittest only."""
import json
import hashlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify.verifier import GENESIS, digest, verify


class NativeVerifierTests(unittest.TestCase):
    def test_recorded_native_repair_report_recomputes(self):
        path = Path(__file__).resolve().parents[1] / "evidence/eclipse-native-proof-20260905.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        receipt = report.pop("receipt")
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.assertEqual(hashlib.sha256(encoded.encode()).hexdigest(), receipt)
        self.assertEqual(report["sensitivity"], "10/10")
        self.assertEqual(report["blind_spots"], [])
        self.assertTrue(report["baseline"]["valid"])
        self.assertEqual(report["baseline"]["detail"]["measured_count"], 3)
        self.assertTrue(report["baseline"]["detail"]["measured_paths_match"])

    def test_empty_collection_and_iterator_fail_closed(self):
        for paths in ([], (), iter(())):
            errors, measured = verify(paths)
            self.assertTrue(errors)
            self.assertEqual(measured, [])

    def test_missing_collection_fails_closed(self):
        errors, measured = verify(None)
        self.assertTrue(errors)
        self.assertEqual(measured, [])

    def test_valid_nonempty_control_and_changed_payload(self):
        receipt = {"plane": "retrieval", "status": "MEASURED",
                   "machine": {"cpu": "unit-fixture", "ram_gb": 1, "gpu": "none"},
                   "measured_at": "2000-01-01T00:00:00Z", "method": "fixture-control",
                   "metrics": {"score": 0.5}, "prev_hash": GENESIS}
        receipt["hash"] = digest(receipt)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            errors, measured = verify([str(path)])
            self.assertEqual(errors, [])
            self.assertEqual(measured, [str(path)])
            receipt["metrics"]["score"] = 0.9
            path.write_text(json.dumps(receipt), encoding="utf-8")
            errors, _ = verify([str(path)])
            self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
