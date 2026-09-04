import importlib.util
import json
import tempfile
import unittest
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
        receipt["hash"] = MODULE.verify.__globals__["digest"](receipt)
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


if __name__ == "__main__":
    unittest.main()
