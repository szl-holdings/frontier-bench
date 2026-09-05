#!/usr/bin/env python3
"""Standard-library regression tests for finish_bench_plane.py."""

from __future__ import annotations

import ast
import contextlib
import datetime as dt
import importlib.util
import io
import json
import hmac
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HERE = pathlib.Path(__file__).resolve().parent
CONTROLLER = HERE / "finish_bench_plane.py"
MODULE_SPEC = importlib.util.spec_from_file_location("finish_bench_plane_under_test", CONTROLLER)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError("could not load controller")
bench = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = bench
MODULE_SPEC.loader.exec_module(bench)


def make_spec(genesis: str = "0" * 64) -> bench.RepoSpec:
    return bench.RepoSpec(
        plane="engine",
        repo="fixture",
        port=9999,
        revision="1" * 40,
        genesis=genesis,
        runtime_hashes={},
        test_hashes={},
    )


def genesis_body() -> dict:
    return {
        "machine": dict(bench.EXPECTED_MACHINE["receipt"]),
        "measured_at": "2026-09-04T02:15:00Z",
        "method": "fixture genesis",
        "metrics": {},
        "plane": "engine",
        "prev_hash": bench.ZERO_HASH,
        "status": "BLOCKED",
    }


def write_receipt(root: pathlib.Path, value: dict) -> pathlib.Path:
    target = root / "receipts" / "000-genesis.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return target


class ReceiptTests(unittest.TestCase):
    def test_valid_genesis_is_independently_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = genesis_body()
            receipt["hash"] = bench._receipt_digest(receipt)
            write_receipt(root, receipt)
            result = bench.verify_receipts(make_spec(receipt["hash"]), root)
            self.assertEqual(result["receipt_count"], 1)
            self.assertEqual(result["measured_count"], 0)

    def test_tamper_with_preserved_declared_hash_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = genesis_body()
            receipt["hash"] = bench._receipt_digest(receipt)
            trusted = receipt["hash"]
            receipt["method"] = "tampered"
            write_receipt(root, receipt)
            with self.assertRaises(bench.BenchError):
                bench.verify_receipts(make_spec(trusted), root)

    def test_wrong_plane_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = genesis_body()
            receipt["plane"] = "quant"
            receipt["hash"] = bench._receipt_digest(receipt)
            write_receipt(root, receipt)
            with self.assertRaises(bench.BenchError):
                bench.verify_receipts(make_spec(receipt["hash"]), root)

    def test_duplicate_json_key_fails(self) -> None:
        raw = b'{"plane":"engine","plane":"quant"}'
        with self.assertRaises(bench.BenchError):
            bench.strict_json_from_bytes(raw, source="duplicate-fixture")

    def test_non_finite_json_fails(self) -> None:
        with self.assertRaises(bench.BenchError):
            bench.strict_json_from_bytes(b'{"x":NaN}', source="nan-fixture")

    def test_timestamp_contract_requires_canonical_z_form(self) -> None:
        parsed = bench._parse_utc_timestamp("2026-09-04T12:00:00Z", field="fixture")
        self.assertEqual(parsed.utcoffset(), dt.timedelta(0))
        with self.assertRaises(bench.BenchError):
            bench._parse_utc_timestamp("2026-09-04T12:00:00+00:00", field="fixture")

    def test_non_measured_metrics_fail(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            receipt = genesis_body()
            receipt["metrics"] = {"fake": 1}
            receipt["hash"] = bench._receipt_digest(receipt)
            write_receipt(root, receipt)
            with self.assertRaises(bench.BenchError):
                bench.verify_receipts(make_spec(receipt["hash"]), root)

    def test_measured_receipt_requires_and_verifies_hmac_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            genesis = genesis_body()
            genesis["hash"] = bench._receipt_digest(genesis)
            write_receipt(root, genesis)
            spec = make_spec(genesis["hash"])
            key = bytearray.fromhex("11" * 32)
            measured = {
                "schema_version": "szl-bench-receipt/v3",
                "audience": bench.RECEIPT_AUDIENCE,
                "plane": "engine",
                "status": "MEASURED",
                "machine": dict(bench.EXPECTED_MACHINE["receipt"]),
                "measured_at": "2026-09-04T02:16:00Z",
                "method": "authenticated fixture",
                "metrics": {"model": "fixture", "precision": "fp16", "prompt_tps": 1.0, "decode_tps": 2.0, "peak_vram_gb": 3.0},
                "prev_hash": genesis["hash"],
                "source_revision": spec.revision,
                "workload": {"model_revision": "model@fixture", "data_revision": "prompts@fixture", "configuration_sha256": "2" * 64},
                "artifacts": {"raw.json": "3" * 64},
                "hardware_evidence_sha256": "4" * 64,
                "auth": {"alg": "hmac-sha256", "key_id": bench.RECEIPT_KEY_ID, "mac": ""},
            }
            signed = {field: value for field, value in measured.items() if field != "auth"}
            signed["auth"] = {"alg": measured["auth"]["alg"], "key_id": measured["auth"]["key_id"]}
            measured["auth"]["mac"] = hmac.new(key, bench.RECEIPT_DOMAIN + bench.canonical_json_bytes(signed), hashlib.sha256).hexdigest()
            measured["hash"] = bench._receipt_digest(measured)
            target = root / "receipts" / "001-measured.json"
            target.write_text(json.dumps(measured, sort_keys=True), encoding="utf-8")
            with self.assertRaises(bench.BenchError):
                bench.verify_receipts(spec, root)
            admitted = bench.verify_receipts(spec, root, key)
            self.assertEqual(admitted["measured_count"], 1)
            measured["metrics"]["decode_tps"] = 999
            target.write_text(json.dumps(measured, sort_keys=True), encoding="utf-8")
            with self.assertRaises(bench.BenchError):
                bench.verify_receipts(spec, root, key)

    def test_receipt_sequence_orders_999_before_1000_and_rejects_gaps(self) -> None:
        unordered = [
            ("000-genesis.json" if index == 0 else f"{index:03d}-fixture.json", f"fixture-{index}", b"{}")
            for index in range(1001)
        ]
        unordered.reverse()
        ordered = bench._order_receipt_inputs(unordered, repo="fixture")
        self.assertEqual(ordered[999][0], "999-fixture.json")
        self.assertEqual(ordered[1000][0], "1000-fixture.json")
        with self.assertRaises(bench.BenchError):
            bench._order_receipt_inputs([unordered[-1], unordered[-3]], repo="fixture")


class ControlTests(unittest.TestCase):
    def test_controller_has_no_runtime_assert_statements(self) -> None:
        tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)))
        ast.parse(bench.SERVER_SOURCE)

    def test_optimized_python_cannot_bypass_machine_gate(self) -> None:
        code = (
            "import importlib.util,sys;"
            f"p=r'{CONTROLLER}';"
            "s=importlib.util.spec_from_file_location('m',p);"
            "m=importlib.util.module_from_spec(s);sys.modules['m']=m;s.loader.exec_module(m);"
            "m.qualify_machine({'cpu':'wrong','ram_gib':1,'gpus':[]})"
        )
        completed = subprocess.run(
            [sys.executable, "-O", "-I", "-B", "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("BenchError", completed.stderr)

    def test_child_environment_drops_secrets_and_python_injection(self) -> None:
        saved = dict(os.environ)
        try:
            os.environ["HF_TOKEN"] = "hf_test_secret_1234567890"
            os.environ["GH_TOKEN"] = "ghp_test_secret_1234567890"
            os.environ["AWS_SECRET_ACCESS_KEY"] = "sentinel"
            os.environ["PYTHONPATH"] = "malicious"
            os.environ["PYTHONOPTIMIZE"] = "2"
            child = bench.sanitized_child_env()
        finally:
            os.environ.clear()
            os.environ.update(saved)
        for forbidden in ("HF_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "PYTHONPATH", "PYTHONOPTIMIZE"):
            self.assertNotIn(forbidden, child)
        self.assertEqual(child["PYTHONNOUSERSITE"], "1")

    def test_redaction_removes_common_token_shapes(self) -> None:
        value = bench.redact("token=hf_abcdefghijklmnopqrstuvwxyz012345")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", value)
        self.assertIn("REDACTED", value)
        bearer = bench.redact("Authorization: Bearer SECRET-SHOULD-NOT-APPEAR")
        self.assertNotIn("SECRET-SHOULD-NOT-APPEAR", bearer)

    def test_machine_policy_accepts_only_named_capacity(self) -> None:
        good = {
            "cpu": "14th Gen Intel(R) Core(TM) i9-14900HX",
            "ram_gib": 125.5,
            "gpus": [{"name": "NVIDIA RTX 4000 Ada Generation", "memory_mib": 20480}],
        }
        bench.qualify_machine(good)
        bad = {**good, "gpus": [{"name": "NVIDIA RTX 4000 Ada Generation", "memory_mib": 12288}]}
        with self.assertRaises(bench.BenchError):
            bench.qualify_machine(bad)

    def test_run_lease_rejects_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "active"
            with bench.RunLease(path, "first"):
                with self.assertRaises(bench.BenchError):
                    with bench.RunLease(path, "second"):
                        pass
            self.assertFalse(path.exists())

    def test_space_readme_requires_static_sdk(self) -> None:
        bench.validate_static_space_readme((HERE / "szl-bench-suite.README.md").read_bytes())
        with self.assertRaises(bench.BenchError):
            bench.validate_static_space_readme(b"# no metadata\n")

    def test_space_index_rejects_unsupported_claims(self) -> None:
        reviewed = (HERE / "szl-bench-suite.index.html").read_bytes()
        bench.validate_space_index_template(reviewed)
        with self.assertRaises(bench.BenchError):
            bench.validate_space_index_template(reviewed + b"\nPagedAttention\n")

    def test_space_index_binds_exact_payload_bytes(self) -> None:
        template = (HERE / "szl-bench-suite.index.html").read_bytes()
        payload = b'{"schema_version":"fixture"}\n'
        rendered = bench.finalize_space_index(template, payload)
        self.assertEqual(rendered.count(bench.sha256_bytes(payload).encode("ascii")), 1)
        self.assertNotIn(bench.RESULT_DIGEST_PLACEHOLDER.encode("ascii"), rendered)

    def test_hub_download_symlink_must_resolve_inside_private_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            local = root / "local"
            local.mkdir()
            internal = local / "blob"
            internal.write_bytes(b"trusted")
            safe_link = local / "safe-link"
            outside = root / "outside"
            outside.write_bytes(b"outside")
            escape_link = local / "escape-link"
            with self.assertRaises(bench.BenchError):
                bench._read_hub_download_path(outside, local)
            try:
                safe_link.symlink_to(internal)
                escape_link.symlink_to(outside)
            except OSError:
                return
            self.assertEqual(bench._read_hub_download_path(safe_link, local), b"trusted")
            with self.assertRaises(bench.BenchError):
                bench._read_hub_download_path(escape_link, local)

    def test_metric_contract_rejects_missing_extra_and_out_of_range_fields(self) -> None:
        valid = {"corpus": "fixture", "method": "bm25", "ndcg10": 1.0, "recall100": 0.5, "mrr": 0.75, "p50_ms": 2.0}
        bench._validate_plane_metrics("retrieval", valid, source="fixture")
        for invalid in ({**valid, "mrr": 1.1}, {key: value for key, value in valid.items() if key != "mrr"}, {**valid, "extra": 1}):
            with self.assertRaises(bench.BenchError):
                bench._validate_plane_metrics("retrieval", invalid, source="fixture")

    def test_command_runner_rejects_bare_executable_names(self) -> None:
        with self.assertRaises(bench.BenchError):
            bench.CommandRunner(quiet=True).run(["git", "--version"])

    def test_command_runner_enforces_binary_output_cap_while_streaming(self) -> None:
        with self.assertRaises(bench.BenchError):
            bench.CommandRunner(quiet=True).run_bytes(
                [sys.executable, "-I", "-B", "-c", "import sys;sys.stdout.buffer.write(b'x'*1000000)"],
                timeout=10,
                max_bytes=1024,
                phase="fixture",
                exit_code=bench.EXIT_INTERNAL,
            )

    def test_command_runner_retains_bounded_text_output_and_reports_failures(self) -> None:
        runner = bench.CommandRunner(quiet=True)
        result = runner.run([sys.executable, "-I", "-B", "-c", "print('x'*100000);print('last-line')"])
        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(len(result.output), 65_536)
        self.assertTrue(result.output.replace("\r\n", "\n").endswith("last-line\n"))
        with self.assertRaises(bench.BenchError) as captured:
            runner.run([sys.executable, "-I", "-B", "-c", "import sys;print('failure-detail');sys.exit(3)"])
        self.assertIn("failure-detail", captured.exception.detail["output_tail"])

    def test_command_runner_timeout_terminates_child(self) -> None:
        original_popen = subprocess.Popen
        launched = []

        def launch(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            launched.append(process)
            return process

        started = time.monotonic()
        with mock.patch.object(bench.subprocess, "Popen", side_effect=launch):
            with self.assertRaises(bench.BenchError) as captured:
                bench.CommandRunner(quiet=True).run(
                    [sys.executable, "-I", "-B", "-c", "import time;time.sleep(30)"], timeout=0.1
                )
        self.assertIn("timed out", str(captured.exception))
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNotNone(launched[0].poll())

    def test_command_runner_interrupt_terminates_child_before_returning(self) -> None:
        original_popen = subprocess.Popen
        launched = []

        def launch(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            launched.append(process)
            original_wait = process.wait
            calls = 0

            def interrupt_first_wait(*wait_args, **wait_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise KeyboardInterrupt()
                return original_wait(*wait_args, **wait_kwargs)

            process.wait = interrupt_first_wait
            return process

        with mock.patch.object(bench.subprocess, "Popen", side_effect=launch):
            with self.assertRaises(KeyboardInterrupt):
                bench.CommandRunner(quiet=True).run(
                    [sys.executable, "-I", "-B", "-c", "import time;time.sleep(30)"]
                )
        self.assertIsNotNone(launched[0].poll())

    def test_command_runner_reports_open_descendant_pipe_without_closing_reader(self) -> None:
        release = threading.Event()

        class HeldPipe:
            def __init__(self):
                self.closed_by_reader = False

            def read(self, _size):
                release.wait(timeout=10)
                return b""

            def close(self):
                self.closed_by_reader = threading.current_thread() is not threading.main_thread()

        held = HeldPipe()
        fake = types.SimpleNamespace(stdout=held, stderr=None, returncode=0, pid=-1, wait=lambda **_: 0, kill=lambda: None)
        started = time.monotonic()
        try:
            with mock.patch.object(bench.subprocess, "Popen", return_value=fake), mock.patch.object(bench.os, "killpg", create=True):
                with self.assertRaisesRegex(OSError, "pipes stayed open"):
                    bench.CommandRunner._capture(
                        [sys.executable], cwd=None, timeout=1, env_extra=None, merge_stderr=True, stdout_limit=None
                    )
            self.assertLess(time.monotonic() - started, 5)
            self.assertFalse(held.closed_by_reader)
        finally:
            release.set()

    def test_hub_download_uses_private_local_directory_and_disables_legacy_symlinks(self) -> None:
        observed = {}

        def download(*, local_dir, local_dir_use_symlinks=True, **kwargs):
            observed.update(kwargs, local_dir=local_dir, local_dir_use_symlinks=local_dir_use_symlinks)
            target = pathlib.Path(local_dir) / "results.json"
            target.write_bytes(b"fixture")
            return str(target)

        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.dict(sys.modules, {"huggingface_hub": types.SimpleNamespace(hf_hub_download=download)}):
                content = bench._download_hub_file_strict("owner/repo", "results.json", "a" * 40, "fixture", pathlib.Path(raw))
            self.assertEqual(content, b"fixture")
            self.assertTrue(pathlib.Path(observed["local_dir"]).is_relative_to(pathlib.Path(raw)))
        self.assertIs(observed["local_dir_use_symlinks"], False)
        self.assertIs(observed["force_download"], True)

    def test_container_policy_accepts_only_the_declared_tmpfs_mount(self) -> None:
        spec = make_spec()
        deployer = bench.DockerDeployer(None, "fixture-run", 1.0)
        deployer.images[spec.plane] = {"image_id": "sha256:" + "a" * 64, "results_sha256": "b" * 64}
        inspected = {
            "Image": "sha256:" + "a" * 64,
            "Config": {
                "User": "65532:65532",
                "Labels": {
                    "io.szl.managed-by": bench.MANAGED_BY,
                    "io.szl.run-id": "fixture-run",
                    "io.szl.plane": spec.plane,
                    "io.szl.source-revision": spec.revision,
                    "io.szl.results-sha256": "b" * 64,
                },
            },
            "HostConfig": {
                "Privileged": False,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges=true"],
                "RestartPolicy": {"Name": "no"},
                "Memory": 512 * 1024 * 1024,
                "NanoCpus": 2_000_000_000,
                "PidsLimit": 256,
                "LogConfig": {"Type": "local", "Config": {"max-size": "1m", "max-file": "2"}},
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
                "PortBindings": {"7860/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]},
            },
            "Mounts": [{"Type": "tmpfs", "Source": "", "Destination": "/tmp", "RW": True}],
        }
        deployer._verify_container(spec, inspected, restart="no")
        inspected["Mounts"] = []
        deployer._verify_container(spec, inspected, restart="no")
        for host_key, unsafe_value in (
            ("Tmpfs", {"/tmp": "rw,noexec,nosuid,size=64m", "/extra": "rw"}),
            ("LogConfig", {"Type": "json-file", "Config": {}}),
            ("Binds", ["/host:/data:rw"]),
            ("Mounts", [{"Type": "bind", "Source": "/host", "Target": "/data"}]),
            ("VolumesFrom", ["unrelated-container"]),
        ):
            with self.subTest(host_key=host_key):
                saved = inspected["HostConfig"].get(host_key)
                inspected["HostConfig"][host_key] = unsafe_value
                with self.assertRaises(bench.BenchError):
                    deployer._verify_container(spec, inspected, restart="no")
                if saved is None:
                    inspected["HostConfig"].pop(host_key)
                else:
                    inspected["HostConfig"][host_key] = saved
        inspected["Mounts"].append({"Type": "bind", "Source": "/host", "Destination": "/data", "RW": True})
        with self.assertRaises(bench.BenchError):
            deployer._verify_container(spec, inspected, restart="no")

    def test_assembled_empty_payload_is_stable_and_explicit(self) -> None:
        fixtures = []
        for spec in bench.REPOS:
            fixtures.append(
                {
                    "repo": spec.repo,
                    "plane": spec.plane,
                    "receipt_count": 1,
                    "genesis": spec.genesis,
                    "chain_head": spec.genesis,
                    "latest_receipt_at": "2026-09-04T02:15:00Z",
                    "integrity": "VERIFIED_UNSIGNED_HASH_CHAIN",
                    "results": [],
                }
            )
        first, _ = bench.assemble_payload(fixtures)
        second, _ = bench.assemble_payload(fixtures)
        self.assertEqual(bench.canonical_json_bytes(first), bench.canonical_json_bytes(second))
        self.assertEqual(first["data_state"], "EMPTY_HONEST")
        self.assertEqual(first["count"], 0)

    def test_audit_exports_bound_bundle_without_docker_or_hub(self) -> None:
        def snapshot(spec, *_args):
            return types.SimpleNamespace(spec=spec, record={"commit_authenticity": "SIGNATURE_PRESENT_NOT_VERIFIED"})

        def receipts(spec, *_args):
            return {
                "repo": spec.repo,
                "plane": spec.plane,
                "receipt_count": 1,
                "genesis": spec.genesis,
                "chain_head": spec.genesis,
                "latest_receipt_at": "2026-09-04T02:15:00Z",
                "integrity": "VERIFIED_UNSIGNED_HASH_CHAIN",
                "results": [],
            }

        with tempfile.TemporaryDirectory() as raw:
            workdir = pathlib.Path(raw) / "audit-state"
            args = bench.parse_args([
                "--audit-only", "--workdir", str(workdir), "--export-space-bundle", "bundle",
                "--space-readme", str(HERE / "szl-bench-suite.README.md"),
                "--space-index", str(HERE / "szl-bench-suite.index.html"), "--quiet",
            ])
            self.assertEqual(args.target, "local")
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(bench, "resolve_trusted_executable", side_effect=lambda name, *_a, **_k: sys.executable if name == "git" else None))
                stack.enter_context(mock.patch.object(bench, "validate_git_helper_root", return_value={}))
                stack.enter_context(mock.patch.object(bench, "probe_machine", return_value={"fixture": True}))
                stack.enter_context(mock.patch.object(bench, "materialize_source", side_effect=snapshot))
                stack.enter_context(mock.patch.object(bench, "verify_receipts", side_effect=receipts))
                stack.enter_context(mock.patch.object(bench, "load_receipt_auth_key", return_value=None))
                docker = stack.enter_context(mock.patch.object(bench, "DockerClient", side_effect=AssertionError("Docker must not run in audit")))
                hub = stack.enter_context(mock.patch.object(bench, "hub_preflight", side_effect=AssertionError("Hub must not run in audit")))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(bench.execute(args), 0)
            docker.assert_not_called()
            hub.assert_not_called()
            bundle = workdir / "bundle"
            self.assertEqual({entry.name for entry in bundle.iterdir()}, {"README.md", "index.html", "results.json"})
            payload = (bundle / "results.json").read_bytes()
            self.assertIn(bench.sha256_bytes(payload).encode(), (bundle / "index.html").read_bytes())
            report = json.loads(next((workdir / "evidence").glob("*.json")).read_text())
            self.assertEqual(report["overall"], "AUDIT_VERIFIED")
            self.assertEqual(report["layers"]["source_authenticity"]["state"], "SIGNATURE_PRESENT_NOT_VERIFIED")
            self.assertEqual(report["layers"]["bundle_export"]["sha256"]["results.json"], bench.sha256_bytes(payload))
            self.assertEqual(report["layers"]["publication"]["state"], "NOT_RUN_AUDIT_ONLY")
            with self.assertRaises(bench.BenchError):
                bench.export_space_bundle(args, workdir, payload)

    def test_bundle_export_rejects_outside_workdir_and_non_audit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workdir = pathlib.Path(raw) / "workdir"
            workdir.mkdir()
            args = bench.parse_args([
                "--audit-only", "--workdir", str(workdir), "--export-space-bundle", "../outside",
                "--space-readme", str(HERE / "szl-bench-suite.README.md"),
                "--space-index", str(HERE / "szl-bench-suite.index.html"),
            ])
            with self.assertRaises(bench.BenchError):
                bench.export_space_bundle(args, workdir, b"{}\n")
            self.assertFalse((pathlib.Path(raw) / "outside").exists())
            args.audit_only = False
            with self.assertRaises(bench.BenchError):
                bench.export_space_bundle(args, workdir, b"{}\n")


class _ApiHandler(BaseHTTPRequestHandler):
    payload: dict = {}
    run_id = "fixture-run"

    def log_message(self, *_: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/healthz":
            body = json.dumps({"status": "ok", "plane": "engine", "run_id": self.run_id, "ts": "2026-09-04T00:00:00Z"}).encode()
        elif self.path == "/readyz":
            body = json.dumps({
                "status": "ready",
                "schema_version": "szl-bench-service/v2",
                "run_id": self.run_id,
                "plane": "engine",
                "source_revision": "1" * 40,
                "results_sha256": self.payload["results_sha256"],
            }).encode()
        elif self.path == "/api/results":
            body = json.dumps(self.payload).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HttpWitnessTests(unittest.TestCase):
    def test_service_witness_binds_exact_payload(self) -> None:
        expected = {
            "generated_at": "2026-09-04T02:15:00Z",
            "results": [],
            "results_sha256": bench.sha256_bytes(bench.canonical_json_bytes([])),
        }
        _ApiHandler.payload = {
            "plane": "engine",
            "state": "EMPTY_HONEST",
            "generated_at": expected["generated_at"],
            "count": 0,
            "results": [],
            "results_sha256": expected["results_sha256"],
            "run_id": _ApiHandler.run_id,
            "source_revision": "1" * 40,
            "served_at": "2026-09-04T03:00:00Z",
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            record = bench.validate_service(
                f"http://127.0.0.1:{port}", make_spec(), expected, run_id=_ApiHandler.run_id, timeout=2
            )
            self.assertEqual(record["data_state"], "EMPTY_HONEST")
            _ApiHandler.payload["generated_at"] = "stale"
            with self.assertRaises(bench.BenchError):
                bench.validate_service(
                    f"http://127.0.0.1:{port}", make_spec(), expected, run_id=_ApiHandler.run_id, timeout=2
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class _FakeAdd:
    def __init__(self, *, path_in_repo: str, path_or_fileobj: object):
        self.path_in_repo = path_in_repo
        self.path_or_fileobj = path_or_fileobj


class _FakeDelete:
    def __init__(self, *, path_in_repo: str):
        self.path_in_repo = path_in_repo


class _FakeHubApi:
    def __init__(self, parent: str, files: dict[str, bytes]):
        self.head = parent
        self.stage = "RUNNING"
        self.revisions = {parent: dict(files)}
        self.commits = 0
        self.omit_commit_id = False
        self.interrupt_commit = False

    def repo_info(self, **_: object) -> object:
        return types.SimpleNamespace(sha=self.head)

    def get_space_runtime(self, **_: object) -> object:
        return types.SimpleNamespace(stage=self.stage)

    def list_repo_files(self, *, revision: str, **_: object) -> list[str]:
        return sorted(self.revisions[revision])

    def create_commit(self, *, operations: list[object], parent_commit: str, **_: object) -> object:
        if parent_commit != self.head:
            raise RuntimeError("stale parent")
        self.commits += 1
        revision = ("b" if self.commits == 1 else "c") * 40
        files = dict(self.revisions[parent_commit])
        for operation in operations:
            if isinstance(operation, _FakeDelete):
                files.pop(operation.path_in_repo, None)
            else:
                operation.path_or_fileobj.seek(0)
                files[operation.path_in_repo] = operation.path_or_fileobj.read()
        self.revisions[revision] = files
        self.head = revision
        if self.interrupt_commit:
            raise KeyboardInterrupt()
        if self.omit_commit_id:
            return types.SimpleNamespace()
        return types.SimpleNamespace(oid=revision)


@contextlib.contextmanager
def fake_hub(api: _FakeHubApi, http_get: object):
    original_module = sys.modules.get("huggingface_hub")
    original_download = bench._download_hub_file_strict
    original_http = bench.http_get_bytes
    sys.modules["huggingface_hub"] = types.SimpleNamespace(
        CommitOperationAdd=_FakeAdd,
        CommitOperationDelete=_FakeDelete,
    )
    bench._download_hub_file_strict = lambda _repo, filename, revision, _token, _download_root: api.revisions[revision][filename]
    bench.http_get_bytes = http_get
    try:
        yield
    finally:
        bench._download_hub_file_strict = original_download
        bench.http_get_bytes = original_http
        if original_module is None:
            sys.modules.pop("huggingface_hub", None)
        else:
            sys.modules["huggingface_hub"] = original_module


class HubPublicationTests(unittest.TestCase):
    def context(self, api: _FakeHubApi, parent: str, files: dict[str, bytes]) -> bench.HubContext:
        return bench.HubContext(
            api=api,
            token="fixture-token",
            parent_sha=parent,
            current_sdk="static",
            parent_stage="RUNNING",
            username="betterwithage",
            readme_bytes=None,
            index_template_bytes=(HERE / "szl-bench-suite.index.html").read_bytes(),
            managed_parent_files=files,
            download_root=HERE,
        )

    def test_noop_publication_still_verifies_stable_head_and_public_bytes(self) -> None:
        parent = "a" * 40
        payload = b'{"fixture":"stable"}\n'
        template = (HERE / "szl-bench-suite.index.html").read_bytes()
        index = bench.finalize_space_index(template, payload)
        files = {"README.md": (HERE / "szl-bench-suite.README.md").read_bytes(), "index.html": index, "results.json": payload}
        api = _FakeHubApi(parent, files)

        def http_get(url: str, **_: object) -> tuple[bytes, dict[str, str]]:
            return (payload, {"Content-Type": "application/json"}) if "results.json" in url else (index, {"Content-Type": "text/html"})

        with fake_hub(api, http_get):
            result = bench.publish_and_witness(self.context(api, parent, files), payload, provider_timeout=0.1, public_http_deadline=0.1)
        self.assertFalse(result["changed"])
        self.assertEqual(result["final_head_observation"], parent)
        self.assertEqual(api.commits, 0)

    def test_stale_parent_aborts_before_remote_mutation(self) -> None:
        parent = "a" * 40
        payload = b'{}\n'
        files = {"README.md": b"old", "index.html": b"old", "results.json": b"old"}
        api = _FakeHubApi(parent, files)
        api.head = "d" * 40
        api.revisions[api.head] = dict(files)
        with fake_hub(api, lambda *_args, **_kwargs: (b"", {})):
            with self.assertRaises(bench.BenchError):
                bench.publish_and_witness(self.context(api, parent, files), payload, provider_timeout=0.01, public_http_deadline=0.01)
        self.assertEqual(api.commits, 0)

    def test_missing_commit_id_is_reported_as_unknown_after_attempt(self) -> None:
        parent = "a" * 40
        payload = b'{"fixture":"new"}\n'
        files = {"README.md": (HERE / "szl-bench-suite.README.md").read_bytes(), "index.html": b"old", "results.json": b"old"}
        api = _FakeHubApi(parent, files)
        api.omit_commit_id = True
        with fake_hub(api, lambda *_args, **_kwargs: (b"", {})):
            with self.assertRaises(bench.BenchError) as captured:
                bench.publish_and_witness(self.context(api, parent, files), payload, provider_timeout=0.01, public_http_deadline=0.01)
        self.assertEqual(captured.exception.detail["state"], "UNKNOWN_AFTER_ATTEMPT_REMOTE_MAY_BE_MUTATED")
        self.assertEqual(captured.exception.detail["observed_head"], "b" * 40)
        self.assertEqual(api.commits, 1)

    def test_failed_public_witness_restores_parent_with_conditional_commit(self) -> None:
        parent = "a" * 40
        payload = b'{"fixture":"new"}\n'
        files = {"README.md": (HERE / "szl-bench-suite.README.md").read_bytes(), "index.html": b"old-index", "results.json": b"old-results"}
        api = _FakeHubApi(parent, files)

        def wrong_http(url: str, **_: object) -> tuple[bytes, dict[str, str]]:
            return (b"wrong", {"Content-Type": "application/json"}) if "results.json" in url else (b"wrong", {"Content-Type": "text/html"})

        with fake_hub(api, wrong_http):
            with self.assertRaises(bench.BenchError) as captured:
                bench.publish_and_witness(self.context(api, parent, files), payload, provider_timeout=0.1, public_http_deadline=0.01)
        self.assertEqual(captured.exception.detail["compensation"]["state"], "PUBLISH_FAILED_ROLLBACK_VERIFIED")
        self.assertEqual(api.commits, 2)
        self.assertEqual(api.revisions[api.head], files)

    def test_interrupted_commit_reports_unknown_remote_state(self) -> None:
        parent = "a" * 40
        files = {"README.md": b"old", "index.html": b"old", "results.json": b"old"}
        api = _FakeHubApi(parent, files)
        api.interrupt_commit = True
        with fake_hub(api, lambda *_args, **_kwargs: (b"", {})):
            with self.assertRaises(bench.BenchError) as captured:
                bench.publish_and_witness(self.context(api, parent, files), b"{}\n", provider_timeout=0.01, public_http_deadline=0.01)
        self.assertEqual(captured.exception.detail["state"], "UNKNOWN_AFTER_ATTEMPT_REMOTE_MAY_BE_MUTATED")
        self.assertEqual(captured.exception.detail["observed_head"], "b" * 40)
        self.assertEqual(api.commits, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
