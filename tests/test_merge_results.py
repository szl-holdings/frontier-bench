import json
import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "merge_results.py"
SPEC = importlib.util.spec_from_file_location("merge_results", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
build_payloads = MODULE.build_payloads


class MergeResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inputs = {}
        for index, plane in enumerate(("engine", "retrieval", "quant"), start=1):
            path = self.root / f"{plane}.json"
            path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-09-04T00:00:00Z",
                        "count": 1,
                        "results": [
                            {
                                "plane": plane,
                                "machine": {"gpu": "test"},
                                "measured_at": f"2026-09-04T00:00:0{index}Z",
                                "method": "test",
                                "metrics": {"value": index},
                                "receipt": f"{index:064x}",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.inputs[plane] = path
        self.sources = {
            "engine": "szl-holdings/frontier-bench@" + "1" * 40,
            "retrieval": "szl-holdings/retrieval-bench@" + "2" * 40,
            "quant": "szl-holdings/quant-curve@" + "3" * 40,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_merges_all_planes_and_binds_sources(self) -> None:
        results, deployment = build_payloads(
            self.inputs, self.sources, "2026-09-04T00:01:00Z"
        )
        self.assertEqual(3, results["count"])
        self.assertEqual(["engine", "retrieval", "quant"], [r["plane"] for r in results["results"]])
        self.assertEqual(3, len(results["sources"]))
        self.assertEqual("betterwithage/szl-bench-suite", deployment["target"])
        self.assertTrue(deployment["truth"]["results_are_measured_only"])
        for row in results["results"]:
            self.assertRegex(row["source_revision"], r"^[0-9a-f]{40}$")

    def test_rejects_cross_plane_rows(self) -> None:
        payload = json.loads(self.inputs["retrieval"].read_text(encoding="utf-8"))
        payload["results"][0]["plane"] = "engine"
        self.inputs["retrieval"].write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cross-plane"):
            build_payloads(self.inputs, self.sources, "2026-09-04T00:01:00Z")

    def test_rejects_duplicate_receipts(self) -> None:
        engine = json.loads(self.inputs["engine"].read_text(encoding="utf-8"))
        retrieval = json.loads(self.inputs["retrieval"].read_text(encoding="utf-8"))
        retrieval["results"][0]["receipt"] = engine["results"][0]["receipt"]
        self.inputs["retrieval"].write_text(json.dumps(retrieval), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate receipt"):
            build_payloads(self.inputs, self.sources, "2026-09-04T00:01:00Z")

    def test_rejects_wrong_source_repository(self) -> None:
        self.sources["quant"] = "szl-holdings/retrieval-bench@" + "3" * 40
        with self.assertRaisesRegex(ValueError, "quant source must be"):
            build_payloads(self.inputs, self.sources, "2026-09-04T00:01:00Z")


if __name__ == "__main__":
    unittest.main()
