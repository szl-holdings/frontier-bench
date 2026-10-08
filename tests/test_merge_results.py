import json
import hashlib
import importlib.util
import tempfile
import sys
import unittest
from unittest import mock
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
                                "machine": {"cpu": "test", "gpu": "test", "ram_gb": 1},
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
        self.assertEqual("SZLHOLDINGS/szl-bench-suite", deployment["target"])
        self.assertFalse(deployment["truth"]["results_are_measured_only"])
        self.assertEqual("UNVERIFIED", results["evidence_state"])
        for row in results["results"]:
            self.assertRegex(row["source_revision"], r"^[0-9a-f]{40}$")
            self.assertEqual("UNVERIFIED", row["status"])

    def test_forged_admission_metadata_does_not_authenticate_rows(self):
        payload = json.loads(self.inputs["engine"].read_text())
        payload["truth"] = {"results_are_measured_only": True, "authenticated": True}
        payload["results"][0].update(status="MEASURED", authenticated=True, receipt_admission="VERIFIED")
        self.inputs["engine"].write_text(json.dumps(payload))
        results, deployment = build_payloads(self.inputs, self.sources)
        self.assertEqual("UNVERIFIED", results["results"][0]["status"])
        self.assertNotIn("authenticated", results["results"][0])
        self.assertEqual("NOT_CHECKED", deployment["truth"]["receipt_admission"])
        self.assertFalse(deployment["truth"]["results_are_measured_only"])

    def test_inputs_are_read_once_for_timestamp_rows_and_digest(self):
        read_bytes = Path.read_bytes
        reads = []
        def tracked_read(path):
            reads.append(path)
            if reads.count(path) > 1:
                raise AssertionError("input was reopened")
            return read_bytes(path)
        with mock.patch.object(Path, "read_bytes", tracked_read):
            results, _ = build_payloads(self.inputs, self.sources)
        self.assertEqual(3, len(reads))
        for source in results["sources"]:
            self.assertEqual(hashlib.sha256(read_bytes(self.inputs[source["plane"]])).hexdigest(),
                             source["input_results_sha256"])

    def test_cli_reads_each_input_once_and_keeps_unverified_status(self):
        output, deployment_output = self.root / "combined.json", self.root / "deployment.json"
        argv = ["merge_results.py", "--output", str(output), "--deployment-output", str(deployment_output)]
        for plane in MODULE.EXPECTED_PLANES:
            argv.extend(["--input", f"{plane}={self.inputs[plane]}", "--source", f"{plane}={self.sources[plane]}"])
        load = MODULE._load_payload
        with mock.patch.object(sys, "argv", argv), mock.patch.object(MODULE, "_load_payload", wraps=load) as reader:
            self.assertEqual(0, MODULE.main())
            self.assertEqual(3, reader.call_count)
        self.assertEqual("UNVERIFIED", json.loads(output.read_text())["evidence_state"])
        self.assertFalse(json.loads(deployment_output.read_text())["truth"]["results_are_measured_only"])

        self.inputs["engine"].write_text('{"results": [], "count": 0, "generated_at": "invalid"}')
        output.write_bytes(b"historic result")
        deployment_output.write_bytes(b"historic deployment")
        with mock.patch.object(sys, "argv", argv):
            self.assertEqual(2, MODULE.main())
        self.assertEqual(b"historic result", output.read_bytes())
        self.assertEqual(b"historic deployment", deployment_output.read_bytes())

    def test_rejects_invalid_input_values(self):
        path = self.inputs["engine"]
        baseline = path.read_text()
        for field, value in (("count", True), ("generated_at", "yesterday")):
            with self.subTest(field=field):
                payload = json.loads(baseline)
                payload[field] = value
                path.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    build_payloads(self.inputs, self.sources)
        for field, value in (("metrics", {"value": float("nan")}), ("metrics", {"value": True}),
                             ("machine", {"cpu": "test", "gpu": "none", "ram_gb": -1}),
                             ("measured_at", "2026-09-04T00:00:00"), ("method", [])):
            with self.subTest(field=field, value=value):
                payload = json.loads(baseline)
                payload["results"][0][field] = value
                path.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    build_payloads(self.inputs, self.sources)

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


    def _run_cli(self, output: Path, deployment_output: Path) -> int:
        argv = ["merge_results.py", "--output", str(output),
                "--deployment-output", str(deployment_output)]
        for plane in MODULE.EXPECTED_PLANES:
            argv.extend(["--input", f"{plane}={self.inputs[plane]}",
                         "--source", f"{plane}={self.sources[plane]}"])
        with mock.patch.object(sys, "argv", argv):
            return MODULE.main()

    def test_cli_rejects_same_output_without_overwriting_existing_bytes(self):
        output = self.root / "combined.json"
        output.write_bytes(b"historic result")
        self.assertEqual(2, self._run_cli(output, output))
        self.assertEqual(b"historic result", output.read_bytes())

    def test_cli_rejects_normalized_output_alias_before_creating_file(self):
        directory = self.root / "nested"
        directory.mkdir()
        output = self.root / "combined.json"
        alias = directory / ".." / output.name
        self.assertEqual(2, self._run_cli(output, alias))
        self.assertFalse(output.exists())

    def test_cli_rejects_nested_outputs_before_creating_either_path(self):
        for parent_is_result in (True, False):
            with self.subTest(parent_is_result=parent_is_result):
                parent = self.root / f"output-{parent_is_result}"
                child = parent / "deployment.json"
                outputs = (parent, child) if parent_is_result else (child, parent)
                self.assertEqual(2, self._run_cli(*outputs))
                self.assertFalse(parent.exists())

    def test_cli_rejects_hardlinked_output_files_without_replacing_them(self):
        output = self.root / "combined.json"
        deployment_output = self.root / "deployment.json"
        output.write_bytes(b"historic result")
        try:
            deployment_output.hardlink_to(output)
        except OSError as error:
            self.skipTest(f"hardlinks unavailable: {error}")
        self.assertEqual(2, self._run_cli(output, deployment_output))
        self.assertEqual(b"historic result", output.read_bytes())
        self.assertEqual(b"historic result", deployment_output.read_bytes())
        self.assertTrue(output.samefile(deployment_output))


if __name__ == "__main__":
    unittest.main()
