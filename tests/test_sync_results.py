import importlib.util
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "sync_results.py"
SPEC = importlib.util.spec_from_file_location("sync_results", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SyncResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.receipts = self.root / "receipts"
        self.receipts.mkdir()
        self.output = self.root / "results.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_genesis(self, plane: str) -> None:
        receipt = {
            "machine": {"cpu": "test", "gpu": "test", "ram_gb": 1},
            "measured_at": "2026-09-04T00:00:00Z",
            "method": "test",
            "metrics": {},
            "plane": plane,
            "prev_hash": "0" * 64,
            "status": "BLOCKED",
        }
        receipt["hash"] = MODULE.verify_snapshots.__globals__["digest"](receipt)
        (self.receipts / "000-genesis.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )

    def test_missing_chain_fails_without_output(self) -> None:
        self.assertEqual(1, MODULE.main(str(self.receipts), str(self.output), "engine"))
        self.assertFalse(self.output.exists())

    def test_wrong_plane_fails_without_output(self) -> None:
        self.write_genesis("retrieval")
        self.assertEqual(1, MODULE.main(str(self.receipts), str(self.output), "engine"))
        self.assertFalse(self.output.exists())

    def test_blocked_genesis_is_an_honest_empty_plane(self) -> None:
        self.write_genesis("engine")
        self.assertEqual(0, MODULE.main(str(self.receipts), str(self.output), "engine"))
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(0, payload["count"])
        self.assertEqual([], payload["results"])
        self.assertEqual("2026-09-04T00:00:00Z", payload["generated_at"])
        self.assertEqual("UNVERIFIED", payload["provenance"]["authenticity"])
        self.assertFalse(payload["provenance"]["results_are_measured_only"])

    def test_replacement_after_verification_does_not_change_export(self):
        self.write_genesis("engine")
        path = self.receipts / "000-genesis.json"
        receipt = json.loads(path.read_text())
        receipt.update(status="MEASURED", metrics={"value": 1})
        receipt["hash"] = MODULE.verify_snapshots.__globals__["digest"](receipt)
        path.write_text(json.dumps(receipt))
        original_verify = MODULE.verify_snapshots
        original_digest = original_verify.__globals__["digest"]

        def replace_after_read(paths):
            admitted = original_verify(paths)
            replacement = dict(receipt, metrics={"value": 999})
            replacement["hash"] = original_digest(replacement)
            path.write_text(json.dumps(replacement))
            return admitted

        with mock.patch.object(MODULE, "verify_snapshots", side_effect=replace_after_read):
            self.assertEqual(0, MODULE.main(str(self.receipts), str(self.output), "engine"))
        row = json.loads(self.output.read_text())["results"][0]
        self.assertEqual({"value": 1}, row["metrics"])
        self.assertEqual(receipt["hash"], row["receipt"])
        self.assertEqual("UNVERIFIED", row["status"])

    def test_write_failures_preserve_existing_output(self):
        self.write_genesis("engine")
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation):
                self.output.write_bytes(b"historic artifact")
                with mock.patch.object(MODULE.os, operation, side_effect=OSError("synthetic failure")):
                    self.assertEqual(1, MODULE.main(str(self.receipts), str(self.output), "engine"))
                self.assertEqual(b"historic artifact", self.output.read_bytes())
                self.assertEqual([], list(self.root.glob(".*.tmp")))

    def test_bad_receipt_leaves_existing_artifact_intact(self):
        self.write_genesis("engine")
        (self.receipts / "001-invalid.json").write_text("[]")
        self.output.write_bytes(b"historic artifact")
        self.assertEqual(1, MODULE.main(str(self.receipts), str(self.output), "engine"))
        self.assertEqual(b"historic artifact", self.output.read_bytes())


if __name__ == "__main__":
    unittest.main()
