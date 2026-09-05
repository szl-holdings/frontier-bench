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
        self.context = SimpleNamespace(readme_bytes=self.readme, index_template_bytes=self.template,
                                       api=SimpleNamespace(space_info=Mock(return_value=SimpleNamespace(host=controller.SPACE_URL))))

    def write_bundle(self) -> None:
        (self.bundle / "README.md").write_bytes(self.readme)
        (self.bundle / "results.json").write_bytes(self.payload)
        (self.bundle / "index.html").write_bytes(controller.finalize_space_index(self.template, self.payload))

    def invoke(self, *, token: bool = True, expected: bytes | None = None, extra: list[str] | None = None,
               readmit_error: BaseException | None = None, publish_error: BaseException | None = None,
               anonymous_outcome: dict | None = None):
        output = io.StringIO()
        env = {key: value for key, value in os.environ.items() if key not in {"HF_TOKEN", controller.RECEIPT_KEY_ENV}}
        if token:
            env["HF_TOKEN"] = "hf_UNIT_TEST_NOT_A_REAL_CREDENTIAL"
        admission = {"state": "READMITTED_FROM_REVIEWED_SOURCE_RECEIPTS", "count": 0}
        with patch.dict(os.environ, env, clear=True), patch.object(publisher, "load_controller", return_value=controller), \
                patch.object(publisher, "readmit_payload", return_value=(self.payload if expected is None else expected, admission), side_effect=readmit_error) as readmit, \
                patch.object(publisher, "verify_anonymous_noop", return_value=anonymous_outcome) as anonymous, \
                patch.object(controller, "hub_preflight", return_value=self.context) as preflight, \
                patch.object(controller, "publish_and_witness", return_value={"changed": True, "commit": "a" * 40}, side_effect=publish_error) as publish, \
                contextlib.redirect_stdout(output):
            code = publisher.main(["--bundle-dir", str(self.bundle), "--report", str(self.report), *(extra or [])])
            report = json.loads(output.getvalue())
            self.assertNotIn("hf_UNIT_TEST_NOT_A_REAL_CREDENTIAL", output.getvalue())
            if not token:
                self.assertNotIn("HF_TOKEN", os.environ, "temporary cached credential must be removed from process environment")
            self.anonymous = anonymous
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
        self.anonymous.assert_not_called()

    def test_missing_hf_auth_does_not_read_cached_token_without_opt_in(self) -> None:
        cached = Mock(return_value="hf_CACHED_TEST_ONLY")
        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(get_token=cached)}):
            code, report, _, preflight, publish = self.invoke(token=False)
        self.assertEqual(code, controller.EXIT_HUB_AUTH)
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        cached.assert_not_called()
        preflight.assert_not_called()
        publish.assert_not_called()
        self.anonymous.assert_called_once()

    def test_anonymous_identical_bundle_noop_uses_no_authenticated_publication(self) -> None:
        code, report, readmit, preflight, publish = self.invoke(token=False, anonymous_outcome={"changed": False, "commit": "a" * 40, "publisher": "ANONYMOUS_READ_ONLY"})
        self.assertEqual(code, 0)
        self.assertEqual(report["remote_mutation"], "NO_CHANGE_WITNESSED")
        self.assertEqual(report["publication"]["publisher"], "ANONYMOUS_READ_ONLY")
        readmit.assert_called_once()
        self.anonymous.assert_called_once()
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

    def test_cached_auth_alias_uses_explicit_cached_path(self) -> None:
        cached = Mock(return_value="hf_CACHED_TEST_ONLY")
        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(get_token=cached)}):
            code, _, _, preflight, publish = self.invoke(token=False, extra=["--cached-auth"])
        self.assertEqual(code, 0)
        cached.assert_called_once_with()
        preflight.assert_called_once()
        publish.assert_called_once()
        self.anonymous.assert_not_called()

    def test_authenticated_provider_host_must_match_reviewed_static_host(self) -> None:
        self.context.api.space_info.return_value = SimpleNamespace(host="https://example.com")
        code, report, _, preflight, publish = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        preflight.assert_called_once()
        publish.assert_not_called()

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


class AnonymousWitnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="szl-anon-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.files = {"README.md": b"readme fixture", "index.html": b"index fixture", "results.json": b"{}"}
        self.info = SimpleNamespace(id=publisher.TARGET, sha="a" * 40, private=False, sdk="static", host=controller.SPACE_URL)
        self.api = Mock()
        self.api.space_info.return_value = self.info
        self.api.get_space_runtime.return_value = SimpleNamespace(stage="RUNNING")
        self.api.list_repo_files.return_value = list(self.files)
        self.factory = Mock(return_value=self.api)

    def invoke(self, *, remote: dict[str, bytes] | None = None, public: dict[str, bytes] | None = None):
        remote = self.files if remote is None else remote
        public = self.files if public is None else public

        def download(_repo, name, revision, token, _root):
            self.assertIs(token, False)
            self.assertEqual(revision, "a" * 40)
            return remote[name]

        def http(url, **_kwargs):
            name = "results.json" if "/results.json?" in url else "index.html"
            return public[name], {"Content-Type": "application/json" if name.endswith(".json") else "text/html"}

        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(HfApi=self.factory)}), \
                patch.object(controller, "_download_hub_file_strict", side_effect=download), \
                patch.object(controller, "http_get_bytes", side_effect=http):
            try:
                return publisher.verify_anonymous_noop(controller, self.files, self.root)
            finally:
                self.factory.assert_called_once_with(token=False)
                self.api.create_commit.assert_not_called()
                self.api.upload_folder.assert_not_called()
                self.api.restart_space.assert_not_called()
                self.api.whoami.assert_not_called()

    def test_matching_immutable_and_public_bytes_are_noop_verified(self) -> None:
        outcome = self.invoke()
        self.assertFalse(outcome["changed"])
        self.assertFalse(outcome["authenticated_write"])
        self.assertEqual(outcome["publisher"], "ANONYMOUS_READ_ONLY")
        self.assertEqual(self.api.space_info.call_count, 2)
        self.assertEqual(self.api.get_space_runtime.call_count, 2)

    def test_changed_immutable_bundle_requires_authentication_without_writing(self) -> None:
        self.assertIsNone(self.invoke(remote={**self.files, "README.md": b"different"}))

    def test_nonrunning_runtime_requires_authentication_without_restarting(self) -> None:
        self.api.get_space_runtime.return_value = SimpleNamespace(stage="STOPPED")
        self.assertIsNone(self.invoke())

    def test_public_bytes_mismatch_cannot_be_noop_success(self) -> None:
        with self.assertRaisesRegex(controller.BenchError, "differs from the verified bundle"):
            self.invoke(public={**self.files, "results.json": b"different"})

    def test_concurrent_head_change_cannot_be_noop_success(self) -> None:
        changed = SimpleNamespace(**{**vars(self.info), "sha": "b" * 40})
        self.api.space_info.side_effect = [self.info, changed]
        with self.assertRaisesRegex(controller.BenchError, "changed during the public witness"):
            self.invoke()

    def test_provider_host_allowlist_accepts_expected_hosts(self) -> None:
        for host in publisher.ALLOWED_LIVE_HOSTS:
            self.assertEqual(publisher._live_url(SimpleNamespace(host=f"https://{host}")), f"https://{host}/")

    def test_provider_host_rejects_userinfo_ports_and_other_components(self) -> None:
        for host in ("http://betterwithage-szl-bench-suite.static.hf.space", "https://example.com",
                     "https://betterwithage-szl-bench-suite.static.hf.space/other",
                     "https://betterwithage-szl-bench-suite.static.hf.space?redirect=1",
                     "https://user@betterwithage-szl-bench-suite.static.hf.space",
                     "https://betterwithage-szl-bench-suite.static.hf.space:443"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                publisher._live_url(SimpleNamespace(host=host))


if __name__ == "__main__":
    unittest.main()
