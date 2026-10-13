"""Publication adapter tests: no real network, credentials, or provider mutation."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
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
        self.assets = controller.load_space_static_assets(publisher.CONTROLLER_DIR / "szl-bench-suite.index.html",
                                                          phase="test", exit_code=1)
        self.write_bundle()
        self.context = SimpleNamespace(readme_bytes=self.readme, index_template_bytes=self.template,
                                       static_asset_bytes=dict(self.assets),
                                       api=SimpleNamespace(space_info=Mock(return_value=SimpleNamespace(host=controller.SPACE_URL))))

    def write_bundle(self) -> None:
        (self.bundle / "README.md").write_bytes(self.readme)
        (self.bundle / "results.json").write_bytes(self.payload)
        (self.bundle / "index.html").write_bytes(controller.finalize_space_index(self.template, self.payload))
        for name, data in self.assets.items():
            target = self.bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def invoke(self, *, token: bool = True, expected: bytes | None = None, extra: list[str] | None = None,
               readmit_error: BaseException | None = None, publish_error: BaseException | None = None,
               anonymous_outcome: dict | None = None, anonymous_error: BaseException | None = None):
        output = io.StringIO()
        env = {key: value for key, value in os.environ.items() if key not in {"HF_TOKEN", controller.RECEIPT_KEY_ENV}}
        if token:
            env["HF_TOKEN"] = "hf_UNIT_TEST_NOT_A_REAL_CREDENTIAL"
        admission = {"state": "READMITTED_FROM_REVIEWED_SOURCE_RECEIPTS", "count": 0}
        with patch.dict(os.environ, env, clear=True), patch.object(publisher, "load_controller", return_value=controller), \
                patch.object(publisher, "readmit_payload", return_value=(self.payload if expected is None else expected, admission), side_effect=readmit_error) as readmit, \
                patch.object(publisher, "verify_anonymous_noop", return_value=anonymous_outcome, side_effect=anonymous_error) as anonymous, \
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
        self.assertEqual(report["mode"], "PUBLISH")
        self.assertEqual(report["space_url"], f"{controller.SPACE_URL}/index.html")
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

    def test_large_results_bundle_keeps_strict_parsing_and_fresh_readmission(self) -> None:
        canonical = self.payload
        self.payload += b" " * (controller.MAX_JSON_BYTES - len(self.payload) + 1)
        self.assertLessEqual(len(self.payload), controller.MAX_HTTP_BYTES)
        self.write_bundle()
        files, parsed = publisher.read_bundle(controller, self.bundle)
        self.assertEqual(parsed, payload_fixture())
        self.assertEqual(files["results.json"], self.payload)
        # The receipt parser's default ceiling is deliberately unchanged.
        with self.assertRaises(controller.BenchError):
            controller.strict_json_from_bytes(self.payload, source="receipt.json")
        # Parsing a valid larger JSON document must not bypass canonical source
        # re-admission: these padded bytes still differ from the real export.
        code, report, readmit, preflight, publish = self.invoke(expected=canonical)
        self.assertEqual(code, controller.EXIT_RESULT)
        self.assertEqual(report["failure"]["phase"], "bundle_admission")
        readmit.assert_called_once()
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_over_http_limit_results_fail_before_parsing(self) -> None:
        self.payload += b" " * (controller.MAX_HTTP_BYTES - len(self.payload) + 1)
        self.write_bundle()
        with patch.object(controller, "strict_json_from_bytes") as parser:
            with self.assertRaises(controller.BenchError):
                publisher.read_bundle(controller, self.bundle)
        parser.assert_not_called()

    def test_bundle_parser_retains_all_strict_json_guards(self) -> None:
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[' * (controller.MAX_JSON_DEPTH + 2) + b'0' + b']' * (controller.MAX_JSON_DEPTH + 2)):
            with self.subTest(data=data[:32]), self.assertRaises(controller.BenchError):
                controller.strict_json_from_bytes(data, source="bundle/results.json", max_bytes=controller.MAX_HTTP_BYTES)

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

    def test_valid_bundle_carries_exact_reviewed_szl_assets(self) -> None:
        files, _ = publisher.read_bundle(controller, self.bundle)
        self.assertEqual(set(files), publisher.BUNDLE_FILES | set(controller.SPACE_STATIC_ASSETS))
        for name, digest in controller.SPACE_STATIC_ASSETS.items():
            self.assertEqual(controller.sha256_bytes(files[name]), digest)

    def test_tampered_missing_or_extra_szl_asset_aborts_before_provider(self) -> None:
        szl = self.bundle / "szl"
        mutations = {
            "tampered stylesheet": lambda: (szl / "szl-design-system.css").write_bytes(b":root { --accent: red; }\n"),
            "missing stylesheet": lambda: (szl / "szl-design-system.css").unlink(),
            "unreviewed webfont": lambda: (szl / "Inter-latin.woff2").write_bytes(b"unreviewed"),
            "unreviewed logo": lambda: (szl / "logos" / "szl_logo_primary.svg").write_bytes(b"<svg/>"),
            "unreviewed directory": lambda: (szl / "fonts").mkdir(),
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                shutil.rmtree(self.bundle)
                self.bundle.mkdir()
                self.write_bundle()
                mutate()
                code, report, readmit, preflight, publish = self.invoke()
                self.assertEqual(code, controller.EXIT_RESULT)
                self.assertEqual(report["failure"]["phase"], "bundle_admission")
                readmit.assert_not_called()
                preflight.assert_not_called()
                publish.assert_not_called()
                self.report.unlink()

    def test_provider_context_assets_must_equal_bundle_assets(self) -> None:
        self.context.static_asset_bytes = {**self.assets, "szl/szl-design-system.css": b"different"}
        code, report, _, preflight, publish = self.invoke()
        self.assertEqual(code, controller.EXIT_RESULT)
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        preflight.assert_called_once()
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

    def test_verify_only_never_resolves_write_authority_or_falls_back(self) -> None:
        outcome = {"changed": False, "commit": "a" * 40, "publisher": "ANONYMOUS_READ_ONLY"}
        provider_error = controller.BenchError("anonymous_witness", "provider witness failed", controller.EXIT_PROVIDER)
        cases = (("verified", outcome, None), ("different", None, None), ("provider_error", None, provider_error))
        original_get = os.environ.get

        def without_write_token(key, default=None):
            self.assertNotEqual(key, "HF_TOKEN", "verification-only must not look up write credentials")
            return original_get(key, default)

        for token in (False, True):
            for cached_flag in (False, True):
                for label, anonymous_outcome, anonymous_error in cases:
                    with self.subTest(token_present=token, cached_flag=cached_flag, outcome=label):
                        self.report = self.root / f"verify-{token}-{cached_flag}-{label}.json"
                        cached = Mock(side_effect=AssertionError("verification-only must not resolve cached credentials"))
                        extra = ["--verify-only", *(["--use-cached-auth"] if cached_flag else [])]
                        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(get_token=cached)}), \
                                patch.object(os.environ, "get", side_effect=without_write_token):
                            code, report, readmit, preflight, publish = self.invoke(
                                token=token, extra=extra, anonymous_outcome=anonymous_outcome,
                                anonymous_error=anonymous_error)
                        self.assertEqual(report["mode"], "VERIFY_ONLY")
                        readmit.assert_called_once()
                        self.anonymous.assert_called_once()
                        cached.assert_not_called()
                        preflight.assert_not_called()
                        publish.assert_not_called()
                        if label == "verified":
                            self.assertEqual(code, 0)
                            self.assertEqual(report["state"], "PUBLISHED_EVIDENCE_SURFACE_OPERATIONAL")
                            self.assertEqual(report["remote_mutation"], "NO_CHANGE_WITNESSED")
                            self.assertEqual(report["publication"], outcome)
                        else:
                            self.assertEqual(code, controller.EXIT_PROVIDER)
                            self.assertEqual(report["state"], "FAILED_CLOSED")
                            self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
                            self.assertEqual(report["failure"]["phase"],
                                             "verification_only" if label == "different" else "anonymous_witness")
                        self.assertEqual(json.loads(self.report.read_bytes())["mode"], "VERIFY_ONLY")

    def test_verify_only_preserves_fresh_source_readmission(self) -> None:
        code, report, readmit, preflight, publish = self.invoke(extra=["--verify-only"], expected=b"different")
        self.assertEqual(code, controller.EXIT_RESULT)
        self.assertEqual(report["mode"], "VERIFY_ONLY")
        self.assertEqual(report["failure"]["phase"], "bundle_admission")
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        readmit.assert_called_once()
        self.anonymous.assert_not_called()
        preflight.assert_not_called()
        publish.assert_not_called()

    def test_cached_auth_alias_uses_explicit_cached_path(self) -> None:
        cached = Mock(return_value="hf_CACHED_TEST_ONLY")
        with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(get_token=cached)}):
            code, _, _, preflight, publish = self.invoke(token=False, extra=["--cached-auth"])
        self.assertEqual(code, 0)
        cached.assert_called_once_with()
        preflight.assert_called_once()
        publish.assert_called_once()
        self.anonymous.assert_called_once()

    def test_available_credentials_are_not_resolved_for_identical_healthy_bundle(self) -> None:
        for token, extra in ((True, []), (False, ["--cached-auth"])):
            with self.subTest(token_present=token, extra=extra):
                self.report = self.root / ("environment-report.json" if token else "cached-report.json")
                cached = Mock(side_effect=AssertionError("no-op must not read cached credentials"))
                with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(get_token=cached)}):
                    code, report, _, preflight, publish = self.invoke(token=token, extra=extra,
                        anonymous_outcome={"changed": False, "commit": "a" * 40, "publisher": "ANONYMOUS_READ_ONLY"})
                self.assertEqual(code, 0)
                self.assertEqual(report["remote_mutation"], "NO_CHANGE_WITNESSED")
                cached.assert_not_called()
                preflight.assert_not_called()
                publish.assert_not_called()
                self.anonymous.assert_called_once()

    def test_existing_report_parent_is_never_chmodded(self) -> None:
        with patch.object(controller.os, "chmod", wraps=os.chmod) as chmod:
            code, _, _, _, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertNotIn(self.root, [Path(call.args[0]) for call in chmod.call_args_list])

    @unittest.skipIf(os.name == "nt", "POSIX directory permission regression")
    def test_shared_report_parent_preserves_permissions(self) -> None:
        self.root.chmod(0o775)
        code, _, _, _, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o775)
        self.assertEqual(self.report.stat().st_mode & 0o777, 0o600)

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
        self.files = {
            "README.md": b"readme fixture",
            "index.html": b"<!doctype html><html><head></head><body>index fixture</body></html>",
            "results.json": b"{}",
            **{name: f"asset fixture {name}".encode() for name in controller.SPACE_STATIC_ASSETS},
        }
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
            name = url.split("?", 1)[0].removeprefix(f"{controller.SPACE_URL}/")
            self.assertIn(name, self.files)
            self.assertEqual(url, f"{controller.SPACE_URL}/{name}?run={'a' * 40}")
            kinds = {".json": "application/json", ".html": "text/html", ".css": "text/css", ".svg": "image/svg+xml"}
            headers = {"Content-Type": kinds[Path(name).suffix]}
            if name != "index.html":
                headers["X-Repo-Commit"] = "a" * 40
            return public[name], headers

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
        self.assertEqual(outcome["space_url"], f"{controller.SPACE_URL}/index.html")
        self.assertEqual(outcome["public_index_url"], f"{controller.SPACE_URL}/index.html?run={'a' * 40}")
        self.assertEqual(self.api.space_info.call_count, 2)
        self.assertEqual(self.api.get_space_runtime.call_count, 2)

    def test_reviewed_static_provider_injection_is_noop_verified(self) -> None:
        injection = (
            controller.HF_STATIC_INDEX_INJECTION_PREFIX
            + controller.canonical_json_bytes({"SPACE_CREATOR_USER_ID": controller.HF_STATIC_CREATOR_USER_ID})
            + controller.HF_STATIC_INDEX_INJECTION_SUFFIX
        )
        public_index = self.files["index.html"].replace(b"<head>", b"<head>" + injection, 1)
        outcome = self.invoke(public={**self.files, "index.html": public_index})
        self.assertEqual(outcome["public_index_transform"], "HF_STATIC_CREATOR_METADATA_V1")
        self.assertEqual(outcome["public_index_sha256"], controller.sha256_bytes(public_index))
        self.assertEqual(outcome["public_index_normalized_sha256"], controller.sha256_bytes(self.files["index.html"]))
        self.assertEqual(outcome["public_index_injection_sha256"], controller.sha256_bytes(injection))

    def test_unreviewed_static_provider_injection_cannot_be_noop_success(self) -> None:
        injection = (
            controller.HF_STATIC_INDEX_INJECTION_PREFIX
            + b'{"SPACE_CREATOR_USER_ID":"000000000000000000000000"}'
            + controller.HF_STATIC_INDEX_INJECTION_SUFFIX
        )
        public_index = self.files["index.html"].replace(b"<head>", b"<head>" + injection, 1)
        with self.assertRaisesRegex(controller.BenchError, "unreviewed static-provider variables"):
            self.invoke(public={**self.files, "index.html": public_index})

    def test_changed_immutable_bundle_requires_authentication_without_writing(self) -> None:
        self.assertIsNone(self.invoke(remote={**self.files, "README.md": b"different"}))

    def test_nonrunning_runtime_requires_authentication_without_restarting(self) -> None:
        self.api.get_space_runtime.return_value = SimpleNamespace(stage="STOPPED")
        self.assertIsNone(self.invoke())

    def test_public_bytes_mismatch_cannot_be_noop_success(self) -> None:
        with self.assertRaisesRegex(controller.BenchError, "differs from the verified bundle"):
            self.invoke(public={**self.files, "results.json": b"different"})

    def test_noop_witnesses_every_public_szl_asset(self) -> None:
        outcome = self.invoke()
        expected = {name: controller.sha256_bytes(self.files[name]) for name in controller.SPACE_STATIC_ASSETS}
        self.assertEqual(outcome["public_static_asset_sha256"], expected)
        self.assertEqual(outcome["immutable_static_asset_sha256"], expected)

    def test_missing_remote_szl_asset_requires_authenticated_publication(self) -> None:
        self.api.list_repo_files.return_value = ["README.md", "index.html", "results.json"]
        self.assertIsNone(self.invoke())

    def test_retired_remote_file_requires_authenticated_cleanup(self) -> None:
        self.assertIn("style.css", controller.SPACE_RETIRED_FILES)
        self.api.list_repo_files.return_value = [*self.files, "style.css"]
        self.assertIsNone(self.invoke())

    def test_unrelated_extra_remote_file_still_allows_noop(self) -> None:
        self.api.list_repo_files.return_value = [*self.files, ".gitattributes"]
        self.assertFalse(self.invoke()["changed"])

    def test_public_szl_asset_mismatch_cannot_be_noop_success(self) -> None:
        with self.assertRaisesRegex(controller.BenchError, "differs from the reviewed vendored asset"):
            self.invoke(public={**self.files, "szl/szl-design-system.css": b"different"})

    def test_concurrent_head_change_cannot_be_noop_success(self) -> None:
        changed = SimpleNamespace(**{**vars(self.info), "sha": "b" * 40})
        self.api.space_info.side_effect = [self.info, changed]
        with self.assertRaisesRegex(controller.BenchError, "changed during the public witness"):
            self.invoke()

    def test_provider_host_allowlist_accepts_expected_hosts(self) -> None:
        for host in publisher.ALLOWED_LIVE_HOSTS:
            self.assertEqual(publisher._live_url(SimpleNamespace(host=f"https://{host}")), f"https://{host}/")

    def test_provider_host_rejects_userinfo_ports_and_other_components(self) -> None:
        for host in ("https://betterwithage-szl-bench-suite.static.hf.space",
                     "http://szlholdings-szl-bench-suite.static.hf.space", "https://example.com",
                     "https://szlholdings-szl-bench-suite.static.hf.space/other",
                     "https://szlholdings-szl-bench-suite.static.hf.space?redirect=1",
                     "https://user@szlholdings-szl-bench-suite.static.hf.space",
                     "https://szlholdings-szl-bench-suite.static.hf.space:443"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                publisher._live_url(SimpleNamespace(host=host))


FAKE_TOKEN = "hf_UNIT_TEST_NOT_A_REAL_CREDENTIAL"


def identity_fixture(*, name: str = "betterwithage", role: str = "write", org_role: str = "admin",
                     fine_grained: dict | None = None) -> dict:
    access_token = {"displayName": "ci", "role": role}
    if fine_grained is not None:
        access_token["fineGrained"] = fine_grained
    return {"type": "user", "name": name, "orgs": [{"name": "SZLHOLDINGS", "roleInOrg": org_role}],
            "auth": {"type": "access_token", "accessToken": access_token}}


class CredentialCanaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = Mock()
        self.api.whoami.return_value = identity_fixture()
        self.api.space_info.return_value = SimpleNamespace(id=publisher.TARGET, sha="a" * 40, resource_group=None)
        self.factory = Mock(return_value=self.api)

    def run_check(self, *, token: str | None = FAKE_TOKEN, extra: list[str] | None = None) -> tuple[int, dict, str]:
        output = io.StringIO()
        env = {key: value for key, value in os.environ.items() if key != "HF_TOKEN"}
        if token is not None:
            env["HF_TOKEN"] = token
        with patch.dict(os.environ, env, clear=True), \
                patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(HfApi=self.factory, get_token=Mock(return_value=None))}), \
                patch.object(publisher, "load_controller", return_value=controller), \
                contextlib.redirect_stdout(output):
            code = publisher.main(["--check-write-credential", *(extra or [])])
        text = output.getvalue()
        self.assertNotIn(FAKE_TOKEN, text)
        for mutation in ("create_commit", "upload_file", "upload_folder", "delete_file", "restart_space",
                         "pause_space", "add_space_secret", "update_repo_settings"):
            getattr(self.api, mutation).assert_not_called()
        return code, json.loads(text), text

    def test_live_write_credential_passes_with_identity_and_booleans_only(self) -> None:
        code, report, _ = self.run_check()
        self.assertEqual(code, 0)
        self.assertEqual(report["state"], "WRITE_CREDENTIAL_VERIFIED")
        self.assertEqual(report["remote_mutation"], "NOT_ATTEMPTED")
        self.assertEqual(report["credential"], {"identity": "betterwithage", "identity_matches": True,
                                                "token_write_scope": True, "org_write_role": True, "org_policy_clear": True,
                                                "space_visible": True, "resource_group_write_access": True})
        self.factory.assert_called_once_with(token=FAKE_TOKEN)
        self.api.space_info.assert_called_once_with(repo_id=publisher.TARGET, expand=["resourceGroup"])

    def test_fine_grained_repo_write_on_org_or_space_passes(self) -> None:
        for entity in ({"type": "org", "name": "SZLHOLDINGS"}, {"type": "space", "name": publisher.TARGET}):
            with self.subTest(entity=entity):
                self.api.whoami.return_value = identity_fixture(role="fineGrained", fine_grained={
                    "global": [], "scoped": [{"entity": entity, "permissions": ["repo.content.read", "repo.write"]}]})
                code, report, _ = self.run_check()
                self.assertEqual(code, 0, report)

    def test_revoked_token_fails_closed_with_redacted_output(self) -> None:
        class Unauthorized(Exception):
            response = SimpleNamespace(status_code=401)

        self.api.whoami.side_effect = Unauthorized(f"401 Client Error: Invalid credentials Authorization: Bearer {FAKE_TOKEN}")
        code, report, _ = self.run_check()
        self.assertEqual(code, controller.EXIT_HUB_AUTH)
        self.assertEqual(report["state"], "FAILED_CLOSED")
        self.assertEqual(report["failure"]["phase"], "hub_auth")
        self.assertIn("HTTP 401", report["failure"]["error"])
        self.assertFalse(report["credential"]["identity_matches"])
        self.api.space_info.assert_not_called()

    def test_wrong_user_read_only_or_unscoped_tokens_fail_closed(self) -> None:
        cases = {
            "wrong user": identity_fixture(name="someone-else"),
            "read-only token": identity_fixture(role="read"),
            "org read role": identity_fixture(org_role="read"),
            "org contributor role": identity_fixture(org_role="contributor"),
            "fine-grained read only": identity_fixture(role="fineGrained", fine_grained={
                "scoped": [{"entity": {"type": "org", "name": "SZLHOLDINGS"}, "permissions": ["repo.content.read"]}]}),
            "fine-grained other org": identity_fixture(role="fineGrained", fine_grained={
                "scoped": [{"entity": {"type": "org", "name": "other-org"}, "permissions": ["repo.write"]}]}),
            "oauth token without access-token role": {**identity_fixture(), "auth": {"type": "oauth"}},
        }
        for label, identity in cases.items():
            with self.subTest(label):
                self.api.reset_mock()
                self.api.whoami.return_value = identity
                code, report, _ = self.run_check()
                self.assertEqual(code, controller.EXIT_HUB_AUTH)
                self.assertEqual(report["state"], "FAILED_CLOSED")
                self.assertEqual(report["failure"]["phase"], "hub_auth")
                self.assertFalse(all(value for key, value in report["credential"].items() if key != "identity"))

    def test_space_unreadable_with_credential_fails_closed(self) -> None:
        class NotFound(Exception):
            response = SimpleNamespace(status_code=404)

        self.api.space_info.side_effect = NotFound(f"token={FAKE_TOKEN}")
        code, report, _ = self.run_check()
        self.assertEqual(code, controller.EXIT_HUB_AUTH)
        self.assertIn("HTTP 404", report["failure"]["error"])
        self.assertFalse(report["credential"]["space_visible"])

    def test_org_security_restrictions_block_even_admin_and_public_space(self) -> None:
        for restrictions in (["token-policy"], ["mfa"], ["sso"], ["ip"], ["token-revoked"], None, "", {}):
            with self.subTest(restrictions=restrictions):
                self.api.reset_mock()
                identity = identity_fixture(org_role="admin")
                identity["orgs"][0]["securityRestrictions"] = restrictions
                self.api.whoami.return_value = identity
                code, report, _ = self.run_check()
                self.assertEqual(code, controller.EXIT_HUB_AUTH)
                self.assertFalse(report["credential"]["org_policy_clear"])
                self.api.space_info.assert_not_called()

    def test_resource_group_requires_exact_membership_write_role(self) -> None:
        group_id = "a" * 24
        self.api.space_info.return_value.resource_group = {"id": group_id, "name": "bench"}
        cases = ((None, False), ([], False), ([{"id": "b" * 24, "role": "write"}], False),
                 ([{"id": group_id, "role": "read"}], False),
                 ([{"id": group_id, "role": "contributor"}], False),
                 ([{"id": group_id, "role": "write"}], True),
                 ([{"id": group_id, "role": "admin"}], True),
                 ([{"id": group_id, "role": "write"}, {"id": group_id, "role": "read"}], False))
        for groups, allowed in cases:
            with self.subTest(groups=groups):
                identity = identity_fixture(org_role="write")
                identity["orgs"][0]["resourceGroups"] = groups
                identity["orgs"][0]["securityRestrictions"] = []
                self.api.whoami.return_value = identity
                code, report, _ = self.run_check()
                self.assertEqual(code, 0 if allowed else controller.EXIT_HUB_AUTH)
                self.assertEqual(report["credential"]["resource_group_write_access"], allowed)

    def test_org_admin_has_resource_group_override_but_malformed_metadata_fails(self) -> None:
        self.api.whoami.return_value = identity_fixture(org_role="admin")
        self.api.space_info.return_value.resource_group = {"id": "a" * 24, "name": "bench"}
        self.assertEqual(self.run_check()[0], 0)
        for malformed in (False, {}, {"id": ""}, {"id": 42}, [], "group"):
            with self.subTest(group=malformed):
                self.api.space_info.return_value.resource_group = malformed
                self.assertEqual(self.run_check()[0], controller.EXIT_HUB_AUTH)
        del self.api.space_info.return_value.resource_group
        self.assertEqual(self.run_check()[0], controller.EXIT_HUB_AUTH)

    def test_sdk_none_from_omitted_or_null_group_does_not_admit_org_writer(self) -> None:
        self.api.whoami.return_value = identity_fixture(org_role="write")
        # Real SpaceInfo always creates this attribute, defaulting to None
        # even when resourceGroup was absent from the HTTP response.
        self.api.space_info.return_value.resource_group = None
        code, report, _ = self.run_check()
        self.assertEqual(code, controller.EXIT_HUB_AUTH)
        self.assertFalse(report["credential"]["resource_group_write_access"])

    def test_duplicate_target_org_is_not_authority(self) -> None:
        identity = identity_fixture()
        identity["orgs"] *= 2
        self.api.whoami.return_value = identity
        self.assertEqual(self.run_check()[0], controller.EXIT_HUB_AUTH)

    def test_interrupt_is_reported_without_provider_body(self) -> None:
        self.api.whoami.side_effect = KeyboardInterrupt(FAKE_TOKEN)
        code, report, _ = self.run_check()
        self.assertEqual(code, controller.EXIT_INTERRUPTED)
        self.assertEqual(report["state"], "FAILED_CLOSED")

    def test_missing_token_fails_before_contacting_provider(self) -> None:
        code, report, _ = self.run_check(token=None)
        self.assertEqual(code, controller.EXIT_HUB_AUTH)
        self.assertEqual(report["failure"]["phase"], "hub_auth")
        self.factory.assert_not_called()

    def test_canary_cannot_be_combined_with_publication_arguments(self) -> None:
        for extra in (["--verify-only"], ["--bundle-dir", "bundle"], ["--report", "report.json"]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                publisher.main(["--check-write-credential", *extra])
            self.assertEqual(raised.exception.code, 2)

    def test_publication_still_requires_a_bundle(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            publisher.main([])
        self.assertEqual(raised.exception.code, 2)


class CredentialSelectionTests(unittest.TestCase):
    def invoke(self, candidates, checker):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "output"
            text = io.StringIO()
            env = {"GITHUB_OUTPUT": str(out), **{f"CANDIDATE_{k}": v for k, v in candidates.items()}}
            with patch.dict(os.environ, env, clear=True), patch.object(publisher, "load_controller", return_value=controller), \
                 patch.object(publisher, "check_write_credential", side_effect=checker) as check, contextlib.redirect_stdout(text):
                code = publisher.main(["--select-write-credential"])
            for value in candidates.values():
                if value:
                    self.assertNotIn(value, text.getvalue())
            return code, json.loads(text.getvalue()), out.read_text() if out.exists() else "", check

    def test_org_credential_is_preferred_only_after_validation(self):
        code, report, output, check = self.invoke({"HF_ORG_TOKEN": "hf_ORG_FAKE", "HF_TOKEN": FAKE_TOKEN}, lambda *_: {"identity_matches": True})
        self.assertEqual(code, 0)
        self.assertEqual(output, "pick=HF_ORG_TOKEN\n")
        self.assertEqual(report["selected"], "HF_ORG_TOKEN")
        self.assertEqual(check.call_count, 1)

    def test_invalid_org_credential_falls_back_to_verified_repo_credential(self):
        def check(_controller, token):
            if token == "hf_ORG_FAKE":
                raise controller.BenchError("hub_auth", "not admitted", controller.EXIT_HUB_AUTH)
            return {"identity_matches": True}
        code, report, output, checked = self.invoke({"HF_ORG_TOKEN": "hf_ORG_FAKE", "HF_TOKEN": FAKE_TOKEN}, check)
        self.assertEqual(code, 0)
        self.assertEqual(output, "pick=HF_TOKEN\n")
        self.assertFalse(report["candidates"][0]["verified"])
        self.assertTrue(report["candidates"][1]["verified"])
        self.assertEqual(checked.call_count, 2)

    def test_no_admitted_candidate_emits_no_selection(self):
        def reject(*_):
            raise controller.BenchError("hub_auth", "not admitted", controller.EXIT_HUB_AUTH)
        for candidates in ({}, {"HF_TOKEN": FAKE_TOKEN}, {"HF_ORG_TOKEN": "hf_ORG_FAKE", "HF_TOKEN": FAKE_TOKEN}):
            code, report, output, _ = self.invoke(candidates, reject)
            self.assertEqual(code, controller.EXIT_HUB_AUTH)
            self.assertEqual(report["state"], "FAILED_CLOSED")
            self.assertEqual(output, "")

    def test_interrupt_does_not_try_next_candidate(self):
        def interrupt(*_):
            raise KeyboardInterrupt(FAKE_TOKEN)
        code, _, output, checked = self.invoke({"HF_ORG_TOKEN": "hf_ORG_FAKE", "HF_TOKEN": FAKE_TOKEN}, interrupt)
        self.assertEqual(code, controller.EXIT_INTERRUPTED)
        self.assertEqual(output, "")
        self.assertEqual(checked.call_count, 1)

    def test_selection_rejects_mixed_or_cached_auth_modes(self):
        for extra in (["--check-write-credential"], ["--use-cached-auth"], ["--bundle-dir", "x"], ["--verify-only"], ["--report", "x"]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                publisher.main(["--select-write-credential", *extra])



class WorkflowContractTests(unittest.TestCase):
    def test_main_and_weekly_publication_are_enabled_while_prs_stay_read_only(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "17 6 * * 1"', workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertNotIn("Scheduled publication is intentionally disabled", workflow)

    def test_publication_and_canary_use_only_the_validated_candidate(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text()
        publish = workflow.split("\n  publish:\n", 1)[1]
        self.assertIn("--select-write-credential", publish)
        self.assertIn("CANDIDATE_HF_ORG_TOKEN: ${{ secrets.HF_ORG_TOKEN }}", publish)
        self.assertIn("CANDIDATE_HF_TOKEN: ${{ secrets.HF_TOKEN }}", publish)
        selection = "HF_TOKEN: ${{ steps.publication_credential.outputs.pick == 'HF_ORG_TOKEN' && secrets.HF_ORG_TOKEN || secrets.HF_TOKEN }}"
        self.assertEqual(publish.count(selection), 2)
        self.assertLess(publish.index("--select-write-credential"), publish.index("--bundle-dir /tmp/szl-bench-publish/bundle"))

    def test_scheduled_and_manual_runs_exercise_the_write_credential(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(encoding="utf-8")
        publish_job = workflow.split("\n  publish:\n", 1)[1]
        step = publish_job.split("- name: Write-credential canary", 1)[1]
        self.assertIn("if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'", step)
        self.assertIn("HF_TOKEN: ${{ steps.publication_credential.outputs.pick", step)
        self.assertIn("tools/publish_space.py --check-write-credential", step)


if __name__ == "__main__":
    unittest.main()
