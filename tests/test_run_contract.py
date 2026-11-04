"""Raw-run contract tests.  No network, model, GPU, or provider writes."""
from __future__ import annotations

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock as mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.client import chat_completion
from harness.engine_registry import REGISTRY
from harness.run_contract import (
    RUN_CONTRACT_SCHEMA,
    canonical_sha256,
    build_run_contract,
    failure_summary,
    load_workload,
    parse_model_revisions,
)
from harness.runner import main


CONFIG = ROOT / "configs" / "prompts.json"
MOCK_CONFIG = ROOT / "tests" / "fixtures" / "mock-workload.json"
SOURCE_REVISION = "a" * 40
MODEL_REVISION = "b" * 40


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        events = [
            {"choices": [{"delta": {"content": "fixture"}}]},
            {"choices": [], "usage": {"completion_tokens": 1}},
        ]
        for event in events:
            yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


class WorkloadContractTests(unittest.TestCase):
    def test_committed_workloads_have_exact_content_digests(self):
        for path in (CONFIG, MOCK_CONFIG):
            with self.subTest(path=path):
                workload = load_workload(path, REGISTRY)
                self.assertEqual(len(workload.config_sha256), 64)
                self.assertEqual(
                    workload.identity["content_sha256"],
                    canonical_sha256(list(workload.prompts)),
                )
                self.assertEqual(set(REGISTRY), set(workload.model_by_engine))

    def test_prompt_tamper_and_registry_drift_fail_closed(self):
        payload = json.loads(CONFIG.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workload.json"
            tampered = copy.deepcopy(payload)
            tampered["prompts"][0]["text"] += " changed"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "content digest mismatch"):
                load_workload(path, REGISTRY)

            missing_engine = copy.deepcopy(payload)
            del missing_engine["model_by_engine"]["vllm"]
            path.write_text(json.dumps(missing_engine), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exact engine registry"):
                load_workload(path, REGISTRY)

    def test_duplicate_json_keys_ids_and_texts_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workload.json"
            path.write_text('{"schema":"one","schema":"two"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                load_workload(path, REGISTRY)

            payload = json.loads(CONFIG.read_text(encoding="utf-8"))
            payload["prompts"].append(copy.deepcopy(payload["prompts"][0]))
            payload["workload"]["content_sha256"] = canonical_sha256(payload["prompts"])
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be unique"):
                load_workload(path, REGISTRY)

    def test_model_revision_mapping_is_exact(self):
        self.assertEqual(
            {"vllm": MODEL_REVISION},
            parse_model_revisions([f"vllm={MODEL_REVISION}"], ["vllm"]),
        )
        for values in (
            [],
            [f"other={MODEL_REVISION}"],
            ["vllm=SHORT"],
            [f"vllm={MODEL_REVISION}", f"vllm={MODEL_REVISION}"],
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                parse_model_revisions(values, ["vllm"])

    def test_contract_is_deterministic_and_never_promotion_eligible(self):
        workload = load_workload(MOCK_CONFIG, REGISTRY)
        arguments = {
            "source_revision": SOURCE_REVISION,
            "configured_engines": ["vllm"],
            "model_revisions": {"vllm": MODEL_REVISION},
            "repeats": 2,
            "max_tokens": 16,
            "seed": 7,
        }
        first = build_run_contract(workload, **arguments)
        second = build_run_contract(workload, **arguments)
        self.assertEqual(first, second)
        self.assertEqual(RUN_CONTRACT_SCHEMA, first["schema"])
        digest = first.pop("contract_sha256")
        self.assertEqual(digest, canonical_sha256(first))
        self.assertFalse(first["evaluation_policy"]["promotion_eligible"])
        self.assertIsNone(first["evaluation_policy"]["thresholds"])
        self.assertEqual("CALLER_DECLARED_NOT_READ_BACK", first["models"][0]["identity_state"])

    def test_contract_rejects_unbound_source_seed_or_model(self):
        workload = load_workload(MOCK_CONFIG, REGISTRY)
        base = {
            "source_revision": SOURCE_REVISION,
            "configured_engines": ["vllm"],
            "model_revisions": {"vllm": MODEL_REVISION},
            "repeats": 2,
            "max_tokens": 16,
            "seed": 7,
        }
        for field, value in (
            ("source_revision", "main"),
            ("seed", -1),
            ("repeats", 0),
            ("max_tokens", 0),
            ("model_revisions", {}),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_run_contract(workload, **dict(base, **{field: value}))


class ExecutionBoundaryTests(unittest.TestCase):
    def test_seed_is_sent_to_the_compatible_endpoint(self):
        captured = {}

        def open_request(request, timeout):
            captured.update(json.loads(request.data.decode("utf-8")))
            return Response()

        with mock.patch("harness.client.urllib.request.urlopen", side_effect=open_request):
            result = chat_completion(
                "http://fixture.invalid",
                "frontier-bench/mock-vllm",
                "fixture",
                seed=19,
            )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(19, captured["seed"])
        self.assertTrue(captured["stream"])
        self.assertEqual(0.0, captured["temperature"])

    def test_failure_summary_reconciles_engine_and_request_counts(self):
        summary = failure_summary(
            [
                {
                    "status": "MEASURED",
                    "summary": {"n_requests": 3, "n_success": 2, "n_failed": 1},
                },
                {"status": "BLOCKED"},
                {"status": "INVALID"},
            ]
        )
        self.assertEqual(3, summary["engines_total"])
        self.assertEqual(3, summary["requests_total"])
        self.assertEqual(1, summary["requests_failed"])
        with self.assertRaisesRegex(ValueError, "do not reconcile"):
            failure_summary(
                [{"status": "FAILED", "summary": {"n_requests": 1, "n_success": 1, "n_failed": 1}}]
            )

    def test_cli_rejects_missing_model_revision_before_health_check(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"VLLM_ENDPOINT": "http://fixture.invalid"}, clear=True
        ), mock.patch("harness.runner.health_check") as health, contextlib.redirect_stderr(
            io.StringIO()
        ):
            output = Path(directory) / "result.json"
            code = main(
                [
                    "--config",
                    str(MOCK_CONFIG),
                    "--source-revision",
                    SOURCE_REVISION,
                    "--seed",
                    "0",
                    "--out",
                    str(output),
                ]
            )
        self.assertEqual(2, code)
        self.assertFalse(output.exists())
        health.assert_not_called()

    def test_cli_emits_bound_raw_run_and_preserves_failure_evidence(self):
        measured = {
            "engine": "vllm",
            "status": "MEASURED",
            "summary": {
                "engine": "vllm",
                "model": "frontier-bench/mock-vllm",
                "n_requests": 2,
                "n_success": 1,
                "n_failed": 1,
                "errors": ["synthetic failure"],
            },
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"VLLM_ENDPOINT": "http://fixture.invalid"}, clear=True
        ), mock.patch(
            "harness.runner.run_engine", return_value=measured
        ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            output = Path(directory) / "result.json"
            code = main(
                [
                    "--config",
                    str(MOCK_CONFIG),
                    "--source-revision",
                    SOURCE_REVISION,
                    "--model-revision",
                    f"vllm={MODEL_REVISION}",
                    "--seed",
                    "0",
                    "--repeats",
                    "2",
                    "--out",
                    str(output),
                ]
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(0, code)
        self.assertEqual("szl.frontier-bench.raw-run/v1", payload["schema"])
        self.assertEqual(1, payload["failure_summary"]["requests_failed"])
        measured_row = next(row for row in payload["results"] if row["status"] == "MEASURED")
        self.assertEqual("synthetic failure", measured_row["summary"]["errors"][0])
        self.assertEqual(
            payload["run_contract"]["contract_sha256"],
            measured_row["contract_sha256"],
        )
        self.assertFalse(payload["run_contract"]["evaluation_policy"]["promotion_eligible"])


if __name__ == "__main__":
    unittest.main()
