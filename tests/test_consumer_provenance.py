"""Synthetic producer-to-consumer regressions; no server or provider is started."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify.verifier import GENESIS, digest
from tools import merge_results, sync_results


def api_module():
    # Test endpoint functions, not FastAPI or an ASGI service.
    class FixtureApp:
        def __init__(self, **kwargs):
            pass

        def get(self, path):
            return lambda handler: handler

    fastapi = types.ModuleType("fastapi")
    fastapi.FastAPI = FixtureApp
    spec = importlib.util.spec_from_file_location("fixture_api", ROOT / "app/main.py")
    module = importlib.util.module_from_spec(spec)
    with unittest.mock.patch.dict(sys.modules, {"fastapi": fastapi}):
        spec.loader.exec_module(module)
    return module


def receipt(plane="engine", score=1):
    row = {"plane": plane, "status": "MEASURED", "machine": {
        "cpu": "synthetic", "gpu": "none", "ram_gb": 1},
        "measured_at": "2000-01-01T00:00:00Z", "method": "synthetic-only",
        "metrics": {"score": score}, "prev_hash": GENESIS}
    row["hash"] = digest(row)
    return row


def merged_fixture(directory):
    inputs, sources = {}, {}
    for index, plane in enumerate(merge_results.EXPECTED_PLANES, 1):
        row = receipt(plane, index)
        row["receipt"] = row.pop("hash")
        inputs[plane] = directory / (plane + ".json")
        inputs[plane].write_text(json.dumps({"generated_at": row["measured_at"],
                                           "count": 1, "results": [row]}), encoding="utf-8")
        sources[plane] = merge_results.EXPECTED_REPOSITORIES[plane] + "@" + str(index) * 40
    results, deployment = merge_results.build_payloads(inputs, sources)
    return {"results": results, "deployment": deployment}


class APIConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.api = api_module()
        self.path = self.root / "results.json"
        self.api.RESULTS = str(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_sync_output_remains_unverified_in_api(self):
        receipts = self.root / "receipts"
        receipts.mkdir()
        (receipts / "fixture.json").write_text(json.dumps(receipt()), encoding="utf-8")
        self.assertEqual(0, sync_results.main(str(receipts), str(self.path), "engine"))
        response = self.api.results()
        self.assertEqual("UNVERIFIED", response["state"])
        self.assertEqual("UNVERIFIED", response["authenticity"])
        self.assertEqual(1, response["count"])
        self.assertEqual({"score": 1}, response["results"][0]["metrics"])
        self.assertEqual("UNVERIFIED", response["results"][0]["status"])
        self.assertFalse(response["results_are_measured_only"])

    def test_caller_flags_cannot_authenticate_api_rows(self):
        for status in (None, "UNVERIFIED", "MEASURED", "VERIFIED"):
            with self.subTest(status=status):
                row = receipt()
                row.update(authenticated=True, receipt_admission="VERIFIED")
                if status is None:
                    row.pop("status")
                else:
                    row["status"] = status
                self.path.write_text(json.dumps({"generated_at": row["measured_at"],
                    "count": 1, "results": [row], "evidence_state": "MEASURED",
                    "provenance": {"authenticity": "VERIFIED", "results_are_measured_only": True}}))
                response = self.api.results()
                self.assertEqual("UNVERIFIED", response["state"])
                self.assertEqual("UNVERIFIED", response["results"][0]["status"])
                self.assertNotIn("authenticated", response["results"][0])
                self.assertNotIn("receipt_admission", response["results"][0])

    def test_valid_empty_and_other_plane_inputs_are_honestly_empty(self):
        for rows in ([], [receipt("retrieval")]):
            self.path.write_text(json.dumps({"generated_at": "2000-01-01T00:00:00Z",
                                            "count": len(rows), "results": rows}))
            response = self.api.results()
            self.assertEqual("EMPTY_HONEST", response["state"])
            self.assertEqual([], response["results"])
            self.assertEqual(0, response["count"])

    def test_missing_and_malformed_input_is_unavailable_not_empty(self):
        self.assertEqual("UNAVAILABLE", self.api.results()["state"])
        for payload in ([], {}, {"results": []}, {"results": [], "count": True},
                        {"results": [None], "count": 1, "generated_at": "fixture"},
                        {"results": [receipt()], "count": 0, "generated_at": "fixture"}):
            with self.subTest(payload=payload):
                self.path.write_text(json.dumps(payload))
                response = self.api.results()
                self.assertEqual("UNAVAILABLE", response["state"])
                self.assertEqual([], response["results"])
                self.assertIsNone(response["generated_at"])
        self.path.write_bytes(b"{not-json")
        self.assertEqual("UNAVAILABLE", self.api.results()["state"])


class BrowserConsumerTests(unittest.TestCase):
    def test_merged_output_and_forged_flags_never_become_verified(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Path(raw) / "fixture.json"
            fixture.write_text(json.dumps(merged_fixture(Path(raw))), encoding="utf-8")
            completed = subprocess.run(
                ["node", str(ROOT / "tests/consumer_provenance.mjs"), str(fixture)],
                cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertIn("consumer provenance: 9 cases passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
