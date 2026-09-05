"""Publication adapter tests: no real network, credentials, or provider mutation."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bench_space_publisher_tested", ROOT / "tools" / "publish_space.py")
publisher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)
controller = publisher.load_controller()


def payload_fixture() -> dict:
    return {
        "schema_version": "szl-bench-results/v2", "generated_at": "2026-09-04T00:00:00Z",
        "data_state": "EMPTY_HONEST", "count": 0,
        "results_sha256": controller.sha256_bytes(controller.canonical_json_bytes([])),
        "results": [],
        "sources": {
            spec.plane: {"repo": spec.repo, "revision": spec.revision, "genesis": spec.genesis,
                         "receipt_count": 1, "receipt_head": spec.genesis,
                         "integrity": "VERIFIED_UNSIGNED_EMPTY_CHAIN"}
            for spec in controller.REPOS
        },
    }


class PublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="szl-publisher-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.report = self.root / "report.json"
        self.template = (publisher.CONTROLLER_DIR / "szl-bench-suite.index.html").read_bytes()
        self.readme = (publisher.CONTROLLER_DIR / "szl-bench-suite.README.md").read_bytes()
        self.payload = controller.pretty_json_bytes(payload_fixture())
        self.write_bundle()
        self.context = SimpleNamespace(readme_bytes=self.readme, index_template_bytes=self.template)

    def write_bundle(self) -> None:
        (self.bundle / "README.md").write_bytes(self.readme)
        (self.bundle / "results.json").write_bytes(self.payload)
        (self.bundle / "index.html").write_bytes(controller.finalize_space_index(self.template, self.payload))

    def invoke(self, *, token: bool = True, expected: bytes | None = None, extra: list[str] | None = None,
               readmit_error: BaseException | None = None, publish_error: BaseException | None = None):
        output = io.StringIO()
        env = {key: value for key, value in os.environ.items() if key not in {"HF_TOKEN", controller.RECEIPT_KEY_ENV}}
        if token:
            env["HF_TOKEN"] = "hf_UNIT_TEST_NOT_A_REAL_CREDENTIAL"
        admission = {"state": "READMITTED_FROM_REVIEWED_SOURCE_RECEIPTS", "count": 0}
        with patch.dict(os.environ, env, clear=True), patch.object(publisher, "load_controller", return_value=controller), \
                patch.object(publisher, "readmit_payload", return_value=(self.payload if expected is None else expected, admission), side_effect=readmit_error) as readmit, \
                patch.object(controller, "hub_preflight", return_value=self.context) as preflight, \
                patch.object(controller, "publish_and_witness", return_value={"changed": True, "commit": "a" * 40}, side_effect=publish_error) as publish, \
                contextlib.redirect_stdout(output):
            code = publisher.main(["--bundle-dir", str(self.bundle), "--report", str(self.report), *(extra or [])])
            report = json.loads(output.getvalue())
            self.assertNotIn("hf_UNIT_TEST_NOT_A_REAL_CREDENTIAL", output.getvalue())
            if not token:
                self.assertNotIn("HF_TOKEN", os.environ, "temporary cached credential must be removed from process environment")
        return code, report, readmit, preflight, publish

    def test_valid_bundle_uses_readmission_then_controller_publication(self) -> None:
        code, report, readmit, preflight, publish = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(report["state"], "PUBLISHED_EVIDENCE_SURFACE_OPERATIONAL")
        self.assertEqual(report["measurements"], "NOT_PERFORMED_BY_PUBLISHER")
        readmit.assert_called_once()
        preflight.assert_called_once()
        args = preflight.call_args.args[0]
        self.assertEqual(args.expected_hf_user, "betterwithage")
        self.assertEqual(args.hf_token_env, "HF_TOKEN")
        publish.assert_called_once_with(self.context, self.payload, provider_timeout=600.0, public_http_deadline=180.0)
        self.assertEqual(json.loads(self.report.read_bytes())["state"], report["state"])

    def test_changed_index_aborts_before_readmission_or_provider(self) -> None:
        with (self.bundle / "index.html").open("ab") as stream:
            stream.write(b"<!-- changed -->")
        code, report, readmit, preflight, publish = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        readmit.assert_not_called()
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_changed_readme_aborts_before_provider(self) -> None:
        (self.bundle / "README.md").write_bytes(b"---\nsdk: static\n---\nunreviewed")
        code, _, _, preflight, publish = self.invoke()
        self.assertNotEqual(code, 0)
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_extra_bundle_file_aborts_before_provider(self) -> None:
        (self.bundle / "extra.txt").write_text("not managed", encoding="utf-8")
        code, _, _, preflight, publish = self.invoke()
        self.assertNotEqual(code, 0)
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_source_pin_mismatch_aborts_before_provider(self) -> None:
        data = payload_fixture()
        data["sources"]["engine"]["revision"] = "0" * 40
        self.payload = controller.pretty_json_bytes(data)
        self.write_bundle()
        code, _, readmit, preflight, publish = self.invoke()
        self.assertNotEqual(code, 0)
        readmit.assert_not_called()
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_readmitted_bytes_must_match_even_when_bundle_schema_and_digest_match(self) -> None:
        code, report, readmit, preflight, publish = self.invoke(expected=b'{"different":true}\n')
        self.assertEqual(code, controller.EXIT_RESULT)
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        readmit.assert_called_once()
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_missing_measurement_key_fails_before_hub_preflight(self) -> None:
        error = controller.BenchError("receipt_auth", "MEASURED receipt requires the protected key", controller.EXIT_RECEIPT)
        code, report, _, preflight, publish = self.invoke(readmit_error=error)
        self.assertEqual(code, controller.EXIT_RECEIPT)
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_missing_hf_auth_does_not_read_cached_token_without_opt_in(self) -> None:
        cached = Mock(return_value="hf_CACHED_TEST_ONLY")
        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(get_token=cached)}):
            code, report, _, preflight, publish = self.invoke(token=False)
        self.assertEqual(code, controller.EXIT_HUB_AUTH)
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        cached.assert_not_called()
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_explicit_cached_token_is_used_without_login_or_persistence(self) -> None:
        cached = Mock(return_value="hf_CACHED_TEST_ONLY")
        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(get_token=cached)}):
            code, report, _, preflight, publish = self.invoke(token=False, extra=["--use-cached-auth"])
        self.assertEqual(code, 0)
        cached.assert_called_once_with()
        preflight.assert_called_once()
        publish.assert_called_once()
        self.assertNotIn("hf_CACHED_TEST_ONLY", json.dumps(report))

    def test_provider_failure_preserves_compensation_detail(self) -> None:
        error = controller.BenchError("hub_rollback", "publication witness failed", controller.EXIT_PROVIDER,
                                      detail={"compensation": {"state": "PUBLISH_FAILED_ROLLBACK_VERIFIED"}})
        code, report, _, _, publish = self.invoke(publish_error=error)
        self.assertEqual(code, controller.EXIT_PROVIDER)
        publish.assert_called_once()
        self.assertEqual(report["failure"]["detail"]["compensation"]["state"], "PUBLISH_FAILED_ROLLBACK_VERIFIED")
        self.assertEqual(report["remote_mutation"], "SEE_FAILURE_DETAIL_OR_UNKNOWN_AFTER_ATTEMPT")

    def test_readmission_clears_key_when_receipt_verification_raises(self) -> None:
        key = bytearray(b"x" * 32)
        snapshots = [SimpleNamespace(spec=spec, record={}) for spec in controller.REPOS]
        with patch.object(controller, "resolve_trusted_executable", return_value="/trusted/git"), \
                patch.object(controller, "validate_git_helper_root", return_value={}), \
                patch.object(controller, "materialize_source", side_effect=snapshots), \
                patch.object(controller, "load_receipt_auth_key", return_value=key), \
                patch.object(controller, "verify_receipts", side_effect=ValueError("fixture failure")):
            with self.assertRaisesRegex(ValueError, "fixture failure"):
                publisher.readmit_payload(controller, self.root)
        self.assertEqual(key, bytearray(32))


if __name__ == "__main__":
    unittest.main()
