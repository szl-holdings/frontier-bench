"""Re-admit an audit-exported bundle and publish through the reviewed controller.

Static publication does not qualify this host, start Docker, run workloads, or
establish that benchmark measurements were independently witnessed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import secrets
import sys
import tempfile
import urllib.parse
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_DIR = ROOT / "deploy" / "bench-plane"
CONTROLLER_PATH = CONTROLLER_DIR / "finish_bench_plane.py"
TARGET = "betterwithage/szl-bench-suite"
EXPECTED_USER = "betterwithage"
BUNDLE_FILES = {"README.md", "index.html", "results.json"}
ALLOWED_LIVE_HOSTS = {"betterwithage-szl-bench-suite.hf.space", "betterwithage-szl-bench-suite.static.hf.space"}


def _live_url(info: Any) -> str:
    value = str(getattr(info, "host", "")).rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_LIVE_HOSTS
            or parsed.username is not None or parsed.password is not None or parsed.port is not None
            or parsed.path or parsed.params or parsed.query or parsed.fragment):
        raise ValueError("provider returned an unexpected live host")
    return value + "/"


def load_controller() -> ModuleType:
    spec = importlib.util.spec_from_file_location("szl_reviewed_bench_controller", CONTROLLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("reviewed bench-plane controller is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.SPACE_ID != TARGET:
        raise RuntimeError("reviewed controller targets an unexpected Space")
    return module


def read_bundle(controller: ModuleType, directory: Path) -> tuple[dict[str, bytes], dict[str, Any]]:
    directory = Path(os.path.abspath(directory.expanduser()))
    controller._reject_link_components(directory, phase="bundle_admission", exit_code=controller.EXIT_RESULT)
    if not directory.is_dir() or {path.name for path in directory.iterdir()} != BUNDLE_FILES:
        raise controller.BenchError("bundle_admission", "bundle must contain exactly README.md, index.html, and results.json", controller.EXIT_RESULT)
    files = {
        name: controller.read_bounded_regular_file(directory / name, limit=controller.MAX_HTTP_BYTES,
                                                  phase="bundle_admission", exit_code=controller.EXIT_RESULT)
        for name in sorted(BUNDLE_FILES)
    }
    controller.validate_static_space_readme(files["README.md"])
    template = controller.read_bounded_regular_file(CONTROLLER_DIR / "szl-bench-suite.index.html",
                                                   limit=controller.MAX_HTTP_BYTES, phase="bundle_admission",
                                                   exit_code=controller.EXIT_RESULT)
    if controller.finalize_space_index(template, files["results.json"]) != files["index.html"]:
        raise controller.BenchError("bundle_admission", "bundle index differs from the reviewed template finalized for these results bytes", controller.EXIT_RESULT)
    payload = controller.strict_json_from_bytes(files["results.json"], source="bundle/results.json")
    required = {"schema_version", "generated_at", "data_state", "count", "results_sha256", "sources", "results"}
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema_version") != "szl-bench-results/v2":
        raise controller.BenchError("bundle_admission", "results payload has an unsupported schema", controller.EXIT_RESULT)
    controller._parse_utc_timestamp(payload["generated_at"], field="bundle.generated_at")
    rows, count = payload["results"], payload["count"]
    if not isinstance(rows, list) or type(count) is not int or count != len(rows) or count > len(controller.REPOS) * controller.MAX_RECEIPTS:
        raise controller.BenchError("bundle_admission", "results count does not match the bounded row list", controller.EXIT_RESULT)
    if payload["data_state"] != ("MEASURED" if rows else "EMPTY_HONEST"):
        raise controller.BenchError("bundle_admission", "data state contradicts the result count", controller.EXIT_RESULT)
    if payload["results_sha256"] != controller.sha256_bytes(controller.canonical_json_bytes(rows)):
        raise controller.BenchError("bundle_admission", "results list digest is invalid", controller.EXIT_RESULT)
    sources = payload["sources"]
    if not isinstance(sources, dict) or set(sources) != {spec.plane for spec in controller.REPOS}:
        raise controller.BenchError("bundle_admission", "results sources do not match the three reviewed planes", controller.EXIT_RESULT)
    for spec in controller.REPOS:
        source = sources[spec.plane]
        if (not isinstance(source, dict)
                or set(source) != {"repo", "revision", "genesis", "receipt_count", "receipt_head", "integrity"}
                or source["repo"] != spec.repo or source["revision"] != spec.revision or source["genesis"] != spec.genesis):
            raise controller.BenchError("bundle_admission", f"{spec.plane} source does not match the reviewed pins", controller.EXIT_RESULT)
    # Metadata alone is insufficient: original receipts and HMACs are checked by
    # readmit_payload, and its exact output must match the supplied bytes.
    return files, payload


def readmit_payload(controller: ModuleType, run_root: Path, git_bin: str | None = None) -> tuple[bytes, dict[str, Any]]:
    runner = controller.CommandRunner(quiet=True)
    trusted_git = controller.resolve_trusted_executable("git", git_bin)
    if trusted_git is None:
        raise controller.BenchError("preflight", "trusted Git executable is unavailable", controller.EXIT_PREFLIGHT)
    helpers = controller.validate_git_helper_root(runner, trusted_git)
    snapshots = [controller.materialize_source(spec, run_root, runner, trusted_git) for spec in controller.REPOS]
    receipt_key = controller.load_receipt_auth_key()
    try:
        receipts = [controller.verify_receipts(snapshot.spec, snapshot, receipt_key) for snapshot in snapshots]
    finally:
        if receipt_key is not None:
            for index in range(len(receipt_key)):
                receipt_key[index] = 0
    merged, _ = controller.assemble_payload(receipts)
    return controller.pretty_json_bytes(merged), {
        "state": "READMITTED_FROM_REVIEWED_SOURCE_RECEIPTS",
        "toolchain": {"git": trusted_git, **helpers},
        "sources": {snapshot.spec.plane: dict(snapshot.record) for snapshot in snapshots},
        "data_state": merged["data_state"], "count": merged["count"],
        "measurements": "NOT_PERFORMED_BY_PUBLISHER",
    }


def positive_timeout(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 1 <= number <= 1800:
        raise argparse.ArgumentTypeError("timeout must be finite and between 1 and 1800 seconds")
    return number


def verify_anonymous_noop(controller: ModuleType, files: dict[str, bytes], run_root: Path) -> dict[str, Any] | None:
    """Witness an already-identical public deployment using explicit anonymous reads.

    None means authentication is needed for a changed bundle or runtime. This
    function has no commit, upload, restart, or other mutation path.
    """
    from huggingface_hub import HfApi

    api = HfApi(token=False)
    before = api.space_info(repo_id=TARGET)
    before_sha = str(getattr(before, "sha", ""))
    if getattr(before, "id", TARGET) != TARGET or not controller.COMMIT_RE.fullmatch(before_sha):
        raise controller.BenchError("anonymous_witness", "provider returned an unexpected Space or revision", controller.EXIT_PROVIDER)
    live_url = _live_url(before)
    runtime = api.get_space_runtime(repo_id=TARGET)
    if bool(getattr(before, "private", True)) or getattr(before, "sdk", "") != "static" or str(getattr(runtime, "stage", "UNKNOWN")) != "RUNNING":
        return None
    remote_files = set(api.list_repo_files(repo_id=TARGET, repo_type="space", revision=before_sha))
    if not BUNDLE_FILES.issubset(remote_files):
        return None
    download_root = controller.ensure_private_directory(run_root / "anonymous-downloads", phase="anonymous_witness", exit_code=controller.EXIT_PROVIDER)
    hashes: dict[str, str] = {}
    for name, expected in files.items():
        observed = controller._download_hub_file_strict(TARGET, name, before_sha, False, download_root)
        if observed != expected:
            return None
        hashes[name] = controller.sha256_bytes(observed)
    public_hashes: dict[str, str] = {}
    for name, route in (("index.html", ""), ("results.json", "results.json")):
        observed, headers = controller.http_get_bytes(f"{live_url}{route}?run={before_sha}", timeout=15,
                                                       max_bytes=controller.MAX_HTTP_BYTES, expect_json=name.endswith(".json"))
        if name == "index.html" and "text/html" not in str(headers.get("Content-Type", "")).lower():
            raise controller.BenchError("anonymous_witness", "public index has the wrong content type", controller.EXIT_PROVIDER)
        if observed != files[name]:
            raise controller.BenchError("anonymous_witness", f"public {name} differs from the verified bundle", controller.EXIT_PROVIDER)
        public_hashes[name] = controller.sha256_bytes(observed)
    after = api.space_info(repo_id=TARGET)
    after_runtime = api.get_space_runtime(repo_id=TARGET)
    if (str(getattr(after, "sha", "")) != before_sha or str(getattr(after_runtime, "stage", "UNKNOWN")) != "RUNNING"
            or bool(getattr(after, "private", True)) or getattr(after, "sdk", "") != "static" or _live_url(after) != live_url):
        raise controller.BenchError("anonymous_witness", "Space head, host, or runtime changed during the public witness", controller.EXIT_PROVIDER)
    return {
        "space": TARGET, "space_url": live_url, "publisher": "ANONYMOUS_READ_ONLY",
        "parent_commit": before_sha, "commit": before_sha, "changed": False,
        "authenticated_write": False, "provider_stage": "RUNNING",
        "immutable_readback_sha256": hashes["results.json"], "immutable_index_sha256": hashes["index.html"],
        "immutable_readme_sha256": hashes["README.md"], "public_results_sha256": public_hashes["results.json"],
        "public_index_sha256": public_hashes["index.html"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True, help="directory exported by the reviewed controller audit")
    parser.add_argument("--git-bin", help="optional absolute trusted Git executable path")
    parser.add_argument("--report", type=Path, help="new local JSON report path; defaults to .bench-plane-publish/<run-id>.json")
    parser.add_argument("--use-cached-auth", "--cached-auth", dest="use_cached_auth", action="store_true", help="explicitly permit reading the local Hugging Face login token in this process")
    parser.add_argument("--provider-timeout", type=positive_timeout, default=600.0)
    parser.add_argument("--public-http-deadline", type=positive_timeout, default=180.0)
    args = parser.parse_args(argv)
    controller = load_controller()
    run_id = controller.utc_now().replace(":", "").replace("-", "") + "-" + secrets.token_hex(4)
    report_path = Path(os.path.abspath((args.report or Path.cwd() / ".bench-plane-publish" / f"{run_id}.json").expanduser()))
    report: dict[str, Any] = {
        "schema_version": "szl-static-bench-publication/v1", "run_id": run_id,
        "started_at": controller.utc_now(), "target": TARGET, "space_url": controller.SPACE_URL,
        "controller_sha256": controller.sha256_file(CONTROLLER_PATH),
        "state": "IN_PROGRESS", "remote_mutation": "NOT_ATTEMPTED",
        "measurements": "NOT_PERFORMED_BY_PUBLISHER", "local_runtime": "NOT_REQUESTED",
    }
    report_ready = False
    try:
        controller._reject_link_components(report_path, phase="report", exit_code=controller.EXIT_CLI)
        if report_path.exists():
            raise controller.BenchError("report", "refusing to overwrite an existing publication report", controller.EXIT_CLI)
        controller.ensure_private_directory(report_path.parent, phase="report", exit_code=controller.EXIT_CLI)
        controller.atomic_write(report_path, controller.pretty_json_bytes(report))
        report_ready = True
        files, payload = read_bundle(controller, args.bundle_dir)
        report["bundle_sha256"] = {name: controller.sha256_bytes(data) for name, data in files.items()}
        with tempfile.TemporaryDirectory(prefix="szl-static-publish-") as temporary:
            run_root = controller.ensure_private_directory(Path(temporary), phase="preflight", exit_code=controller.EXIT_PREFLIGHT)
            expected_bytes, admission = readmit_payload(controller, run_root, args.git_bin)
            if expected_bytes != files["results.json"]:
                raise controller.BenchError("bundle_admission", "exported results do not equal freshly verified source receipts", controller.EXIT_RESULT)
            report["admission"] = admission
            report["data_state"], report["count"] = payload["data_state"], payload["count"]
            controller.atomic_write(report_path, controller.pretty_json_bytes(report))
            previous_token = os.environ.get("HF_TOKEN")
            added_cached_token = False
            try:
                if not previous_token and args.use_cached_auth:
                    from huggingface_hub import get_token
                    cached_token = get_token()
                    if cached_token:
                        os.environ["HF_TOKEN"] = cached_token
                        added_cached_token = True
                    cached_token = None
                if not os.environ.get("HF_TOKEN"):
                    outcome = verify_anonymous_noop(controller, files, run_root)
                    if outcome is None:
                        raise controller.BenchError("hub_auth", "HF_TOKEN is required because the bundle or runtime needs a write; cached login is read only with --use-cached-auth", controller.EXIT_HUB_AUTH)
                else:
                    hub_args = argparse.Namespace(hf_token_env="HF_TOKEN", expected_hf_user=EXPECTED_USER,
                                                  space_readme=str(args.bundle_dir / "README.md"),
                                                  space_index=str(CONTROLLER_DIR / "szl-bench-suite.index.html"))
                    context = controller.hub_preflight(hub_args, run_root / "hub-downloads")
                    advertised_url = _live_url(context.api.space_info(repo_id=TARGET))
                    if advertised_url.rstrip("/") != controller.SPACE_URL.rstrip("/"):
                        raise controller.BenchError("hub_auth", "provider host differs from the reviewed controller's static host", controller.EXIT_HUB_AUTH)
                    if context.readme_bytes != files["README.md"] or controller.finalize_space_index(context.index_template_bytes, files["results.json"]) != files["index.html"]:
                        raise controller.BenchError("bundle_admission", "publication assets changed after initial admission", controller.EXIT_RESULT)
                    report["remote_mutation"] = "ATTEMPT_IN_PROGRESS"
                    controller.atomic_write(report_path, controller.pretty_json_bytes(report))
                    outcome = controller.publish_and_witness(context, files["results.json"],
                                                             provider_timeout=args.provider_timeout,
                                                             public_http_deadline=args.public_http_deadline)
                report["publication"] = outcome
                report["remote_mutation"] = "COMMIT_WITNESSED" if outcome["changed"] else "NO_CHANGE_WITNESSED"
            finally:
                if added_cached_token:
                    os.environ.pop("HF_TOKEN", None)
        report["state"], report["exit_code"] = "PUBLISHED_EVIDENCE_SURFACE_OPERATIONAL", 0
    except BaseException as exc:
        exit_code = controller.EXIT_INTERRUPTED if isinstance(exc, (KeyboardInterrupt, SystemExit)) else getattr(exc, "exit_code", controller.EXIT_INTERNAL)
        report["state"], report["exit_code"] = "FAILED_CLOSED", exit_code
        report["failure"] = controller.sanitize_for_report({"phase": getattr(exc, "phase", "publisher"), "error": controller.redact(exc), "detail": getattr(exc, "detail", None)})
        if report["remote_mutation"] == "ATTEMPT_IN_PROGRESS":
            report["remote_mutation"] = "SEE_FAILURE_DETAIL_OR_UNKNOWN_AFTER_ATTEMPT"
    finally:
        report["ended_at"] = controller.utc_now()
        if report_ready:
            controller.atomic_write(report_path, controller.pretty_json_bytes(report))
        print(json.dumps(controller.sanitize_for_report({**report, "report_path": str(report_path) if report_ready else None}), sort_keys=True))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
