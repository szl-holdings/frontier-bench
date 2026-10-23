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
import re
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
TARGET = "SZLHOLDINGS/szl-bench-suite"
EXPECTED_USER = "betterwithage"
TARGET_ORG = TARGET.split("/", 1)[0]
ORG_WRITE_ROLES = {"admin", "write"}
BUNDLE_FILES = {"README.md", "index.html", "results.json"}
ALLOWED_LIVE_HOSTS = {"szlholdings-szl-bench-suite.hf.space", "szlholdings-szl-bench-suite.static.hf.space"}


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


def _bundle_inventory(directory: Path) -> tuple[set[str], set[str]]:
    """Return (files, directories) below the bundle root as POSIX paths, never following links."""
    files: set[str] = set()
    directories: set[str] = set()
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        relative = Path(current).relative_to(directory)
        for name in dirnames:
            directories.add((relative / name).as_posix())
        for name in filenames:
            files.add((relative / name).as_posix())
    return files, directories


def read_bundle(controller: ModuleType, directory: Path) -> tuple[dict[str, bytes], dict[str, Any]]:
    directory = Path(os.path.abspath(directory.expanduser()))
    controller._reject_link_components(directory, phase="bundle_admission", exit_code=controller.EXIT_RESULT)
    expected_files = BUNDLE_FILES | set(controller.SPACE_STATIC_ASSETS)
    expected_directories = {parent.as_posix() for name in controller.SPACE_STATIC_ASSETS for parent in Path(name).parents if parent != Path(".")}
    if not directory.is_dir() or _bundle_inventory(directory) != (expected_files, expected_directories):
        raise controller.BenchError("bundle_admission", "bundle must contain exactly README.md, index.html, results.json, and the reviewed szl/ design-system assets", controller.EXIT_RESULT)
    files = {}
    for name in sorted(expected_files):
        path = directory.joinpath(*name.split("/"))
        controller._reject_link_components(path, phase="bundle_admission", exit_code=controller.EXIT_RESULT)
        files[name] = controller.read_bounded_regular_file(path, limit=controller.MAX_HTTP_BYTES,
                                                           phase="bundle_admission", exit_code=controller.EXIT_RESULT)
    controller.validate_static_space_readme(files["README.md"])
    template = controller.read_bounded_regular_file(CONTROLLER_DIR / "szl-bench-suite.index.html",
                                                   limit=controller.MAX_HTTP_BYTES, phase="bundle_admission",
                                                   exit_code=controller.EXIT_RESULT)
    if controller.finalize_space_index(template, files["results.json"]) != files["index.html"]:
        raise controller.BenchError("bundle_admission", "bundle index differs from the reviewed template finalized for these results bytes", controller.EXIT_RESULT)
    reviewed_assets = controller.load_space_static_assets(CONTROLLER_DIR / "szl-bench-suite.index.html",
                                                          phase="bundle_admission", exit_code=controller.EXIT_RESULT)
    if any(files[name] != data for name, data in reviewed_assets.items()):
        raise controller.BenchError("bundle_admission", "bundle szl/ design-system assets differ from the reviewed vendored files", controller.EXIT_RESULT)
    payload = controller.strict_json_from_bytes(files["results.json"], source="bundle/results.json",
                                                max_bytes=controller.MAX_HTTP_BYTES)
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

    None means authentication is needed for a changed bundle or runtime, or to
    delete a retired file that is still present. This function has no commit,
    upload, delete, restart, or other mutation path.
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
    if not set(files).issubset(remote_files):
        return None
    if remote_files & set(controller.SPACE_RETIRED_FILES):
        # A retired file is still served; only the authenticated controller can delete it.
        return None
    download_root = controller.ensure_private_directory(run_root / "anonymous-downloads", phase="anonymous_witness", exit_code=controller.EXIT_PROVIDER)
    hashes: dict[str, str] = {}
    for name, expected in files.items():
        observed = controller._download_hub_file_strict(TARGET, name, before_sha, False, download_root)
        if observed != expected:
            return None
        hashes[name] = controller.sha256_bytes(observed)
    public_hashes: dict[str, str] = {}
    public_index_evidence: dict[str, str | None] | None = None
    for name, route in (("index.html", "index.html"), ("results.json", "results.json")):
        observed, headers = controller.http_get_bytes(f"{live_url}{route}?run={before_sha}", timeout=15,
                                                       max_bytes=controller.MAX_HTTP_BYTES, expect_json=name.endswith(".json"))
        if name == "index.html" and "text/html" not in str(headers.get("Content-Type", "")).lower():
            raise controller.BenchError("anonymous_witness", "public index has the wrong content type", controller.EXIT_PROVIDER)
        if name == "index.html":
            public_index_evidence = controller.verify_live_static_index(
                files[name], observed, reviewed_readme=files["README.md"], phase="anonymous_witness"
            )
        else:
            controller.require_live_revision_header(headers, before_sha, phase="anonymous_witness")
            if observed != files[name]:
                raise controller.BenchError("anonymous_witness", f"public {name} differs from the verified bundle", controller.EXIT_PROVIDER)
        public_hashes[name] = controller.sha256_bytes(observed)
    public_asset_hashes = controller.verify_public_static_assets(
        live_url, before_sha, {name: files[name] for name in controller.SPACE_STATIC_ASSETS}, phase="anonymous_witness"
    )
    after = api.space_info(repo_id=TARGET)
    after_runtime = api.get_space_runtime(repo_id=TARGET)
    if (str(getattr(after, "sha", "")) != before_sha or str(getattr(after_runtime, "stage", "UNKNOWN")) != "RUNNING"
            or bool(getattr(after, "private", True)) or getattr(after, "sdk", "") != "static" or _live_url(after) != live_url):
        raise controller.BenchError("anonymous_witness", "Space head, host, or runtime changed during the public witness", controller.EXIT_PROVIDER)
    return {
        "space": TARGET, "space_url": f"{live_url}index.html", "publisher": "ANONYMOUS_READ_ONLY",
        "public_index_url": f"{live_url}index.html?run={before_sha}",
        "parent_commit": before_sha, "commit": before_sha, "changed": False,
        "authenticated_write": False, "provider_stage": "RUNNING",
        "immutable_readback_sha256": hashes["results.json"], "immutable_index_sha256": hashes["index.html"],
        "immutable_readme_sha256": hashes["README.md"], "public_results_sha256": public_hashes["results.json"],
        "immutable_static_asset_sha256": {name: hashes[name] for name in controller.SPACE_STATIC_ASSETS},
        "public_static_asset_sha256": public_asset_hashes,
        "public_results_revision": before_sha,
        "public_index_sha256": public_hashes["index.html"],
        "public_index_normalized_sha256": (public_index_evidence or {}).get("normalized_sha256"),
        "public_index_transform": (public_index_evidence or {}).get("transform"),
        "public_index_injection_sha256": (public_index_evidence or {}).get("injection_sha256"),
    }


TOKEN_KINDS = ("read", "write", "fineGrained")


def _token_shape(access_token: Any) -> tuple[str, list[str]]:
    """Non-secret token metadata: the provider role and the entity types holding repo.write.

    Lets a failed selection distinguish a read token, or a fine-grained token
    scoped only to the user, from a token scoped elsewhere, without names or values.
    """
    if not isinstance(access_token, dict):
        return "unknown", []
    role = access_token.get("role")
    kind = role if role in TOKEN_KINDS else "unknown"
    fine_grained = access_token.get("fineGrained")
    scoped = fine_grained.get("scoped") if isinstance(fine_grained, dict) else None
    types = {str(entry["entity"].get("type", "")).lower() for entry in (scoped if isinstance(scoped, list) else [])
             if isinstance(entry, dict) and isinstance(entry.get("entity"), dict)
             and isinstance(entry.get("permissions"), list) and "repo.write" in entry["permissions"]}
    return kind, sorted(name for name in types if name in ("user", "org", "model", "dataset", "space"))


def _token_can_write_target(access_token: Any) -> bool:
    """True only for a classic write token or a fine-grained repo.write scope on the org or this Space."""
    if not isinstance(access_token, dict):
        return False
    role = access_token.get("role")
    if role == "write":
        return True
    if role != "fineGrained":
        return False
    fine_grained = access_token.get("fineGrained")
    scoped = fine_grained.get("scoped") if isinstance(fine_grained, dict) else None
    for entry in scoped if isinstance(scoped, list) else []:
        entity = entry.get("entity") if isinstance(entry, dict) else None
        permissions = entry.get("permissions") if isinstance(entry, dict) else None
        if not isinstance(entity, dict) or not isinstance(permissions, list) or "repo.write" not in permissions:
            continue
        kind, name = str(entity.get("type", "")).lower(), str(entity.get("name", ""))
        if (kind == "org" and name.lower() == TARGET_ORG.lower()) or (kind == "space" and name.lower() == TARGET.lower()):
            return True
    return False


def check_write_credential(controller: ModuleType, token: str) -> dict[str, Any]:
    """Check the provider's current write authorization metadata without writing.

    Read-only calls only (whoami, space_info with resourceGroup expansion).
    This is a point-in-time authorization check, not an attempted write. The
    result carries the identity name and booleans, never provider bodies.
    """
    from huggingface_hub import HfApi

    evidence: dict[str, Any] = {"identity": None, "identity_matches": False, "token_kind": "unknown",
                                "token_write_entity_types": [], "token_write_scope": False,
                                "org_write_role": False, "org_policy_clear": False,
                                "space_visible": False, "resource_group_write_access": False}
    api = HfApi(token=token)
    try:
        identity = api.whoami()
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise controller.BenchError("hub_auth", f"Hugging Face rejected the write credential (HTTP {status or 'unknown'})",
                                    controller.EXIT_HUB_AUTH, detail=evidence) from None
    if not isinstance(identity, dict):
        raise controller.BenchError("hub_auth", "Hugging Face returned an unreadable identity", controller.EXIT_HUB_AUTH, detail=evidence)
    name = str(identity.get("name", ""))
    evidence["identity"] = name
    evidence["identity_matches"] = identity.get("type", "user") == "user" and name.lower() == EXPECTED_USER.lower()
    auth = identity.get("auth")
    access_token = auth.get("accessToken") if isinstance(auth, dict) else None
    evidence["token_kind"], evidence["token_write_entity_types"] = _token_shape(access_token)
    evidence["token_write_scope"] = _token_can_write_target(access_token)
    orgs = identity.get("orgs")
    matching_orgs = [org for org in (orgs if isinstance(orgs, list) else [])
                     if isinstance(org, dict) and str(org.get("name", "")).lower() == TARGET_ORG.lower()]
    org = matching_orgs[0] if len(matching_orgs) == 1 else {}
    evidence["org_write_role"] = org.get("roleInOrg") in ORG_WRITE_ROLES
    # whoami-v2 declares restrictions for this authentication method. Public
    # repository reads do not override token-policy, MFA, SSO, revocation or IP
    # restrictions. Absent is allowed by the provider's optional-field schema.
    restrictions = org.get("securityRestrictions", [])
    evidence["org_policy_clear"] = bool(org) and isinstance(restrictions, list) and not restrictions
    if not evidence["identity_matches"]:
        raise controller.BenchError("hub_auth", f"write credential belongs to {name!r}, expected {EXPECTED_USER!r}", controller.EXIT_HUB_AUTH, detail=evidence)
    if not evidence["token_write_scope"]:
        raise controller.BenchError("hub_auth", f"write credential lacks write scope for {TARGET}", controller.EXIT_HUB_AUTH, detail=evidence)
    if not evidence["org_write_role"]:
        raise controller.BenchError("hub_auth", f"{name!r} lacks an admin or write role in {TARGET_ORG}", controller.EXIT_HUB_AUTH, detail=evidence)
    if not evidence["org_policy_clear"]:
        raise controller.BenchError("hub_auth", f"write credential has unresolved security restrictions in {TARGET_ORG}", controller.EXIT_HUB_AUTH, detail=evidence)
    try:
        # The default public Space response may omit its resource group. Ask
        # explicitly so a readable public repository cannot hide a denied ACL.
        info = api.space_info(repo_id=TARGET, expand=["resourceGroup"])
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise controller.BenchError("hub_auth", f"write credential could not read {TARGET} (HTTP {status or 'unknown'})",
                                    controller.EXIT_HUB_AUTH, detail=evidence) from None
    evidence["space_visible"] = str(getattr(info, "id", "")).lower() == TARGET.lower()
    if not evidence["space_visible"]:
        raise controller.BenchError("hub_auth", "provider returned an unexpected Space identity", controller.EXIT_HUB_AUTH, detail=evidence)
    group = getattr(info, "resource_group", False)
    if group is None:
        # The pinned SDK maps both omitted and null resourceGroup to None.
        # That is not proof of an ungrouped repository. Only the documented
        # org-admin override can establish access without group metadata.
        evidence["resource_group_write_access"] = org.get("roleInOrg") == "admin"
    elif isinstance(group, dict) and isinstance(group.get("id"), str) and re.fullmatch(r"[0-9a-f]{24}", group["id"]):
        memberships = org.get("resourceGroups")
        matching_groups = [entry for entry in (memberships if isinstance(memberships, list) else [])
                           if isinstance(entry, dict) and entry.get("id") == group["id"]]
        evidence["resource_group_write_access"] = org.get("roleInOrg") == "admin" or (
            len(matching_groups) == 1 and matching_groups[0].get("role") in ORG_WRITE_ROLES
        )
    if not evidence["resource_group_write_access"]:
        raise controller.BenchError("hub_auth", "write credential lacks verified access to the Space resource group", controller.EXIT_HUB_AUTH, detail=evidence)
    return evidence


def run_credential_check(controller: ModuleType, use_cached_auth: bool) -> int:
    report: dict[str, Any] = {
        "schema_version": "szl-static-bench-credential-check/v1", "mode": "CHECK_WRITE_CREDENTIAL",
        "target": TARGET, "checked_at": controller.utc_now(), "remote_mutation": "NOT_ATTEMPTED",
        "state": "IN_PROGRESS",
    }
    try:
        token = os.environ.get("HF_TOKEN")
        if not token and use_cached_auth:
            from huggingface_hub import get_token
            token = get_token()
        if not token:
            raise controller.BenchError("hub_auth", "HF_TOKEN is required for the write-credential check", controller.EXIT_HUB_AUTH)
        report["credential"] = check_write_credential(controller, token)
        report["state"], report["exit_code"] = "WRITE_CREDENTIAL_VERIFIED", 0
    except (KeyboardInterrupt, SystemExit):
        report["state"], report["exit_code"] = "FAILED_CLOSED", controller.EXIT_INTERRUPTED
        report["failure"] = {"phase": "hub_auth", "error": "credential check interrupted"}
    except Exception as exc:
        exit_code = getattr(exc, "exit_code", controller.EXIT_INTERNAL)
        report["state"], report["exit_code"] = "FAILED_CLOSED", exit_code
        if isinstance(getattr(exc, "detail", None), dict):
            report["credential"] = exc.detail
        report["failure"] = {"phase": getattr(exc, "phase", "hub_auth"), "error": controller.redact(exc)}
    print(json.dumps(controller.sanitize_for_report(report), sort_keys=True))
    return int(report["exit_code"])


def run_credential_selection(controller: ModuleType) -> int:
    """Choose one admitted Actions credential; export its label, never its value."""
    report: dict[str, Any] = {"schema_version": "szl-static-bench-credential-selection/v1",
                              "target": TARGET, "remote_mutation": "NOT_ATTEMPTED",
                              "state": "FAILED_CLOSED", "candidates": []}
    selected = None
    try:
        output_path = os.environ.get("GITHUB_OUTPUT")
        if not output_path:
            raise controller.BenchError("hub_auth", "GITHUB_OUTPUT is required for credential selection", controller.EXIT_HUB_AUTH)
        for name in ("HF_ORG_TOKEN", "HF_TOKEN"):
            token = os.environ.get(f"CANDIDATE_{name}")
            row: dict[str, Any] = {"secret": name, "present": bool(token), "verified": False}
            report["candidates"].append(row)
            if not token:
                continue
            try:
                row["credential"] = check_write_credential(controller, token)
            except controller.BenchError as exc:
                row["failure"] = {"phase": exc.phase, "error": controller.redact(exc)}
                if isinstance(exc.detail, dict):
                    row["credential"] = exc.detail  # identity and booleans only, never provider bodies
                continue
            row["verified"] = True
            selected = name
            break
        if selected is None:
            raise controller.BenchError("hub_auth", "no candidate has verified write authorization for the target Space", controller.EXIT_HUB_AUTH)
        # Both the publisher and the scheduled canary resolve the same selected
        # label through Actions expressions. No token is written to job output.
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"pick={selected}\n")
        report.update(state="WRITE_CREDENTIAL_SELECTED", selected=selected, exit_code=0)
    except (KeyboardInterrupt, SystemExit):
        report.update(exit_code=controller.EXIT_INTERRUPTED, failure={"phase": "hub_auth", "error": "credential selection interrupted"})
    except Exception as exc:
        report.update(exit_code=getattr(exc, "exit_code", controller.EXIT_INTERNAL),
                      failure={"phase": getattr(exc, "phase", "hub_auth"), "error": controller.redact(exc)})
    print(json.dumps(controller.sanitize_for_report(report), sort_keys=True))
    return int(report["exit_code"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, help="directory exported by the reviewed controller audit (required unless --check-write-credential)")
    parser.add_argument("--select-write-credential", action="store_true", help="validate Actions credential candidates and emit only the selected secret label")
    parser.add_argument("--check-write-credential", action="store_true", help="check HF_TOKEN authorization metadata for the Space using read-only calls; never publishes")
    parser.add_argument("--git-bin", help="optional absolute trusted Git executable path")
    parser.add_argument("--report", type=Path, help="new local JSON report path; defaults to .bench-plane-publish/<run-id>.json")
    parser.add_argument("--verify-only", action="store_true", help="verify the existing public bundle anonymously; never resolve write credentials or modify the Space")
    parser.add_argument("--use-cached-auth", "--cached-auth", dest="use_cached_auth", action="store_true", help="explicitly permit reading the local Hugging Face login token in this process")
    parser.add_argument("--provider-timeout", type=positive_timeout, default=600.0)
    parser.add_argument("--public-http-deadline", type=positive_timeout, default=180.0)
    args = parser.parse_args(argv)
    if args.select_write_credential:
        if args.check_write_credential or args.verify_only or args.bundle_dir or args.report or args.use_cached_auth:
            parser.error("--select-write-credential cannot be combined with publication, cached authentication or credential-check arguments")
        return run_credential_selection(load_controller())
    if args.check_write_credential:
        if args.verify_only or args.bundle_dir or args.report:
            parser.error("--check-write-credential cannot be combined with --verify-only, --bundle-dir, or --report")
        return run_credential_check(load_controller(), args.use_cached_auth)
    if args.bundle_dir is None:
        parser.error("--bundle-dir is required")
    controller = load_controller()
    run_id = controller.utc_now().replace(":", "").replace("-", "") + "-" + secrets.token_hex(4)
    report_path = Path(os.path.abspath((args.report or Path.cwd() / ".bench-plane-publish" / f"{run_id}.json").expanduser()))
    report: dict[str, Any] = {
        "schema_version": "szl-static-bench-publication/v1", "run_id": run_id,
        "started_at": controller.utc_now(), "target": TARGET, "space_url": f"{controller.SPACE_URL}/index.html",
        "mode": "VERIFY_ONLY" if args.verify_only else "PUBLISH",
        "controller_sha256": controller.sha256_file(CONTROLLER_PATH),
        "state": "IN_PROGRESS", "remote_mutation": "NOT_ATTEMPTED",
        "measurements": "NOT_PERFORMED_BY_PUBLISHER", "local_runtime": "NOT_REQUESTED",
    }
    report_ready = False
    try:
        controller._reject_link_components(report_path, phase="report", exit_code=controller.EXIT_CLI)
        if report_path.exists():
            raise controller.BenchError("report", "refusing to overwrite an existing publication report", controller.EXIT_CLI)
        # A caller-selected report may live in a shared directory. Protect the
        # new file, but never chmod existing caller-owned parent directories.
        report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        controller._reject_link_components(report_path.parent, phase="report", exit_code=controller.EXIT_CLI)
        if not report_path.parent.is_dir():
            raise controller.BenchError("report", "report parent must be a directory", controller.EXIT_CLI)
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
            added_cached_token = False
            try:
                # Even when credentials are available, unchanged healthy data
                # must be witnessed anonymously before resolving write authority.
                outcome = verify_anonymous_noop(controller, files, run_root)
                if outcome is None:
                    if args.verify_only:
                        raise controller.BenchError("verification_only", "the existing public bundle or runtime could not be verified; read-only mode cannot publish or repair it", controller.EXIT_PROVIDER)
                    previous_token = os.environ.get("HF_TOKEN")
                    if not previous_token and args.use_cached_auth:
                        from huggingface_hub import get_token
                        cached_token = get_token()
                        if cached_token:
                            os.environ["HF_TOKEN"] = cached_token
                            added_cached_token = True
                        cached_token = None
                    if not os.environ.get("HF_TOKEN"):
                        raise controller.BenchError("hub_auth", "HF_TOKEN is required because the bundle or runtime needs a write; cached login is read only with --use-cached-auth", controller.EXIT_HUB_AUTH)
                    hub_args = argparse.Namespace(hf_token_env="HF_TOKEN", expected_hf_user=EXPECTED_USER,
                                                  space_readme=str(args.bundle_dir / "README.md"),
                                                  space_index=str(CONTROLLER_DIR / "szl-bench-suite.index.html"))
                    context = controller.hub_preflight(hub_args, run_root / "hub-downloads")
                    advertised_url = _live_url(context.api.space_info(repo_id=TARGET))
                    if advertised_url.rstrip("/") != controller.SPACE_URL.rstrip("/"):
                        raise controller.BenchError("hub_auth", "provider host differs from the reviewed controller's static host", controller.EXIT_HUB_AUTH)
                    if (context.readme_bytes != files["README.md"]
                            or controller.finalize_space_index(context.index_template_bytes, files["results.json"]) != files["index.html"]
                            or dict(context.static_asset_bytes) != {name: files[name] for name in controller.SPACE_STATIC_ASSETS}):
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
