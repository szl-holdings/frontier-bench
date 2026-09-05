#!/usr/bin/env python3
"""Deploy and verify the SZL bench evidence plane without inventing evidence.

This controller intentionally separates four facts:

* source integrity: exact, embedded Git commit pins and audited runtime files;
* data integrity: receipt hashes/chains independently recomputed here;
* local runtime: immutable containers witnessed on loopback before and after restart;
* publication: an optional, required-for-``published`` Hugging Face commit, readback,
  provider-state check, and public payload witness.

It never executes repository-controlled code and does not run the three benchmark
harnesses.  Every MEASURED receipt must carry the v3 operator-HMAC envelope.  If the
pinned repositories contain only their BLOCKED genesis receipts, the data state is
EMPTY_HONEST.  That describes the admitted data; service operation is witnessed
separately.  Windows is audit-only; deployment requires the qualified Linux node.

Recommended invocations on the dedicated node:

    python -I -B finish_bench_plane.py --audit-only --workdir ~/szl-bench
    python -I -B finish_bench_plane.py --preflight-only --target local
    python -I -B finish_bench_plane.py --target local
    python -I -B finish_bench_plane.py --target published \
        --space-readme ./szl-bench-suite.README.md \
        --space-index ./szl-bench-suite.index.html

For ``--target published``, put the Hugging Face token in HF_TOKEN.  Tokens are
never accepted on the command line and are never passed to Git or Docker.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import datetime as dt
import hashlib
import hmac
import inspect
import io
import json
import math
import os
import pathlib
import platform
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Iterable, Mapping, Sequence


VERSION = "2.0.0"
MANAGED_BY = "finish-bench-plane-v2"
SPACE_ID = "betterwithage/szl-bench-suite"
SPACE_URL = "https://betterwithage-szl-bench-suite.static.hf.space"
RUNTIME_BASE_IMAGE = "python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"
MAX_JSON_BYTES = 1_048_576
MAX_HTTP_BYTES = 4_194_304
MAX_RECEIPTS = 10_000
MAX_RECEIPT_BYTES_TOTAL = 64 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 100_000
MAX_TREE_BYTES = 4 * 1024 * 1024
MAX_TREE_ENTRIES = 20_000
ZERO_HASH = "0" * 64
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
RECEIPT_NAME_RE = re.compile(r"^([0-9]{3,})[-_].+\.json$")
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
SECRET_PATTERN = re.compile(
    r"(?i)(?:hf_|gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_\-]{12,}"
)
BEARER_PATTERN = re.compile(r"(?i)\b(?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b")
RECEIPT_AUDIENCE = SPACE_ID
RECEIPT_KEY_ID = "szl-bench-node-hmac-v1"
RECEIPT_KEY_ENV = "SZL_BENCH_RECEIPT_HMAC_KEY_HEX"
RECEIPT_DOMAIN = b"SZL-BENCH-RECEIPT-V3\0"
RESULT_DIGEST_PLACEHOLDER = "__RESULTS_JSON_SHA256__"
# Updated deliberately whenever either reviewed publication asset changes.
SPACE_README_SHA256 = "c3d6f9c45e81db69dacd79dab23c0fa2b67abea0a0d22b23f051c4ca0ab347e9"
SPACE_INDEX_TEMPLATE_SHA256 = "45d8fb2f202075b9c6093fc3c8e4cc6433d6c9033c1b38fba1dcf59bd9f1b309"


EXIT_CLI = 2
EXIT_PREFLIGHT = 10
EXIT_LOCKED = 11
EXIT_SOURCE = 20
EXIT_RECEIPT = 30
EXIT_RESULT = 31
EXIT_BUILD = 40
EXIT_RUNTIME = 41
EXIT_CUTOVER = 42
EXIT_HUB_AUTH = 50
EXIT_HUB_COMMIT = 51
EXIT_HUB_READBACK = 52
EXIT_PROVIDER = 53
EXIT_ROLLBACK = 60
EXIT_INTERNAL = 70
EXIT_INTERRUPTED = 130


@dataclasses.dataclass(frozen=True)
class RepoSpec:
    plane: str
    repo: str
    port: int
    revision: str
    genesis: str
    runtime_hashes: Mapping[str, str]
    test_hashes: Mapping[str, str]

    @property
    def url(self) -> str:
        return f"https://github.com/szl-holdings/{self.repo}.git"


REPOS: tuple[RepoSpec, ...] = (
    RepoSpec(
        plane="engine",
        repo="frontier-bench",
        port=7861,
        revision="8b1a3ecb5b567b7836f1d9242694722d316e241c",
        genesis="de352dd39bc106b2d64f8f4cad536e4a26317da6c38d64e282dcb107ab179c37",
        runtime_hashes={
            "Dockerfile": "9f80f7df758fd9103579149a1daff70aadbebf5d62e2a9f2dfd20d41f6c80eeb",
            "requirements.txt": "ff111be454a639448aad6d0da9e943fcc7b214231cf28e99360071632a2cf0a1",
            "app/main.py": "0751b18b2692f2f82f4ac72eb67b16ef8cb33dcd6ba06d2cc698a413a911c51b",
        },
        test_hashes={
            "tests/test_metrics.py": "a7f5a3e07c9015f5196c9c15c8f461169631072e068a54866d380def6918df0f",
            "tests/test_merge_results.py": "6a299636e4e41fb7dbb05b98e1a631bd8f6c1961c2b678f35f265e977584bcc2",
            "tests/test_space_bundle.py": "d250c2ef10ab6eb7f9a9d9b45678f4738029dc8c800e4f125c0b1fe22a0aca30",
            "tests/test_sync_results.py": "656bf404c5ed62a64c1f82f47694754ed6919b089d8808356c0fceffa1ec761d",
        },
    ),
    RepoSpec(
        plane="retrieval",
        repo="retrieval-bench",
        port=7862,
        revision="ca61ce3f294db1b6deca9e0734c5605e6f58a01b",
        genesis="dff9bc08a2b4cfed07337b8eb31ca7e6382f4a9da87314cc0e328a89fb89ab06",
        runtime_hashes={
            "Dockerfile": "9f80f7df758fd9103579149a1daff70aadbebf5d62e2a9f2dfd20d41f6c80eeb",
            "requirements.txt": "ff111be454a639448aad6d0da9e943fcc7b214231cf28e99360071632a2cf0a1",
            "app/main.py": "40d06b9b7fe2497b3e696b098a3e44c864096f0b6c7c3afd726e6a861514c1e5",
        },
        test_hashes={
            "tests/test_multivector.py": "438e21fa0e3432a1732762f86296d758e061c35c371c6276477761ec2c828b69",
            "tests/test_retrieval.py": "8ed5626f77b71ff3f4c0f259f58b542a70746ad9010a310c85e12f9a65b59608",
        },
    ),
    RepoSpec(
        plane="quant",
        repo="quant-curve",
        port=7863,
        revision="3710a43d2566ea1bf820f5687aad07d3de8ea769",
        genesis="cc56e0f6efe1479dd3daccc23d4dabcc0d78de079dac39509f5e4e0777143c0a",
        runtime_hashes={
            "Dockerfile": "9f80f7df758fd9103579149a1daff70aadbebf5d62e2a9f2dfd20d41f6c80eeb",
            "requirements.txt": "ff111be454a639448aad6d0da9e943fcc7b214231cf28e99360071632a2cf0a1",
            "app/main.py": "0d66c6a9c6c7ac377a6eab95c6c6ee604b45aa47f053ee4ce0c46c335a4d4eba",
        },
        test_hashes={
            "tests/test_quant_curve.py": "fcdee40993e1cb102487f1f191662828c957c1d7096d23cec3fe80fa8b2c5f69",
        },
    ),
)

EXPECTED_MACHINE = {
    "cpu_contains": "i9-14900HX",
    "ram_min_gib": 120.0,
    "gpu_contains": "RTX 4000 Ada",
    "gpu_min_mib": 19_000.0,
    "receipt": {"cpu": "i9-14900HX", "ram_gb": 128, "gpu": "RTX 4000 Ada 20GB"},
}

REQUIRED_RECEIPT_FIELDS = {
    "plane",
    "status",
    "machine",
    "measured_at",
    "method",
    "metrics",
    "prev_hash",
    "hash",
}
MEASURED_RECEIPT_FIELDS = REQUIRED_RECEIPT_FIELDS | {
    "schema_version",
    "audience",
    "source_revision",
    "workload",
    "artifacts",
    "hardware_evidence_sha256",
    "auth",
}
RECEIPT_STATUSES = {"MEASURED", "BLOCKED", "INVALID", "FAILED", "PROMOTED"}
PLANE_METRIC_FIELDS: Mapping[str, Mapping[str, str]] = {
    "engine": {
        "model": "text",
        "precision": "text",
        "prompt_tps": "nonnegative",
        "decode_tps": "nonnegative",
        "peak_vram_gb": "nonnegative",
    },
    "retrieval": {
        "corpus": "text",
        "method": "text",
        "ndcg10": "unit",
        "recall100": "unit",
        "mrr": "unit",
        "p50_ms": "nonnegative",
    },
    "quant": {
        "model": "text",
        "precision": "text",
        "perplexity": "positive",
        "decode_tps": "nonnegative",
        "peak_vram_gb": "nonnegative",
    },
}

SERVER_SOURCE = r'''#!/usr/bin/env python3
import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def load_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} is not an object")
    return value


IDENTITY = load_object("/srv/identity.json")
PAYLOAD = load_object("/srv/results.json")
ROWS = PAYLOAD.get("results")
if not isinstance(ROWS, list) or PAYLOAD.get("count") != len(ROWS):
    raise RuntimeError("results/count invariant failed")
if any(not isinstance(row, dict) or row.get("plane") != IDENTITY["plane"] for row in ROWS):
    raise RuntimeError("result plane invariant failed")
EXPECTED_STATE = "MEASURED" if ROWS else "EMPTY_HONEST"
if PAYLOAD.get("data_state") != EXPECTED_STATE:
    raise RuntimeError("data-state invariant failed")
if PAYLOAD.get("results_sha256") != IDENTITY["results_sha256"]:
    raise RuntimeError("results digest identity invariant failed")


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Handler(BaseHTTPRequestHandler):
    server_version = "szl-bench-api/2"

    def log_message(self, fmt, *args):
        return

    def send_json(self, status, value):
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "plane": IDENTITY["plane"], "run_id": IDENTITY["run_id"], "ts": now()})
            return
        if path in ("/readyz", "/api/build-info"):
            self.send_json(200, {**IDENTITY, "status": "ready", "data_state": EXPECTED_STATE, "count": len(ROWS)})
            return
        if path == "/api/results":
            self.send_json(200, {
                "plane": IDENTITY["plane"],
                "state": EXPECTED_STATE,
                "generated_at": PAYLOAD.get("generated_at"),
                "count": len(ROWS),
                "results": ROWS,
                "results_sha256": IDENTITY["results_sha256"],
                "run_id": IDENTITY["run_id"],
                "source_revision": IDENTITY["source_revision"],
                "served_at": now(),
            })
            return
        self.send_json(404, {"error": "not_found"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


Server(("0.0.0.0", 7860), Handler).serve_forever()
'''


class BenchError(RuntimeError):
    def __init__(self, phase: str, message: str, exit_code: int, *, detail: Any = None):
        super().__init__(message)
        self.phase = phase
        self.exit_code = exit_code
        self.detail = detail


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def redact(value: Any) -> str:
    text = str(value)
    text = BEARER_PATTERN.sub("[REDACTED_AUTHORIZATION]", text)
    text = JWT_PATTERN.sub("[REDACTED_JWT]", text)
    text = SECRET_PATTERN.sub("[REDACTED_TOKEN]", text)
    for key in ("authorization", "hf_token", "github_token", "access_token", "secret", "password"):
        text = re.sub(rf"(?i)({key}\s*[:=]\s*)(?:bearer\s+|basic\s+)?[^\s,;]+", r"\1[REDACTED]", text)
    return text


def sanitize_for_report(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        return "[TRUNCATED_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {redact(key): sanitize_for_report(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_report(item, depth=depth + 1) for item in value]
    return redact(value)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_from_bytes(data: bytes, *, source: str) -> Any:
    if len(data) > MAX_JSON_BYTES:
        raise BenchError("receipt_verification", f"{source}: JSON exceeds {MAX_JSON_BYTES} bytes", EXIT_RECEIPT)
    try:
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
        _validate_json_shape(value, source=source)
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise BenchError("receipt_verification", f"{source}: invalid strict JSON: {exc}", EXIT_RECEIPT) from exc


def _validate_json_shape(value: Any, *, source: str) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ValueError(f"{source}: JSON node count exceeds {MAX_JSON_NODES}")
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"{source}: JSON nesting exceeds {MAX_JSON_DEPTH}")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _is_reparse_or_symlink(path: pathlib.Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attrs = path.lstat().st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def require_regular_file(path: pathlib.Path, *, phase: str, exit_code: int) -> None:
    if _is_reparse_or_symlink(path) or not path.is_file():
        raise BenchError(phase, f"expected a regular, non-link file: {path}", exit_code)


def read_bounded_regular_file(path: pathlib.Path, *, limit: int, phase: str, exit_code: int) -> bytes:
    require_regular_file(path, phase=phase, exit_code=exit_code)
    before = path.stat()
    if before.st_size > limit:
        raise BenchError(phase, f"file exceeds {limit} bytes: {path}", exit_code)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BenchError(phase, f"could not safely open {path}: {exc}", exit_code) from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise BenchError(phase, f"file identity changed while opening: {path}", exit_code)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise BenchError(phase, f"file changed while reading: {path}", exit_code)
    finally:
        os.close(descriptor)
    if len(data) > limit:
        raise BenchError(phase, f"file exceeds {limit} bytes: {path}", exit_code)
    return data


def atomic_write(path: pathlib.Path, data: bytes, *, mode: int = 0o600) -> None:
    _reject_link_components(path.parent, phase="evidence", exit_code=EXIT_INTERNAL)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_components(path.parent, phase="evidence", exit_code=EXIT_INTERNAL)
    if path.exists() and _is_reparse_or_symlink(path):
        raise BenchError("evidence", f"refusing to replace a link/reparse point: {path}", EXIT_INTERNAL)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = pathlib.Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _reject_link_components(path: pathlib.Path, *, phase: str, exit_code: int) -> None:
    absolute = pathlib.Path(os.path.abspath(path))
    current = pathlib.Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.exists() and _is_reparse_or_symlink(current):
            raise BenchError(phase, f"path contains a link/reparse point: {current}", exit_code)


def ensure_private_directory(path: pathlib.Path, *, phase: str, exit_code: int) -> pathlib.Path:
    absolute = pathlib.Path(os.path.abspath(path))
    _reject_link_components(absolute, phase=phase, exit_code=exit_code)
    absolute.mkdir(parents=True, exist_ok=True, mode=0o700)
    _reject_link_components(absolute, phase=phase, exit_code=exit_code)
    if not absolute.is_dir():
        raise BenchError(phase, f"expected a directory: {absolute}", exit_code)
    with contextlib.suppress(OSError):
        os.chmod(absolute, 0o700)
    if os.name != "nt" and absolute.stat().st_mode & 0o077:
        raise BenchError(phase, f"directory is not private (expected mode 0700): {absolute}", exit_code)
    return absolute


def validate_workdir(raw: str) -> pathlib.Path:
    expanded = pathlib.Path(raw).expanduser()
    path = pathlib.Path(os.path.abspath(expanded))
    _reject_link_components(path, phase="cli", exit_code=EXIT_CLI)
    home = pathlib.Path.home().resolve(strict=False)
    anchor = pathlib.Path(path.anchor).resolve(strict=False)
    if path.resolve(strict=False) == home or path.resolve(strict=False) == anchor:
        raise BenchError("cli", "--workdir must be a dedicated subdirectory, not a home or filesystem root", EXIT_CLI)
    return ensure_private_directory(path, phase="cli", exit_code=EXIT_CLI)


class EvidenceReport:
    def __init__(self, path: pathlib.Path, run_id: str, args: argparse.Namespace):
        self.path = path
        self.data: dict[str, Any] = {
            "schema_version": "szl-bench-run/v2",
            "controller_version": VERSION,
            "controller_sha256": sha256_file(pathlib.Path(__file__).resolve()),
            "run_id": run_id,
            "started_at": utc_now(),
            "ended_at": None,
            "requested_target": args.target,
            "mode": "audit" if args.audit_only else "preflight" if args.preflight_only else "deploy",
            "layers": {
                "toolchain": {"state": "NOT_RUN"},
                "source": {"state": "NOT_RUN"},
                "source_tests": {"state": "NOT_EXECUTED_UNTRUSTED_SOURCE"},
                "source_authenticity": {"state": "NOT_RUN"},
                "source_ci": {"state": "UNVERIFIED_EXTERNAL"},
                "machine": {"state": "NOT_RUN"},
                "receipts": {"state": "NOT_RUN"},
                "measurements": {"state": "NOT_PERFORMED"},
                "data": {"state": "NOT_RUN"},
                "bundle_export": {"state": "NOT_REQUESTED" if not args.export_space_bundle else "NOT_RUN"},
                "local_runtime": {"state": "NOT_RUN"},
                "publication": {"state": "NOT_REQUESTED" if args.target == "local" else "NOT_RUN"},
                "provider": {"state": "NOT_REQUESTED" if args.target == "local" else "NOT_RUN"},
                "public_runtime": {"state": "NOT_REQUESTED" if args.target == "local" else "NOT_RUN"},
            },
            "overall": "IN_PROGRESS",
            "exit_code": None,
            "failure": None,
        }
        self.flush()

    def layer(self, name: str, state: str, **details: Any) -> None:
        self.data["layers"][name] = sanitize_for_report({"state": state, **details})
        self.flush()

    def finish(self, overall: str, exit_code: int, failure: Mapping[str, Any] | None = None) -> None:
        self.data["overall"] = overall
        self.data["exit_code"] = exit_code
        self.data["failure"] = sanitize_for_report(dict(failure)) if failure else None
        self.data["ended_at"] = utc_now()
        self.flush()

    def flush(self) -> None:
        atomic_write(self.path, pretty_json_bytes(self.data))


class RunLease:
    """Atomic cooperating-writer lease; stale leases require explicit operator recovery."""

    def __init__(self, path: pathlib.Path, run_id: str):
        self.path = path
        self.owner_path = path / "owner.json"
        self.run_id = run_id
        self.token = secrets.token_hex(32)
        self.owned = False

    def __enter__(self) -> "RunLease":
        ensure_private_directory(self.path.parent, phase="lock", exit_code=EXIT_LOCKED)
        try:
            os.mkdir(self.path, 0o700)
        except FileExistsError as exc:
            owner = "unreadable owner record"
            with contextlib.suppress(Exception):
                owner = redact(read_bounded_regular_file(self.owner_path, limit=16_384, phase="lock", exit_code=EXIT_LOCKED).decode("utf-8"))
            raise BenchError("lock", f"bench-plane lease already exists at {self.path}: {owner}", EXIT_LOCKED) from exc
        if _is_reparse_or_symlink(self.path):
            raise BenchError("lock", f"new lease path is a link/reparse point: {self.path}", EXIT_LOCKED)
        self.owned = True
        atomic_write(
            self.owner_path,
            pretty_json_bytes(
                {
                    "schema_version": "szl-bench-lock/v1",
                    "token": self.token,
                    "run_id": self.run_id,
                    "pid": os.getpid(),
                    "hostname": socket.gethostname(),
                    "created_at": utc_now(),
                }
            ),
        )
        return self

    def __exit__(self, *_: Any) -> None:
        if not self.owned:
            return
        owner = strict_json_from_bytes(
            read_bounded_regular_file(self.owner_path, limit=16_384, phase="lock", exit_code=EXIT_ROLLBACK),
            source=str(self.owner_path),
        )
        if not isinstance(owner, dict) or owner.get("token") != self.token:
            raise BenchError("lock", "lease ownership changed; refusing to remove it", EXIT_ROLLBACK)
        self.owner_path.unlink()
        self.path.rmdir()
        self.owned = False


def global_deploy_lease_path() -> pathlib.Path:
    identity = str(os.geteuid()) if hasattr(os, "geteuid") else sha256_bytes(str(pathlib.Path.home()).encode("utf-8"))[:16]
    root = pathlib.Path(tempfile.gettempdir()) / f"szl-bench-plane-v2-{identity}"
    return root / "active"


def sanitized_child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
        if not length or length >= len(buffer):
            raise BenchError("preflight", "could not resolve the Windows system directory", EXIT_PREFLIGHT)
        windows_dir = buffer.value
        env.update(
            {
                "SYSTEMROOT": windows_dir,
                "WINDIR": windows_dir,
                "COMSPEC": str(pathlib.Path(windows_dir) / "System32" / "cmd.exe"),
                "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            }
        )
    for locale_name in ("LANG", "LC_ALL"):
        if locale_name in os.environ:
            env[locale_name] = os.environ[locale_name]
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_ALLOW_PROTOCOL": "https",
            "GIT_PROTOCOL_FROM_USER": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
    )
    if extra:
        env.update(extra)
    return env


@dataclasses.dataclass
class CommandResult:
    returncode: int
    output: str
    elapsed_seconds: float


class CommandRunner:
    def __init__(self, *, quiet: bool = False):
        self.quiet = quiet

    @staticmethod
    def _capture(
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None,
        timeout: float,
        env_extra: Mapping[str, str] | None,
        merge_stderr: bool,
        stdout_limit: int | None,
    ) -> tuple[int, bytes, bytes, float, bool]:
        started = time.monotonic()
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd) if cwd else None,
            env=sanitized_child_env(env_extra),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            bufsize=0,
            shell=False,
            start_new_session=os.name != "nt",
        )
        if process.stdout is None or (not merge_stderr and process.stderr is None):
            with contextlib.suppress(Exception):
                process.kill()
            raise OSError("could not establish bounded subprocess pipes")
        stdout_data = bytearray()
        stderr_tail = bytearray()
        overflow = threading.Event()
        reader_errors: list[str] = []
        stdout_total = 0

        def terminate() -> None:
            # Linux deployment commands have a private process group. Killing it
            # also closes pipes held by helpers spawned by Git or Docker.
            if os.name != "nt":
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                process.kill()

        def retain_tail(target: bytearray, chunk: bytes, limit: int) -> None:
            target.extend(chunk)
            if len(target) > limit:
                del target[: len(target) - limit]

        def read_stdout() -> None:
            nonlocal stdout_total
            try:
                while True:
                    chunk = process.stdout.read(65_536)
                    if not chunk:
                        break
                    stdout_total += len(chunk)
                    if stdout_limit is None:
                        retain_tail(stdout_data, chunk, 65_536)
                    else:
                        remaining = max(0, stdout_limit + 1 - len(stdout_data))
                        if remaining:
                            stdout_data.extend(chunk[:remaining])
                        if stdout_total > stdout_limit and not overflow.is_set():
                            overflow.set()
                            terminate()
            except (OSError, ValueError) as exc:
                reader_errors.append(str(exc))
            finally:
                # The reading thread owns its raw pipe. Closing a buffered pipe
                # from the waiting thread can deadlock on the reader's lock.
                with contextlib.suppress(Exception):
                    process.stdout.close()

        def read_stderr() -> None:
            if merge_stderr or process.stderr is None:
                return
            try:
                while True:
                    chunk = process.stderr.read(65_536)
                    if not chunk:
                        break
                    retain_tail(stderr_tail, chunk, 65_536)
            except (OSError, ValueError) as exc:
                reader_errors.append(str(exc))
            finally:
                with contextlib.suppress(Exception):
                    process.stderr.close()

        readers = [threading.Thread(target=read_stdout, daemon=True)]
        if not merge_stderr:
            readers.append(threading.Thread(target=read_stderr, daemon=True))
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate()
            with contextlib.suppress(Exception):
                process.wait(timeout=2)
        except BaseException:
            # An interrupt during cutover must not leave a mutating Docker CLI
            # running while the caller restores the previous containers.
            terminate()
            with contextlib.suppress(BaseException):
                process.wait(timeout=2)
            raise
        finally:
            join_deadline = time.monotonic() + 2
            for reader in readers:
                reader.join(timeout=max(0.0, join_deadline - time.monotonic()))
            if any(reader.is_alive() for reader in readers):
                terminate()
                join_deadline = time.monotonic() + 1
                for reader in readers:
                    reader.join(timeout=max(0.0, join_deadline - time.monotonic()))
        if timed_out:
            raise subprocess.TimeoutExpired(list(command), timeout)
        if any(reader.is_alive() for reader in readers):
            raise OSError("subprocess pipes stayed open after command exit")
        if reader_errors:
            raise OSError("could not completely read subprocess output: " + "; ".join(reader_errors))
        if process.returncode is None:
            raise OSError("subprocess termination could not be confirmed")
        return process.returncode, bytes(stdout_data), bytes(stderr_tail), time.monotonic() - started, overflow.is_set()

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: pathlib.Path | None = None,
        timeout: float = 120.0,
        check: bool = True,
        phase: str = "command",
        exit_code: int = EXIT_INTERNAL,
        env_extra: Mapping[str, str] | None = None,
    ) -> CommandResult:
        if not command or not pathlib.Path(str(command[0])).is_absolute():
            raise BenchError(phase, f"bare executable name is forbidden: {command[0] if command else '<empty>'}", exit_code)
        shown = " ".join(str(part) for part in command)
        if not self.quiet:
            print("+", redact(shown), flush=True)
        try:
            returncode, stdout, _, elapsed, _ = self._capture(
                command,
                cwd=cwd,
                timeout=timeout,
                env_extra=env_extra,
                merge_stderr=True,
                stdout_limit=None,
            )
        except subprocess.TimeoutExpired as exc:
            raise BenchError(phase, f"command timed out after {timeout:.0f}s: {shown}", exit_code) from exc
        except OSError as exc:
            raise BenchError(phase, f"could not start command: {shown}: {exc}", exit_code) from exc
        retained = redact(stdout.decode("utf-8", errors="replace"))
        if retained and not self.quiet:
            print(retained, end="" if retained.endswith("\n") else "\n", flush=True)
        if check and returncode != 0:
            raise BenchError(
                phase,
                f"command failed ({returncode}): {shown}",
                exit_code,
                detail={"output_tail": retained[-8_192:]},
            )
        return CommandResult(returncode, retained, elapsed)

    def run_bytes(
        self,
        command: Sequence[str],
        *,
        timeout: float,
        max_bytes: int,
        phase: str,
        exit_code: int,
        env_extra: Mapping[str, str] | None = None,
    ) -> bytes:
        if not command or not pathlib.Path(str(command[0])).is_absolute():
            raise BenchError(phase, f"bare executable name is forbidden: {command[0] if command else '<empty>'}", exit_code)
        shown = " ".join(str(part) for part in command)
        if not self.quiet:
            print("+", redact(shown), flush=True)
        try:
            returncode, stdout, stderr, _, overflow = self._capture(
                command,
                cwd=None,
                timeout=timeout,
                env_extra=env_extra,
                merge_stderr=False,
                stdout_limit=max_bytes,
            )
        except subprocess.TimeoutExpired as exc:
            raise BenchError(phase, f"command timed out after {timeout:.0f}s: {shown}", exit_code) from exc
        except OSError as exc:
            raise BenchError(phase, f"could not start command: {shown}: {exc}", exit_code) from exc
        if overflow:
            raise BenchError(phase, f"command output exceeds {max_bytes} bytes: {shown}", exit_code)
        if returncode != 0:
            error = redact(stderr.decode("utf-8", errors="replace")[-8_192:])
            raise BenchError(phase, f"command failed ({returncode}): {shown}", exit_code, detail={"stderr_tail": error})
        return stdout


def resolve_trusted_executable(name: str, override: str | None = None, *, required: bool = True) -> str | None:
    candidates: list[pathlib.Path]
    if override:
        chosen = pathlib.Path(override).expanduser()
        if not chosen.is_absolute():
            raise BenchError("preflight", f"--{name.replace('_', '-')}-bin must be an absolute path", EXIT_PREFLIGHT)
        candidates = [chosen]
    elif os.name == "nt":
        windows = pathlib.Path(sanitized_child_env()["WINDIR"])
        if name == "git":
            candidates = [pathlib.Path(r"C:\Program Files\Git\cmd\git.exe"), pathlib.Path(r"C:\Program Files\Git\bin\git.exe")]
        elif name == "docker":
            candidates = [pathlib.Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")]
        elif name == "nvidia_smi":
            candidates = [windows / "System32" / "nvidia-smi.exe"]
        else:
            candidates = []
    else:
        binary = name.replace("_", "-")
        candidates = [pathlib.Path("/usr/bin") / binary, pathlib.Path("/usr/local/bin") / binary]
    for candidate in candidates:
        try:
            _reject_link_components(pathlib.Path(os.path.abspath(candidate)), phase="preflight", exit_code=EXIT_PREFLIGHT)
            resolved = candidate.resolve(strict=True)
        except (OSError, BenchError):
            continue
        if resolved.is_file() and not _is_reparse_or_symlink(resolved):
            if os.name != "nt":
                metadata = resolved.stat()
                if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not os.access(resolved, os.X_OK):
                    continue
            return str(resolved)
    if required:
        location = override or ", ".join(str(item) for item in candidates) or "no trusted default"
        raise BenchError("preflight", f"trusted {name.replace('_', '-')} executable not found ({location})", EXIT_PREFLIGHT)
    return None


def validate_git_helper_root(runner: CommandRunner, git_bin: str) -> dict[str, str]:
    result = runner.run([git_bin, "--exec-path"], timeout=30, phase="preflight", exit_code=EXIT_PREFLIGHT)
    try:
        helper_root = pathlib.Path(result.output.strip()).resolve(strict=True)
    except OSError as exc:
        raise BenchError("preflight", "Git helper directory is not resolvable", EXIT_PREFLIGHT) from exc
    if not helper_root.is_dir():
        raise BenchError("preflight", "Git helper path is not a directory", EXIT_PREFLIGHT)
    _reject_link_components(helper_root, phase="preflight", exit_code=EXIT_PREFLIGHT)
    helper_name = "git-remote-https.exe" if os.name == "nt" else "git-remote-https"
    try:
        helper = (helper_root / helper_name).resolve(strict=True)
    except OSError as exc:
        raise BenchError("preflight", "trusted Git HTTPS helper is unavailable", EXIT_PREFLIGHT) from exc
    if not helper.is_file():
        raise BenchError("preflight", "Git HTTPS helper is not a regular file", EXIT_PREFLIGHT)
    if os.name == "nt":
        install_root = pathlib.Path(git_bin).resolve().parents[1]
        if os.path.commonpath([str(helper), str(install_root)]).lower() != str(install_root).lower():
            raise BenchError("preflight", "Git HTTPS helper is outside the trusted Git installation", EXIT_PREFLIGHT)
    else:
        metadata = helper.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not os.access(helper, os.X_OK):
            raise BenchError("preflight", "Git HTTPS helper is not root-owned, executable, and non-writable", EXIT_PREFLIGHT)
    return {"exec_path": str(helper_root), "https_helper": str(helper), "https_helper_sha256": sha256_file(helper)}


def git_command(git_bin: str, hooks_dir: pathlib.Path, *args: str) -> list[str]:
    return [
        git_bin,
        "-c",
        f"core.hooksPath={hooks_dir}",
        "-c",
        "credential.helper=",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "filter.lfs.required=false",
        *args,
    ]


@dataclasses.dataclass
class GitSnapshot:
    spec: RepoSpec
    git_bin: str
    git_dir: pathlib.Path
    hooks_dir: pathlib.Path
    entries: Mapping[str, tuple[str, str]]
    runner: CommandRunner
    record: Mapping[str, Any]

    def git(self, *args: str, timeout: float = 120, check: bool = True) -> CommandResult:
        return self.runner.run(
            git_command(self.git_bin, self.hooks_dir, f"--git-dir={self.git_dir}", *args),
            timeout=timeout,
            check=check,
            phase="source",
            exit_code=EXIT_SOURCE,
        )

    def read_blob(self, relative: str, *, limit: int) -> bytes:
        entry = self.entries.get(relative)
        if entry is None:
            raise BenchError("source", f"{self.spec.repo}: missing required blob {relative}", EXIT_SOURCE)
        _, oid = entry
        size_text = self.git("cat-file", "-s", oid).output.strip()
        try:
            size = int(size_text)
        except ValueError as exc:
            raise BenchError("source", f"{self.spec.repo}: invalid blob size for {relative}", EXIT_SOURCE) from exc
        if size < 0 or size > limit:
            raise BenchError("source", f"{self.spec.repo}: blob {relative} exceeds {limit} bytes", EXIT_SOURCE)
        data = self.runner.run_bytes(
            git_command(self.git_bin, self.hooks_dir, f"--git-dir={self.git_dir}", "cat-file", "blob", oid),
            timeout=120,
            max_bytes=limit,
            phase="source",
            exit_code=EXIT_SOURCE,
        )
        if len(data) != size:
            raise BenchError("source", f"{self.spec.repo}: blob size changed for {relative}", EXIT_SOURCE)
        return data


def _safe_tree_path(raw: str) -> str:
    if not raw or raw.startswith("/") or "\\" in raw or "//" in raw:
        raise ValueError("absolute, empty, backslash, and repeated-separator paths are forbidden")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw):
        raise ValueError("control characters are forbidden")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("dot path segments are forbidden")
    normalized = unicodedata.normalize("NFC", raw)
    if normalized != raw:
        raise ValueError("non-canonical Unicode path")
    return normalized


def materialize_source(
    spec: RepoSpec,
    run_root: pathlib.Path,
    runner: CommandRunner,
    git_bin: str,
) -> GitSnapshot:
    phase = "source"
    sources = ensure_private_directory(run_root / "git-objects", phase=phase, exit_code=EXIT_SOURCE)
    hooks = ensure_private_directory(run_root / "empty-git-hooks", phase=phase, exit_code=EXIT_SOURCE)
    template = ensure_private_directory(run_root / "empty-git-template", phase=phase, exit_code=EXIT_SOURCE)
    if any(hooks.iterdir()) or any(template.iterdir()):
        raise BenchError(phase, "per-run Git hooks/template directories must be empty", EXIT_SOURCE)
    destination = sources / f"{spec.repo}.git"
    if destination.exists():
        raise BenchError(phase, f"fresh bare source already exists: {destination}", EXIT_SOURCE)
    runner.run(
        git_command(git_bin, hooks, "init", "--bare", f"--template={template}", str(destination)),
        timeout=120,
        phase=phase,
        exit_code=EXIT_SOURCE,
    )

    def git(*args: str, timeout: float = 120, check: bool = True) -> CommandResult:
        return runner.run(
            git_command(git_bin, hooks, f"--git-dir={destination}", *args),
            timeout=timeout,
            check=check,
            phase=phase,
            exit_code=EXIT_SOURCE,
        )

    git(
        "fetch",
        "--force",
        "--no-tags",
        "--filter=blob:none",
        spec.url,
        "refs/heads/main:refs/remotes/origin/main",
        timeout=300,
    )
    exists = git("cat-file", "-e", f"{spec.revision}^{{commit}}", check=False)
    if exists.returncode != 0:
        raise BenchError(phase, f"{spec.repo}: pinned revision is unavailable: {spec.revision}", EXIT_SOURCE)
    reachable = git("merge-base", "--is-ancestor", spec.revision, "refs/remotes/origin/main", check=False)
    if reachable.returncode != 0:
        raise BenchError(phase, f"{spec.repo}: pinned revision is no longer reachable from origin/main", EXIT_SOURCE)
    remote_main = git("rev-parse", "refs/remotes/origin/main").output.strip()
    actual = git("rev-parse", f"{spec.revision}^{{commit}}").output.strip()
    if not hmac.compare_digest(actual, spec.revision):
        raise BenchError(phase, f"{spec.repo}: resolved {actual}, expected {spec.revision}", EXIT_SOURCE)
    tree_bytes = runner.run_bytes(
        git_command(git_bin, hooks, f"--git-dir={destination}", "ls-tree", "-r", "-z", "--full-tree", spec.revision),
        timeout=120,
        max_bytes=MAX_TREE_BYTES,
        phase=phase,
        exit_code=EXIT_SOURCE,
    )
    entries: dict[str, tuple[str, str]] = {}
    normalized_seen: set[str] = set()
    for raw_entry in tree_bytes.split(b"\0"):
        if not raw_entry:
            continue
        try:
            metadata, encoded_path = raw_entry.split(b"\t", 1)
            mode, object_type, oid = metadata.decode("ascii").split(" ")
            relative = _safe_tree_path(encoded_path.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise BenchError(phase, f"{spec.repo}: unsafe or malformed Git tree entry", EXIT_SOURCE) from exc
        if mode not in {"100644", "100755"} or object_type != "blob" or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", oid):
            raise BenchError(phase, f"{spec.repo}: symlink, submodule, or unsupported object at {relative}", EXIT_SOURCE)
        collision_key = relative.casefold()
        if collision_key in normalized_seen:
            raise BenchError(phase, f"{spec.repo}: duplicate normalized path {relative}", EXIT_SOURCE)
        normalized_seen.add(collision_key)
        entries[relative] = (mode, oid)
    if len(entries) > MAX_TREE_ENTRIES:
        raise BenchError(phase, f"{spec.repo}: tree exceeds {MAX_TREE_ENTRIES} entries", EXIT_SOURCE)

    snapshot = GitSnapshot(spec, git_bin, destination, hooks, entries, runner, {})
    observed_hashes: dict[str, str] = {}
    for relative, expected in spec.runtime_hashes.items():
        actual_hash = sha256_bytes(snapshot.read_blob(relative, limit=MAX_JSON_BYTES))
        observed_hashes[relative] = actual_hash
        if not hmac.compare_digest(actual_hash, expected):
            raise BenchError(phase, f"{spec.repo}: audited runtime file drift: {relative}", EXIT_SOURCE)
    observed_tests: dict[str, str] = {}
    for relative, expected in spec.test_hashes.items():
        actual_hash = sha256_bytes(snapshot.read_blob(relative, limit=MAX_JSON_BYTES))
        observed_tests[relative] = actual_hash
        if not hmac.compare_digest(actual_hash, expected):
            raise BenchError(phase, f"{spec.repo}: reviewed test manifest drift: {relative}", EXIT_SOURCE)
    commit_bytes = runner.run_bytes(
        git_command(git_bin, hooks, f"--git-dir={destination}", "cat-file", "commit", spec.revision),
        timeout=120,
        max_bytes=MAX_JSON_BYTES,
        phase=phase,
        exit_code=EXIT_SOURCE,
    )
    signature = "PRESENT_UNVERIFIED" if b"\ngpgsig " in b"\n" + commit_bytes else "ABSENT"
    tree = git("rev-parse", f"{spec.revision}^{{tree}}").output.strip()
    committed_at = git("show", "-s", "--format=%cI", spec.revision).output.strip()
    record = {
        "path": str(destination),
        "repo": spec.repo,
        "url": spec.url,
        "revision": actual,
        "remote_main": remote_main,
        "pin_relation": "EQUAL" if hmac.compare_digest(remote_main, spec.revision) else "REVIEWED_ANCESTOR",
        "tree": tree,
        "commit_signature_status": signature,
        "commit_authenticity": "UNVERIFIED_UNSIGNED" if signature == "ABSENT" else "SIGNATURE_PRESENT_NOT_VERIFIED",
        "committed_at": committed_at,
        "runtime_file_sha256": observed_hashes,
        "reviewed_test_file_sha256": observed_tests,
        "source_tests": "NOT_EXECUTED_UNTRUSTED_SOURCE",
        "materialization": "FRESH_BARE_OBJECT_INSPECTION_NO_CHECKOUT",
    }
    snapshot.record = record
    return snapshot


def _validate_finite_json(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BenchError("receipt_verification", f"{path}: non-finite number", EXIT_RECEIPT)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BenchError("receipt_verification", f"{path}: non-string object key", EXIT_RECEIPT)
            _validate_finite_json(item, path=f"{path}.{key}")
        return
    raise BenchError("receipt_verification", f"{path}: unsupported JSON value {type(value).__name__}", EXIT_RECEIPT)


def _parse_utc_timestamp(value: Any, *, field: str) -> dt.datetime:
    if not isinstance(value, str) or len(value) > 64 or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise BenchError("receipt_verification", f"{field}: expected canonical UTC ISO-8601 ending in Z", EXIT_RECEIPT)
    candidate = value[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise BenchError("receipt_verification", f"{field}: invalid ISO-8601 timestamp", EXIT_RECEIPT) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise BenchError("receipt_verification", f"{field}: timestamp must be UTC", EXIT_RECEIPT)
    return parsed


def _receipt_digest(receipt: Mapping[str, Any]) -> str:
    body = {key: value for key, value in receipt.items() if key != "hash"}
    return sha256_bytes(canonical_json_bytes(body))


def _validate_plane_metrics(plane: str, metrics: Mapping[str, Any], *, source: str) -> None:
    contract = PLANE_METRIC_FIELDS[plane]
    missing = sorted(set(contract) - set(metrics))
    extra = sorted(set(metrics) - set(contract))
    if missing or extra:
        raise BenchError(
            "receipt_verification",
            f"{source}: {plane} metric fields mismatch; missing={missing} extra={extra}",
            EXIT_RECEIPT,
        )
    for key, rule in contract.items():
        value = metrics[key]
        if rule == "text":
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise BenchError("receipt_verification", f"{source}: metric {key} must be bounded nonempty text", EXIT_RECEIPT)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise BenchError("receipt_verification", f"{source}: metric {key} must be a finite number", EXIT_RECEIPT)
        number = float(value)
        if rule == "nonnegative" and number < 0:
            raise BenchError("receipt_verification", f"{source}: metric {key} must be nonnegative", EXIT_RECEIPT)
        if rule == "positive" and number <= 0:
            raise BenchError("receipt_verification", f"{source}: metric {key} must be positive", EXIT_RECEIPT)
        if rule == "unit" and not 0.0 <= number <= 1.0:
            raise BenchError("receipt_verification", f"{source}: metric {key} must be between 0 and 1", EXIT_RECEIPT)


def load_receipt_auth_key() -> bytearray | None:
    encoded = os.environ.pop(RECEIPT_KEY_ENV, "")
    if not encoded:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{64}", encoded):
        raise BenchError("receipt_auth", f"{RECEIPT_KEY_ENV} must contain exactly 32 bytes encoded as 64 hex characters", EXIT_RECEIPT)
    return bytearray.fromhex(encoded)


def _validate_digest_mapping(value: Any, *, source: str) -> None:
    if not isinstance(value, dict) or not value or len(value) > 64:
        raise BenchError("receipt_auth", f"{source}: artifacts must be a nonempty mapping with at most 64 entries", EXIT_RECEIPT)
    for name, digest in value.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", name):
            raise BenchError("receipt_auth", f"{source}: invalid artifact name", EXIT_RECEIPT)
        if not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
            raise BenchError("receipt_auth", f"{source}: artifact {name} lacks a SHA-256 digest", EXIT_RECEIPT)


def _validate_measured_auth(spec: RepoSpec, receipt: Mapping[str, Any], key: bytes | bytearray | None, *, source: str) -> None:
    if set(receipt) != MEASURED_RECEIPT_FIELDS:
        missing = sorted(MEASURED_RECEIPT_FIELDS - set(receipt))
        extra = sorted(set(receipt) - MEASURED_RECEIPT_FIELDS)
        raise BenchError("receipt_auth", f"{source}: measured envelope fields mismatch; missing={missing} extra={extra}", EXIT_RECEIPT)
    if receipt.get("schema_version") != "szl-bench-receipt/v3":
        raise BenchError("receipt_auth", f"{source}: unsupported measured receipt schema", EXIT_RECEIPT)
    if receipt.get("audience") != RECEIPT_AUDIENCE:
        raise BenchError("receipt_auth", f"{source}: measured receipt audience mismatch", EXIT_RECEIPT)
    if receipt.get("source_revision") != spec.revision:
        raise BenchError("receipt_auth", f"{source}: measured receipt source revision mismatch", EXIT_RECEIPT)
    workload = receipt.get("workload")
    required_workload = {"model_revision", "data_revision", "configuration_sha256"}
    if not isinstance(workload, dict) or set(workload) != required_workload:
        raise BenchError("receipt_auth", f"{source}: workload must contain exactly {sorted(required_workload)}", EXIT_RECEIPT)
    for field in ("model_revision", "data_revision"):
        value = workload.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise BenchError("receipt_auth", f"{source}: invalid workload {field}", EXIT_RECEIPT)
    if not isinstance(workload.get("configuration_sha256"), str) or not HASH_RE.fullmatch(workload["configuration_sha256"]):
        raise BenchError("receipt_auth", f"{source}: workload configuration_sha256 is invalid", EXIT_RECEIPT)
    _validate_digest_mapping(receipt.get("artifacts"), source=source)
    if not isinstance(receipt.get("hardware_evidence_sha256"), str) or not HASH_RE.fullmatch(receipt["hardware_evidence_sha256"]):
        raise BenchError("receipt_auth", f"{source}: hardware evidence digest is invalid", EXIT_RECEIPT)
    auth = receipt.get("auth")
    if not isinstance(auth, dict) or set(auth) != {"alg", "key_id", "mac"}:
        raise BenchError("receipt_auth", f"{source}: auth must contain exactly alg, key_id, and mac", EXIT_RECEIPT)
    if auth.get("alg") != "hmac-sha256" or auth.get("key_id") != RECEIPT_KEY_ID:
        raise BenchError("receipt_auth", f"{source}: untrusted receipt authentication key or algorithm", EXIT_RECEIPT)
    mac = auth.get("mac")
    if not isinstance(mac, str) or not HASH_RE.fullmatch(mac):
        raise BenchError("receipt_auth", f"{source}: malformed receipt MAC", EXIT_RECEIPT)
    if key is None:
        raise BenchError("receipt_auth", f"{source}: MEASURED receipt requires {RECEIPT_KEY_ENV}", EXIT_RECEIPT)
    signed_body = {field: value for field, value in receipt.items() if field not in {"hash", "auth"}}
    signed_body["auth"] = {"alg": auth["alg"], "key_id": auth["key_id"]}
    expected = hmac.new(key, RECEIPT_DOMAIN + canonical_json_bytes(signed_body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise BenchError("receipt_auth", f"{source}: measured receipt MAC verification failed", EXIT_RECEIPT)


def _order_receipt_inputs(inputs: Sequence[tuple[str, str, bytes]], *, repo: str) -> list[tuple[str, str, bytes]]:
    numbered: list[tuple[int, tuple[str, str, bytes]]] = []
    for item in inputs:
        name = item[0]
        if name == "000-genesis.json":
            sequence = 0
        else:
            match = RECEIPT_NAME_RE.fullmatch(name)
            if not match:
                raise BenchError("receipt_verification", f"{repo}: non-canonical receipt filename: {name}", EXIT_RECEIPT)
            sequence = int(match.group(1))
        numbered.append((sequence, item))
    numbered.sort(key=lambda entry: entry[0])
    observed = [sequence for sequence, _ in numbered]
    expected = list(range(len(numbered)))
    if observed != expected:
        raise BenchError(
            "receipt_verification",
            f"{repo}: receipt sequence prefixes must be unique and consecutive from 000",
            EXIT_RECEIPT,
            detail={"observed_prefixes": observed[: MAX_RECEIPTS + 1], "expected_count": len(numbered)},
        )
    return [item for _, item in numbered]


def _receipt_inputs(spec: RepoSpec, source: pathlib.Path | GitSnapshot) -> list[tuple[str, str, bytes]]:
    records: list[tuple[str, str, bytes]] = []
    total_bytes = 0
    if isinstance(source, GitSnapshot):
        receipt_entries = sorted(name for name in source.entries if name.startswith("receipts/"))
        if len(receipt_entries) > MAX_RECEIPTS:
            raise BenchError("receipt_verification", f"{spec.repo}: receipt count exceeds {MAX_RECEIPTS}", EXIT_RECEIPT)
        unexpected = [name for name in receipt_entries if name.count("/") != 1 or not name.endswith(".json")]
        if unexpected:
            raise BenchError("receipt_verification", f"{spec.repo}: unexpected receipt-tree entries", EXIT_RECEIPT, detail=unexpected)
        for relative in receipt_entries:
            data = source.read_blob(relative, limit=MAX_JSON_BYTES)
            total_bytes += len(data)
            if total_bytes > MAX_RECEIPT_BYTES_TOTAL:
                raise BenchError("receipt_verification", f"{spec.repo}: aggregate receipt bytes exceed {MAX_RECEIPT_BYTES_TOTAL}", EXIT_RECEIPT)
            records.append((relative.rsplit("/", 1)[1], f"{spec.repo}@{spec.revision}:{relative}", data))
    else:
        directory = source / "receipts"
        if not directory.is_dir() or _is_reparse_or_symlink(directory):
            raise BenchError("receipt_verification", f"{spec.repo}: missing safe receipts directory", EXIT_RECEIPT)
        all_entries = sorted(directory.iterdir(), key=lambda path: path.name)
        if len(all_entries) > MAX_RECEIPTS:
            raise BenchError("receipt_verification", f"{spec.repo}: receipt count exceeds {MAX_RECEIPTS}", EXIT_RECEIPT)
        unexpected = [entry.name for entry in all_entries if not entry.name.endswith(".json")]
        if unexpected:
            raise BenchError("receipt_verification", f"{spec.repo}: unexpected receipt-directory entries", EXIT_RECEIPT, detail=unexpected)
        for path in all_entries:
            data = read_bounded_regular_file(path, limit=MAX_JSON_BYTES, phase="receipt_verification", exit_code=EXIT_RECEIPT)
            total_bytes += len(data)
            if total_bytes > MAX_RECEIPT_BYTES_TOTAL:
                raise BenchError("receipt_verification", f"{spec.repo}: aggregate receipt bytes exceed {MAX_RECEIPT_BYTES_TOTAL}", EXIT_RECEIPT)
            records.append((path.name, str(path), data))
    return _order_receipt_inputs(records, repo=spec.repo)


def verify_receipts(spec: RepoSpec, source: pathlib.Path | GitSnapshot, receipt_key: bytes | bytearray | None = None) -> dict[str, Any]:
    inputs = _receipt_inputs(spec, source)
    names = [name for name, _, _ in inputs]
    if not names or names[0] != "000-genesis.json":
        raise BenchError("receipt_verification", f"{spec.repo}: 000-genesis.json must be first", EXIT_RECEIPT)
    if len(inputs) > MAX_RECEIPTS:
        raise BenchError("receipt_verification", f"{spec.repo}: receipt count exceeds {MAX_RECEIPTS}", EXIT_RECEIPT)
    previous = ZERO_HASH
    measured: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    latest_time: dt.datetime | None = None
    previous_time: dt.datetime | None = None
    seen_hashes: set[str] = set()
    for index, (name, label, data) in enumerate(inputs):
        receipt = strict_json_from_bytes(data, source=label)
        if not isinstance(receipt, dict):
            raise BenchError("receipt_verification", f"{label}: receipt must be an object", EXIT_RECEIPT)
        _validate_finite_json(receipt)
        missing = sorted(REQUIRED_RECEIPT_FIELDS - set(receipt))
        if missing:
            raise BenchError("receipt_verification", f"{label}: missing fields {missing}", EXIT_RECEIPT)
        if receipt.get("status") == "MEASURED":
            _validate_measured_auth(spec, receipt, receipt_key, source=label)
        elif set(receipt) != REQUIRED_RECEIPT_FIELDS:
            raise BenchError("receipt_verification", f"{label}: unsigned non-measured receipt has unexpected fields", EXIT_RECEIPT)
        if receipt["plane"] != spec.plane:
            raise BenchError("receipt_verification", f"{label}: plane {receipt['plane']!r} does not match {spec.plane!r}", EXIT_RECEIPT)
        if receipt["status"] not in RECEIPT_STATUSES:
            raise BenchError("receipt_verification", f"{label}: unsupported status {receipt['status']!r}", EXIT_RECEIPT)
        if not isinstance(receipt["method"], str) or not receipt["method"].strip() or len(receipt["method"]) > 1024:
            raise BenchError("receipt_verification", f"{label}: invalid method", EXIT_RECEIPT)
        if not isinstance(receipt["metrics"], dict):
            raise BenchError("receipt_verification", f"{label}: metrics must be an object", EXIT_RECEIPT)
        if receipt["status"] == "MEASURED" and not receipt["metrics"]:
            raise BenchError("receipt_verification", f"{label}: MEASURED receipt has no metrics", EXIT_RECEIPT)
        if receipt["status"] != "MEASURED" and receipt["metrics"]:
            raise BenchError("receipt_verification", f"{label}: non-MEASURED receipt contains metrics", EXIT_RECEIPT)
        if receipt["status"] == "MEASURED":
            _validate_plane_metrics(spec.plane, receipt["metrics"], source=label)
        machine = receipt["machine"]
        if not isinstance(machine, dict) or set(machine) != set(EXPECTED_MACHINE["receipt"]):
            raise BenchError("receipt_verification", f"{label}: machine must contain exactly cpu, ram_gb, and gpu", EXIT_RECEIPT)
        if any(machine.get(key) != value for key, value in EXPECTED_MACHINE["receipt"].items()):
            raise BenchError("receipt_verification", f"{label}: machine does not match the pinned bench-node receipt identity", EXIT_RECEIPT)
        measured_at = _parse_utc_timestamp(receipt["measured_at"], field=f"{label}.measured_at")
        if previous_time is not None and measured_at < previous_time:
            raise BenchError("receipt_verification", f"{label}: receipt timestamp moves backwards", EXIT_RECEIPT)
        if measured_at > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
            raise BenchError("receipt_verification", f"{label}: receipt timestamp is unreasonably in the future", EXIT_RECEIPT)
        previous_time = measured_at
        latest_time = measured_at if latest_time is None or measured_at > latest_time else latest_time
        declared = receipt["hash"]
        if not isinstance(declared, str) or not HASH_RE.fullmatch(declared):
            raise BenchError("receipt_verification", f"{label}: invalid declared hash", EXIT_RECEIPT)
        recomputed = _receipt_digest(receipt)
        if not hmac.compare_digest(recomputed, declared):
            raise BenchError("receipt_verification", f"{label}: canonical hash mismatch", EXIT_RECEIPT)
        if declared in seen_hashes:
            raise BenchError("receipt_verification", f"{label}: duplicate receipt hash", EXIT_RECEIPT)
        seen_hashes.add(declared)
        if receipt["prev_hash"] != previous:
            raise BenchError("receipt_verification", f"{label}: broken prev_hash link", EXIT_RECEIPT)
        if index == 0:
            if receipt["status"] != "BLOCKED" or receipt["metrics"] or receipt["prev_hash"] != ZERO_HASH:
                raise BenchError("receipt_verification", f"{label}: invalid BLOCKED genesis semantics", EXIT_RECEIPT)
            if not hmac.compare_digest(declared, spec.genesis):
                raise BenchError("receipt_verification", f"{label}: genesis trust-root mismatch", EXIT_RECEIPT)
        inventory.append({"file": name, "hash": declared, "status": receipt["status"]})
        if receipt["status"] == "MEASURED":
            measured.append(
                {
                    "plane": spec.plane,
                    "machine": machine,
                    "measured_at": receipt["measured_at"],
                    "method": receipt["method"],
                    "metrics": receipt["metrics"],
                    "receipt": declared,
                    "source_revision": spec.revision,
                    "workload": receipt["workload"],
                    "artifacts": receipt["artifacts"],
                    "hardware_evidence_sha256": receipt["hardware_evidence_sha256"],
                    "receipt_auth": {"alg": receipt["auth"]["alg"], "key_id": receipt["auth"]["key_id"]},
                }
            )
        previous = declared
    return {
        "repo": spec.repo,
        "plane": spec.plane,
        "receipt_count": len(inputs),
        "measured_count": len(measured),
        "genesis": spec.genesis,
        "chain_head": previous,
        "latest_receipt_at": latest_time.isoformat().replace("+00:00", "Z") if latest_time else None,
        "integrity": "VERIFIED_CHAIN_AND_HMAC_MEASUREMENTS" if measured else "VERIFIED_UNSIGNED_EMPTY_CHAIN",
        "inventory": inventory,
        "results": measured,
    }


def assemble_payload(receipt_sets: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    order = {spec.plane: index for index, spec in enumerate(REPOS)}
    merged: list[dict[str, Any]] = []
    seen_receipts: set[str] = set()
    latest: list[dt.datetime] = []
    sources: dict[str, Any] = {}
    per_plane: dict[str, dict[str, Any]] = {}
    for item in receipt_sets:
        plane = str(item["plane"])
        latest_value = item.get("latest_receipt_at")
        if not isinstance(latest_value, str):
            raise BenchError("result_assembly", f"{plane}: missing latest receipt timestamp", EXIT_RESULT)
        latest.append(_parse_utc_timestamp(latest_value, field=f"{plane}.latest_receipt_at"))
        sources[plane] = {
            "repo": item["repo"],
            "revision": next(spec.revision for spec in REPOS if spec.plane == plane),
            "genesis": item["genesis"],
            "receipt_count": item["receipt_count"],
            "receipt_head": item["chain_head"],
            "integrity": item["integrity"],
        }
        for row in item["results"]:
            receipt_hash = row["receipt"]
            if receipt_hash in seen_receipts:
                raise BenchError("result_assembly", f"duplicate exported receipt across planes: {receipt_hash}", EXIT_RESULT)
            seen_receipts.add(receipt_hash)
            merged.append(dict(row))
    merged.sort(key=lambda row: (order[row["plane"]], row["measured_at"], row["receipt"]))
    result_digest = sha256_bytes(canonical_json_bytes(merged))
    generated_at = max(latest).isoformat().replace("+00:00", "Z") if latest else utc_now()
    common = {
        "schema_version": "szl-bench-results/v2",
        "generated_at": generated_at,
        "data_state": "MEASURED" if merged else "EMPTY_HONEST",
        "count": len(merged),
        "results_sha256": result_digest,
        "sources": sources,
        "results": merged,
    }
    for spec in REPOS:
        rows = [row for row in merged if row["plane"] == spec.plane]
        per_plane[spec.plane] = {
            "schema_version": common["schema_version"],
            "generated_at": generated_at,
            "data_state": "MEASURED" if rows else "EMPTY_HONEST",
            "count": len(rows),
            "results_sha256": sha256_bytes(canonical_json_bytes(rows)),
            "source": sources[spec.plane],
            "results": rows,
        }
    return common, per_plane


def _windows_cpu() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except Exception:
        return ""


def _physical_memory_gib() -> float | None:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys / (1024**3)
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
    except (AttributeError, OSError, ValueError):
        return None


def probe_machine(runner: CommandRunner, nvidia_smi: str | None) -> dict[str, Any]:
    cpu = _windows_cpu() if os.name == "nt" else ""
    if not cpu and pathlib.Path("/proc/cpuinfo").is_file():
        for line in pathlib.Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name") and ":" in line:
                cpu = line.split(":", 1)[1].strip()
                break
    cpu = cpu or platform.processor() or "UNKNOWN"
    gpus: list[dict[str, Any]] = []
    if nvidia_smi:
        query = runner.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,uuid,driver_version",
                "--format=csv,noheader,nounits",
            ],
            timeout=30,
            check=False,
            phase="machine",
            exit_code=EXIT_PREFLIGHT,
        )
        if query.returncode == 0:
            for line in query.output.splitlines():
                fields = [field.strip() for field in line.split(",", 3)]
                if len(fields) == 4:
                    try:
                        memory_mib = float(fields[1])
                    except ValueError:
                        memory_mib = None
                    gpus.append({"name": fields[0], "memory_mib": memory_mib, "uuid": fields[2], "driver": fields[3]})
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu": cpu,
        "ram_gib": round(_physical_memory_gib() or 0.0, 2),
        "gpus": gpus,
    }


def qualify_machine(machine: Mapping[str, Any]) -> None:
    problems: list[str] = []
    if EXPECTED_MACHINE["cpu_contains"].lower() not in str(machine.get("cpu", "")).lower():
        problems.append(f"CPU does not contain {EXPECTED_MACHINE['cpu_contains']!r}")
    if float(machine.get("ram_gib") or 0.0) < float(EXPECTED_MACHINE["ram_min_gib"]):
        problems.append(f"RAM is below {EXPECTED_MACHINE['ram_min_gib']} GiB")
    matching = [
        gpu
        for gpu in machine.get("gpus", [])
        if EXPECTED_MACHINE["gpu_contains"].lower() in str(gpu.get("name", "")).lower()
        and float(gpu.get("memory_mib") or 0.0) >= float(EXPECTED_MACHINE["gpu_min_mib"])
    ]
    if not matching:
        problems.append(
            f"no GPU matches {EXPECTED_MACHINE['gpu_contains']!r} with at least {EXPECTED_MACHINE['gpu_min_mib']} MiB"
        )
    if problems:
        raise BenchError("machine", "bench node is not qualified: " + "; ".join(problems), EXIT_PREFLIGHT)


class DockerClient:
    def __init__(self, runner: CommandRunner, executable: str, config_dir: pathlib.Path):
        self.runner = runner
        self.executable = executable
        self.config_dir = ensure_private_directory(config_dir, phase="docker_preflight", exit_code=EXIT_PREFLIGHT)
        config = self.config_dir / "config.json"
        if not config.exists():
            atomic_write(config, b"{}\n")
        self.prefix = [self.executable, "--config", str(self.config_dir), "--host", "unix:///var/run/docker.sock"]

    def run(self, *args: str, **kwargs: Any) -> CommandResult:
        return self.runner.run([*self.prefix, *args], **kwargs)


def docker_preflight(client: DockerClient) -> dict[str, Any]:
    socket_path = pathlib.Path("/var/run/docker.sock")
    try:
        metadata = socket_path.lstat()
    except OSError as exc:
        raise BenchError("docker_preflight", "local Docker socket /var/run/docker.sock is unavailable", EXIT_PREFLIGHT) from exc
    if _is_reparse_or_symlink(socket_path) or not stat.S_ISSOCK(metadata.st_mode):
        raise BenchError("docker_preflight", "Docker endpoint must be the local non-link Unix socket /var/run/docker.sock", EXIT_PREFLIGHT)
    if metadata.st_uid != 0 or metadata.st_mode & 0o002:
        raise BenchError("docker_preflight", "Docker socket must be root-owned and not world-writable", EXIT_PREFLIGHT)
    result = client.run(
        "version", "--format", "{{json .Server}}",
        timeout=30,
        phase="docker_preflight",
        exit_code=EXIT_PREFLIGHT,
    )
    try:
        server = json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise BenchError("docker_preflight", "Docker daemon returned invalid version metadata", EXIT_PREFLIGHT) from exc
    if not isinstance(server, dict) or not server.get("Version"):
        raise BenchError("docker_preflight", "Docker daemon is unavailable", EXIT_PREFLIGHT)
    if server.get("Os") != "linux":
        raise BenchError("docker_preflight", "Docker server must report Linux containers", EXIT_PREFLIGHT)
    info_result = client.run("info", "--format", "{{json .}}", timeout=30, phase="docker_preflight", exit_code=EXIT_PREFLIGHT)
    try:
        info = json.loads(info_result.output)
    except json.JSONDecodeError as exc:
        raise BenchError("docker_preflight", "Docker daemon returned invalid info metadata", EXIT_PREFLIGHT) from exc
    if not isinstance(info, dict) or not info.get("ID") or info.get("OSType") != "linux":
        raise BenchError("docker_preflight", "Docker daemon identity is incomplete or not Linux", EXIT_PREFLIGHT)
    if str(info.get("Architecture", "")).lower() not in {"x86_64", "amd64"}:
        raise BenchError("docker_preflight", "Docker server architecture must be amd64/x86_64 for the pinned bench node", EXIT_PREFLIGHT)
    return {
        "endpoint": "unix:///var/run/docker.sock",
        "server_id": info.get("ID"),
        "server_name": info.get("Name"),
        "server_version": server.get("Version"),
        "api_version": server.get("ApiVersion"),
        "os": server.get("Os"),
        "architecture": info.get("Architecture"),
        "docker_root_dir": info.get("DockerRootDir"),
        "security_options": info.get("SecurityOptions"),
    }


def prepare_build_context(
    spec: RepoSpec,
    generation: pathlib.Path,
    payload: Mapping[str, Any],
    run_id: str,
) -> pathlib.Path:
    context = generation / "build-contexts" / spec.repo
    if context.exists():
        raise BenchError("build", f"build context unexpectedly exists: {context}", EXIT_BUILD)
    context.mkdir(parents=True)
    dockerfile = (
        f"FROM {RUNTIME_BASE_IMAGE}\n"
        "WORKDIR /srv\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        "COPY server.py results.json identity.json ./\n"
        "EXPOSE 7860\n"
        "USER 65532:65532\n"
        'ENTRYPOINT ["/usr/local/bin/python","-I","-B","/srv/server.py"]\n'
    ).encode("utf-8")
    identity = {
        "schema_version": "szl-bench-service/v2",
        "controller_version": VERSION,
        "run_id": run_id.lower(),
        "plane": spec.plane,
        "source_revision": spec.revision,
        "results_sha256": payload["results_sha256"],
    }
    atomic_write(context / "Dockerfile", dockerfile, mode=0o644)
    atomic_write(context / "server.py", SERVER_SOURCE.encode("utf-8"), mode=0o644)
    atomic_write(context / "results.json", pretty_json_bytes(payload), mode=0o644)
    atomic_write(context / "identity.json", pretty_json_bytes(identity), mode=0o644)
    return context


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def http_get_bytes(url: str, *, timeout: float, max_bytes: int, expect_json: bool = False) -> tuple[bytes, Mapping[str, str]]:
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, headers={"Accept": "application/json" if expect_json else "*/*", "User-Agent": f"finish-bench-plane/{VERSION}"})
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise BenchError("runtime_witness", f"{url}: HTTP {response.status}", EXIT_RUNTIME)
            content_type = response.headers.get("Content-Type", "")
            if expect_json and "application/json" not in content_type.lower():
                raise BenchError("runtime_witness", f"{url}: expected JSON content type, got {content_type!r}", EXIT_RUNTIME)
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise BenchError("runtime_witness", f"{url}: response exceeds {max_bytes} bytes", EXIT_RUNTIME)
            return body, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raise BenchError("runtime_witness", f"{url}: HTTP {exc.code}; redirects are forbidden", EXIT_RUNTIME) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BenchError("runtime_witness", f"{url}: request failed: {exc}", EXIT_RUNTIME) from exc


def validate_service(
    base_url: str,
    spec: RepoSpec,
    expected: Mapping[str, Any],
    *,
    run_id: str,
    timeout: float,
) -> dict[str, Any]:
    health_bytes, _ = http_get_bytes(f"{base_url}/healthz", timeout=timeout, max_bytes=65_536, expect_json=True)
    ready_bytes, _ = http_get_bytes(f"{base_url}/readyz", timeout=timeout, max_bytes=65_536, expect_json=True)
    results_bytes, _ = http_get_bytes(f"{base_url}/api/results", timeout=timeout, max_bytes=MAX_HTTP_BYTES, expect_json=True)
    try:
        health = json.loads(health_bytes, parse_constant=_reject_constant)
        ready = json.loads(ready_bytes, parse_constant=_reject_constant)
        results = json.loads(results_bytes, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise BenchError("runtime_witness", f"{spec.repo}: invalid service JSON", EXIT_RUNTIME) from exc
    if health.get("status") != "ok" or health.get("plane") != spec.plane or health.get("run_id") != run_id.lower():
        raise BenchError("runtime_witness", f"{spec.repo}: health identity mismatch", EXIT_RUNTIME)
    ready_checks = {
        "status": ready.get("status") == "ready",
        "schema": ready.get("schema_version") == "szl-bench-service/v2",
        "run_id": ready.get("run_id") == run_id.lower(),
        "plane": ready.get("plane") == spec.plane,
        "source_revision": ready.get("source_revision") == spec.revision,
        "results_sha256": ready.get("results_sha256") == expected["results_sha256"],
    }
    failed_ready = [name for name, passed in ready_checks.items() if not passed]
    if failed_ready:
        raise BenchError("runtime_witness", f"{spec.repo}: readiness identity mismatch: {', '.join(failed_ready)}", EXIT_RUNTIME)
    expected_rows = expected["results"]
    expected_state = "MEASURED" if expected_rows else "EMPTY_HONEST"
    checks = {
        "plane": results.get("plane") == spec.plane,
        "state": results.get("state") == expected_state,
        "generated_at": results.get("generated_at") == expected["generated_at"],
        "count": results.get("count") == len(expected_rows),
        "results": results.get("results") == expected_rows,
        "run_id": results.get("run_id") == run_id.lower(),
        "source_revision": results.get("source_revision") == spec.revision,
        "results_sha256": results.get("results_sha256") == expected["results_sha256"],
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise BenchError("runtime_witness", f"{spec.repo}: API payload mismatch: {', '.join(failed)}", EXIT_RUNTIME)
    return {
        "url": base_url,
        "plane": spec.plane,
        "data_state": expected_state,
        "count": len(expected_rows),
        "results_sha256": expected["results_sha256"],
        "witnessed_at": utc_now(),
    }


def wait_for_service(
    base_url: str,
    spec: RepoSpec,
    expected: Mapping[str, Any],
    *,
    run_id: str,
    deadline_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + deadline_seconds
    delay = 0.5
    last_error: BenchError | None = None
    while time.monotonic() < deadline:
        try:
            return validate_service(
                base_url,
                spec,
                expected,
                run_id=run_id,
                timeout=min(5.0, max(1.0, deadline - time.monotonic())),
            )
        except BenchError as exc:
            last_error = exc
        time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        delay = min(delay * 1.7, 5.0)
    raise BenchError("runtime_witness", f"{spec.repo}: service did not become ready: {last_error}", EXIT_RUNTIME)


class DockerDeployer:
    def __init__(self, client: DockerClient, run_id: str, http_deadline: float):
        self.client = client
        self.run_id = run_id.lower()
        self.http_deadline = http_deadline
        self.images: dict[str, dict[str, str]] = {}
        self.candidates: list[str] = []

    def docker(self, *args: str, timeout: float = 120, check: bool = True, phase: str = "runtime", exit_code: int = EXIT_RUNTIME) -> CommandResult:
        return self.client.run(*args, timeout=timeout, check=check, phase=phase, exit_code=exit_code)

    def build(self, spec: RepoSpec, context: pathlib.Path, results_hash: str) -> dict[str, str]:
        tag = f"szl/{spec.repo}-api:bench-{spec.revision[:12]}-{results_hash[:12]}"
        self.docker(
            "build",
            "--pull",
            "--network=none",
            "--provenance=true",
            "--sbom=true",
            "--label",
            f"io.szl.managed-by={MANAGED_BY}",
            "--label",
            f"io.szl.plane={spec.plane}",
            "--label",
            f"io.szl.source-revision={spec.revision}",
            "--label",
            f"io.szl.results-sha256={results_hash}",
            "--tag",
            tag,
            str(context),
            timeout=1200,
            phase="build",
            exit_code=EXIT_BUILD,
        )
        image_id = self.docker("image", "inspect", "--format", "{{.Id}}", tag, phase="build", exit_code=EXIT_BUILD).output.strip()
        if not image_id.startswith("sha256:"):
            raise BenchError("build", f"{spec.repo}: Docker did not return an immutable image ID", EXIT_BUILD)
        record = {
            "tag": tag,
            "image_id": image_id,
            "base_image": RUNTIME_BASE_IMAGE,
            "server_sha256": sha256_bytes(SERVER_SOURCE.encode("utf-8")),
            "results_sha256": results_hash,
        }
        self.images[spec.plane] = record
        return record

    def _run_args(self, spec: RepoSpec, name: str, host_port: str, *, restart: str) -> list[str]:
        image = self.images[spec.plane]["image_id"]
        return [
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            f"io.szl.managed-by={MANAGED_BY}",
            "--label",
            f"io.szl.run-id={self.run_id}",
            "--label",
            f"io.szl.plane={spec.plane}",
            "--label",
            f"io.szl.source-revision={spec.revision}",
            "--label",
            f"io.szl.results-sha256={self.images[spec.plane]['results_sha256']}",
            "--read-only",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=1m",
            "--log-opt",
            "max-file=2",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            "256",
            "--memory",
            "512m",
            "--cpus",
            "2",
            "--user",
            "65532:65532",
            "--restart",
            restart,
            "--publish",
            f"127.0.0.1:{host_port}:7860",
            image,
        ]

    def _verify_container(self, spec: RepoSpec, inspected: Mapping[str, Any], *, restart: str) -> None:
        expected_image = self.images[spec.plane]["image_id"]
        labels = inspected.get("Config", {}).get("Labels") or {}
        host = inspected.get("HostConfig") or {}
        bindings = (host.get("PortBindings") or {}).get("7860/tcp") or []
        mounts = inspected.get("Mounts")
        log_config = host.get("LogConfig") or {}
        problems: list[str] = []
        if inspected.get("Image") != expected_image:
            problems.append("immutable image ID")
        expected_labels = {
            "io.szl.managed-by": MANAGED_BY,
            "io.szl.run-id": self.run_id,
            "io.szl.plane": spec.plane,
            "io.szl.source-revision": spec.revision,
            "io.szl.results-sha256": self.images[spec.plane]["results_sha256"],
        }
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            problems.append("managed labels")
        if host.get("Privileged") is not False or host.get("ReadonlyRootfs") is not True:
            problems.append("privilege/read-only policy")
        if inspected.get("Config", {}).get("User") != "65532:65532":
            problems.append("non-root user")
        if "ALL" not in (host.get("CapDrop") or []):
            problems.append("capability drop")
        if "no-new-privileges=true" not in (host.get("SecurityOpt") or []):
            problems.append("no-new-privileges")
        if (host.get("RestartPolicy") or {}).get("Name") != restart:
            problems.append("restart policy")
        if host.get("Memory") != 512 * 1024 * 1024 or host.get("NanoCpus") != 2_000_000_000 or host.get("PidsLimit") != 256:
            problems.append("resource limits")
        if log_config.get("Type") != "local" or log_config.get("Config") != {"max-size": "1m", "max-file": "2"}:
            problems.append("bounded log policy")
        if host.get("Tmpfs") != {"/tmp": "rw,noexec,nosuid,size=64m"}:
            problems.append("tmpfs policy")
        if any(host.get(key) for key in ("Binds", "Mounts", "VolumesFrom")):
            problems.append("unexpected host mount configuration")
        if len(bindings) != 1 or bindings[0].get("HostIp") != "127.0.0.1":
            problems.append("loopback port binding")
        # Docker's legacy --tmpfs API records the mount in HostConfig.Tmpfs;
        # versions differ on whether top-level Mounts repeats that entry.
        if not isinstance(mounts, list) or len(mounts) > 1:
            problems.append("mount inventory")
        elif mounts:
            tmp_mount = mounts[0]
            if (
                not isinstance(tmp_mount, dict)
                or tmp_mount.get("Type") != "tmpfs"
                or tmp_mount.get("Source") not in {"", None}
                or tmp_mount.get("Destination") != "/tmp"
                or tmp_mount.get("RW") is not True
            ):
                problems.append("unexpected bind/volume/mount")
        if problems:
            raise BenchError("runtime", f"{spec.repo}: container policy mismatch: {', '.join(problems)}", EXIT_RUNTIME)

    def _container(self, name: str) -> dict[str, Any] | None:
        result = self.docker("container", "inspect", name, check=False)
        if result.returncode != 0:
            return None
        try:
            value = json.loads(result.output)
        except json.JSONDecodeError as exc:
            raise BenchError("runtime", f"invalid Docker inspect output for {name}", EXIT_RUNTIME) from exc
        return value[0] if isinstance(value, list) and value else None

    def _assigned_port(self, name: str) -> int:
        output = self.docker("port", name, "7860/tcp").output
        for line in output.splitlines():
            if line.startswith("127.0.0.1:"):
                try:
                    return int(line.rsplit(":", 1)[1])
                except ValueError:
                    pass
        raise BenchError("runtime", f"{name}: no loopback port binding found", EXIT_RUNTIME)

    def stage(self, spec: RepoSpec, expected: Mapping[str, Any]) -> dict[str, Any]:
        name = f"szl-bench-{spec.plane}-candidate-{self.run_id[-8:]}"
        if self._container(name):
            raise BenchError("runtime", f"candidate container already exists: {name}", EXIT_RUNTIME)
        result = self.docker(*self._run_args(spec, name, "", restart="no"))
        container_id = result.output.strip()
        self.candidates.append(name)
        port = self._assigned_port(name)
        witness = wait_for_service(
            f"http://127.0.0.1:{port}",
            spec,
            expected,
            run_id=self.run_id,
            deadline_seconds=self.http_deadline,
        )
        inspected = self._container(name)
        if not inspected or inspected.get("Id") != container_id:
            raise BenchError("runtime", f"{spec.repo}: candidate container identity mismatch", EXIT_RUNTIME)
        self._verify_container(spec, inspected, restart="no")
        witness.update({"container_id": container_id, "image_id": inspected.get("Image")})
        return witness

    def cleanup_candidates(self) -> None:
        for name in reversed(self.candidates):
            self.docker("rm", "--force", name, check=False)
        self.candidates.clear()

    def _restore_cutover(self, backups: Mapping[str, Mapping[str, Any]], created: Sequence[str]) -> list[str]:
        rollback_errors: list[str] = []

        def rollback_docker(label: str, *args: str) -> CommandResult | None:
            try:
                result = self.docker(*args, check=False)
            except BaseException as exc:
                rollback_errors.append(f"{label}: {redact(exc)}")
                return None
            if result.returncode != 0:
                rollback_errors.append(label)
                return None
            return result

        for name in reversed(created):
            rollback_docker(f"could not remove {name}", "rm", "--force", name)
        for spec in REPOS:
            backup = backups.get(spec.plane)
            if not backup:
                continue
            final_name = f"szl-bench-{spec.plane}"
            if rollback_docker(f"could not restore name {final_name}", "rename", str(backup["name"]), final_name) is None:
                continue
            if backup["was_running"] and rollback_docker(f"could not restart {final_name}", "start", final_name) is None:
                continue
            try:
                restored = self._container(final_name)
            except BaseException as exc:
                rollback_errors.append(f"could not inspect restored {final_name}: {redact(exc)}")
                continue
            if not restored or restored.get("Id") != backup["container_id"]:
                rollback_errors.append(f"restored identity mismatch for {final_name}")
            elif bool(restored.get("State", {}).get("Running")) != bool(backup["was_running"]):
                rollback_errors.append(f"restored running state mismatch for {final_name}")
        return rollback_errors

    def cutover(self, expected_by_plane: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        backups: dict[str, dict[str, Any]] = {}
        created: list[str] = []
        try:
            for spec in REPOS:
                final_name = f"szl-bench-{spec.plane}"
                old = self._container(final_name)
                if old:
                    labels = old.get("Config", {}).get("Labels") or {}
                    if labels.get("io.szl.managed-by") != MANAGED_BY:
                        raise BenchError("cutover", f"refusing to replace unmanaged container {final_name}", EXIT_CUTOVER)
                    backup_name = f"{final_name}-backup-{self.run_id[-8:]}"
                    if self._container(backup_name):
                        raise BenchError("cutover", f"backup name already exists: {backup_name}", EXIT_CUTOVER)
                    was_running = bool(old.get("State", {}).get("Running"))
                    self.docker("rename", final_name, backup_name, phase="cutover", exit_code=EXIT_CUTOVER)
                    backups[spec.plane] = {"name": backup_name, "was_running": was_running, "container_id": old.get("Id")}
                    if was_running:
                        self.docker("stop", "--time", "15", backup_name, phase="cutover", exit_code=EXIT_CUTOVER)
            for spec in REPOS:
                final_name = f"szl-bench-{spec.plane}"
                result = self.docker(
                    *self._run_args(spec, final_name, str(spec.port), restart="unless-stopped"),
                    phase="cutover",
                    exit_code=EXIT_CUTOVER,
                )
                created.append(final_name)
                inspected = self._container(final_name)
                if not inspected or inspected.get("Id") != result.output.strip():
                    raise BenchError("cutover", f"{spec.repo}: final container identity mismatch", EXIT_CUTOVER)
                self._verify_container(spec, inspected, restart="unless-stopped")
            first: dict[str, Any] = {}
            for spec in REPOS:
                first[spec.plane] = wait_for_service(
                    f"http://127.0.0.1:{spec.port}",
                    spec,
                    expected_by_plane[spec.plane],
                    run_id=self.run_id,
                    deadline_seconds=self.http_deadline,
                )
            for spec in REPOS:
                self.docker("restart", "--time", "15", f"szl-bench-{spec.plane}", phase="cutover", exit_code=EXIT_CUTOVER)
            after_restart: dict[str, Any] = {}
            for spec in REPOS:
                after_restart[spec.plane] = wait_for_service(
                    f"http://127.0.0.1:{spec.port}",
                    spec,
                    expected_by_plane[spec.plane],
                    run_id=self.run_id,
                    deadline_seconds=self.http_deadline,
                )
            for backup in backups.values():
                self.docker("rm", backup["name"], check=False)
            return {"initial_witness": first, "restart_witness": after_restart, "rollback_available_during_cutover": bool(backups)}
        except BaseException as original:
            rollback_errors = self._restore_cutover(backups, created)
            if rollback_errors:
                raise BenchError("rollback", "cutover failed and rollback was incomplete", EXIT_ROLLBACK, detail=rollback_errors) from original
            if isinstance(original, KeyboardInterrupt):
                raise
            if isinstance(original, BenchError):
                raise
            raise BenchError("cutover", f"cutover failed and prior services were restored: {original}", EXIT_CUTOVER) from original


@dataclasses.dataclass
class HubContext:
    api: Any
    token: str
    parent_sha: str
    current_sdk: str | None
    parent_stage: str
    username: str
    readme_bytes: bytes | None
    index_template_bytes: bytes
    managed_parent_files: Mapping[str, bytes | None]
    download_root: pathlib.Path


def _space_sdk(info: Any) -> str | None:
    sdk = getattr(info, "sdk", None)
    if sdk:
        return str(sdk)
    card = getattr(info, "card_data", None)
    if isinstance(card, dict):
        return card.get("sdk")
    return getattr(card, "sdk", None) if card is not None else None


def validate_static_space_readme(data: bytes) -> None:
    if not hmac.compare_digest(sha256_bytes(data), SPACE_README_SHA256):
        raise BenchError("hub_auth", "Space README does not match the reviewed asset digest", EXIT_HUB_AUTH)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BenchError("hub_auth", "Space README must be UTF-8", EXIT_HUB_AUTH) from exc
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise BenchError("hub_auth", "Space README lacks YAML front matter", EXIT_HUB_AUTH)
    front = text[4:].split("\n---\n", 1)[0]
    fields: dict[str, str] = {}
    for line in front.splitlines():
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*", line)
        if not match:
            raise BenchError("hub_auth", "Space README uses unsupported or nested front matter", EXIT_HUB_AUTH)
        key, value = match.groups()
        if key in fields:
            raise BenchError("hub_auth", f"Space README contains duplicate metadata key {key}", EXIT_HUB_AUTH)
        fields[key] = value
    if fields.get("sdk") != "static":
        raise BenchError("hub_auth", "Space README front matter must declare sdk: static", EXIT_HUB_AUTH)
    if fields.get("app_file") != "index.html":
        raise BenchError("hub_auth", "Space README front matter must declare app_file: index.html", EXIT_HUB_AUTH)


def validate_space_index_template(data: bytes) -> None:
    if not hmac.compare_digest(sha256_bytes(data), SPACE_INDEX_TEMPLATE_SHA256):
        raise BenchError("hub_auth", "Space index template does not match the reviewed asset digest", EXIT_HUB_AUTH)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BenchError("hub_auth", "Space index must be UTF-8", EXIT_HUB_AUTH) from exc
    required = ("SZL Bench Suite", "results.json", "EMPTY_HONEST", "UNAVAILABLE", "Content-Security-Policy")
    missing = [value for value in required if value not in text]
    if missing:
        raise BenchError("hub_auth", f"Space index lacks required truth-state markers: {missing}", EXIT_HUB_AUTH)
    unsupported_claims = (
        "fused QKV",
        "PagedAttention",
        "CUDA Graphs",
        "HNSW recall guardrails",
        "Q4_K / Q8_0 / FP16 paths",
    )
    present = [value for value in unsupported_claims if value in text]
    if present:
        raise BenchError("hub_auth", f"Space index retains unsupported implementation claims: {present}", EXIT_HUB_AUTH)
    if text.count(RESULT_DIGEST_PLACEHOLDER) != 1:
        raise BenchError("hub_auth", "Space index must contain exactly one result-digest placeholder", EXIT_HUB_AUTH)
    forbidden_sinks = ("innerHTML", "outerHTML", "document.write", "insertAdjacentHTML", "eval(", "new Function")
    sinks = [sink for sink in forbidden_sinks if sink in text]
    if sinks:
        raise BenchError("hub_auth", f"Space index contains unsafe active-content sinks: {sinks}", EXIT_HUB_AUTH)


def finalize_space_index(template: bytes, payload_bytes: bytes) -> bytes:
    validate_space_index_template(template)
    rendered = template.replace(RESULT_DIGEST_PLACEHOLDER.encode("ascii"), sha256_bytes(payload_bytes).encode("ascii"), 1)
    if RESULT_DIGEST_PLACEHOLDER.encode("ascii") in rendered:
        raise BenchError("hub_auth", "Space index digest substitution was incomplete", EXIT_HUB_AUTH)
    return rendered


def export_space_bundle(args: argparse.Namespace, workdir: pathlib.Path, payload_bytes: bytes) -> Mapping[str, Any]:
    """Export verified audit output for a separately authorized sole publisher."""
    if not args.audit_only or not args.space_readme or not args.space_index:
        raise BenchError("bundle_export", "bundle export requires --audit-only and both reviewed Space assets", EXIT_CLI)
    requested = pathlib.Path(args.export_space_bundle).expanduser()
    target = pathlib.Path(os.path.abspath(requested if requested.is_absolute() else workdir / requested))
    if target == workdir or not target.is_relative_to(workdir):
        raise BenchError("bundle_export", "bundle export must be a new directory inside --workdir", EXIT_CLI)
    _reject_link_components(target, phase="bundle_export", exit_code=EXIT_CLI)
    if target.exists():
        raise BenchError("bundle_export", f"refusing to overwrite an existing bundle directory: {target}", EXIT_CLI)
    readme = read_bounded_regular_file(
        pathlib.Path(os.path.abspath(pathlib.Path(args.space_readme).expanduser())),
        limit=MAX_HTTP_BYTES,
        phase="bundle_export",
        exit_code=EXIT_RESULT,
    )
    template = read_bounded_regular_file(
        pathlib.Path(os.path.abspath(pathlib.Path(args.space_index).expanduser())),
        limit=MAX_HTTP_BYTES,
        phase="bundle_export",
        exit_code=EXIT_RESULT,
    )
    validate_static_space_readme(readme)
    desired = {"README.md": readme, "index.html": finalize_space_index(template, payload_bytes), "results.json": payload_bytes}
    ensure_private_directory(target.parent, phase="bundle_export", exit_code=EXIT_RESULT)
    try:
        target.mkdir(mode=0o700)
    except OSError as exc:
        raise BenchError("bundle_export", f"could not reserve a new bundle directory: {redact(exc)}", EXIT_RESULT) from exc
    hashes: dict[str, str] = {}
    for name, data in desired.items():
        atomic_write(target / name, data)
        observed = read_bounded_regular_file(target / name, limit=MAX_HTTP_BYTES, phase="bundle_export", exit_code=EXIT_RESULT)
        if not hmac.compare_digest(sha256_bytes(observed), sha256_bytes(data)):
            raise BenchError("bundle_export", f"exported {name} differs from the verified audit payload", EXIT_RESULT)
        hashes[name] = sha256_bytes(observed)
    return {"directory": str(target), "sha256": hashes, "file_count": len(hashes), "remote_mutation": "NOT_ATTEMPTED"}


def hub_preflight(args: argparse.Namespace, download_root: pathlib.Path) -> HubContext:
    token = os.environ.get(args.hf_token_env, "")
    if not token:
        raise BenchError("hub_auth", f"published target requires a token in {args.hf_token_env}", EXIT_HUB_AUTH)
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise BenchError("hub_auth", "published target requires the huggingface_hub package", EXIT_HUB_AUTH) from exc
    api = HfApi(token=token)
    try:
        identity = api.whoami()
        username = str(identity.get("name", ""))
        if username.lower() != args.expected_hf_user.lower():
            raise BenchError("hub_auth", f"authenticated Hugging Face user is {username!r}, expected {args.expected_hf_user!r}", EXIT_HUB_AUTH)
        info = api.repo_info(repo_id=SPACE_ID, repo_type="space")
    except BenchError:
        raise
    except Exception as exc:
        raise BenchError("hub_auth", f"could not verify Hugging Face identity/Space: {redact(exc)}", EXIT_HUB_AUTH) from exc
    if str(getattr(info, "id", SPACE_ID)).lower() != SPACE_ID.lower():
        raise BenchError("hub_auth", "Hugging Face returned the wrong Space identity", EXIT_HUB_AUTH)
    parent = str(getattr(info, "sha", ""))
    if not COMMIT_RE.fullmatch(parent):
        raise BenchError("hub_auth", "Space head is not an immutable commit SHA", EXIT_HUB_AUTH)
    readme_bytes = None
    if args.space_readme:
        readme_path = pathlib.Path(args.space_readme).expanduser().resolve()
        require_regular_file(readme_path, phase="hub_auth", exit_code=EXIT_HUB_AUTH)
        readme_bytes = read_bounded_regular_file(readme_path, limit=MAX_HTTP_BYTES, phase="hub_auth", exit_code=EXIT_HUB_AUTH)
        validate_static_space_readme(readme_bytes)
    if not args.space_index:
        raise BenchError(
            "hub_auth",
            "published target requires --space-index with the reviewed fail-closed public surface",
            EXIT_HUB_AUTH,
        )
    index_path = pathlib.Path(args.space_index).expanduser().resolve()
    require_regular_file(index_path, phase="hub_auth", exit_code=EXIT_HUB_AUTH)
    index_bytes = read_bounded_regular_file(index_path, limit=MAX_HTTP_BYTES, phase="hub_auth", exit_code=EXIT_HUB_AUTH)
    validate_space_index_template(index_bytes)
    current_sdk = _space_sdk(info)
    if current_sdk != "static" and readme_bytes is None:
        raise BenchError(
            "hub_auth",
            f"Space SDK is {current_sdk or 'UNCONFIGURED'}; pass --space-readme with a reviewed sdk: static README",
            EXIT_HUB_AUTH,
        )
    try:
        files = set(api.list_repo_files(repo_id=SPACE_ID, repo_type="space", revision=parent))
    except Exception as exc:
        raise BenchError("hub_auth", f"could not inventory Space files at immutable parent: {redact(exc)}", EXIT_HUB_AUTH) from exc
    download_root = ensure_private_directory(download_root, phase="hub_readback", exit_code=EXIT_HUB_READBACK)
    managed: dict[str, bytes | None] = {}
    for filename in ("README.md", "index.html", "results.json"):
        managed[filename] = (
            _download_hub_file_strict(SPACE_ID, filename, parent, token, download_root)
            if filename in files
            else None
        )
    try:
        parent_stage = str(getattr(api.get_space_runtime(repo_id=SPACE_ID), "stage", "UNKNOWN"))
    except Exception as exc:
        raise BenchError("hub_auth", f"could not read the current Space runtime state: {redact(exc)}", EXIT_HUB_AUTH) from exc
    return HubContext(
        api=api,
        token=token,
        parent_sha=parent,
        current_sdk=current_sdk,
        parent_stage=parent_stage,
        username=username,
        readme_bytes=readme_bytes,
        index_template_bytes=index_bytes,
        managed_parent_files=managed,
        download_root=download_root,
    )


def _read_hub_download_path(path: pathlib.Path, local_dir: pathlib.Path) -> bytes:
    try:
        local_root = local_dir.resolve(strict=True)
        candidate = pathlib.Path(os.path.abspath(path))
        if not candidate.is_relative_to(local_root):
            raise BenchError("hub_readback", "Hugging Face returned a path outside the private download directory", EXIT_HUB_READBACK)
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(local_root):
            raise BenchError("hub_readback", "Hugging Face download link escapes the private download directory", EXIT_HUB_READBACK)
        _reject_link_components(resolved.parent, phase="hub_readback", exit_code=EXIT_HUB_READBACK)
        return read_bounded_regular_file(
            resolved,
            limit=MAX_HTTP_BYTES,
            phase="hub_readback",
            exit_code=EXIT_HUB_READBACK,
        )
    except BenchError:
        raise
    except (OSError, RuntimeError) as exc:
        raise BenchError("hub_readback", f"could not resolve private Hub download: {redact(exc)}", EXIT_HUB_READBACK) from exc


def _download_hub_file_strict(
    repo_id: str,
    filename: str,
    revision: str,
    token: str,
    download_root: pathlib.Path,
) -> bytes:
    try:
        from huggingface_hub import hf_hub_download

        local_dir = ensure_private_directory(
            download_root / revision / sha256_bytes(filename.encode("utf-8"))[:24],
            phase="hub_readback",
            exit_code=EXIT_HUB_READBACK,
        )
        download_args: dict[str, Any] = {
            "repo_id": repo_id,
            "filename": filename,
            "repo_type": "space",
            "revision": revision,
            "token": token,
            "force_download": True,
            "local_dir": str(local_dir),
        }
        with contextlib.suppress(TypeError, ValueError):
            if "local_dir_use_symlinks" in inspect.signature(hf_hub_download).parameters:
                download_args["local_dir_use_symlinks"] = False
        path = hf_hub_download(**download_args)
        return _read_hub_download_path(pathlib.Path(path), local_dir)
    except BenchError:
        raise
    except Exception as exc:
        raise BenchError("hub_readback", f"could not read {filename} at immutable revision {revision}: {redact(exc)}", EXIT_HUB_READBACK) from exc


def publish_and_witness(
    context: HubContext,
    payload_bytes: bytes,
    *,
    provider_timeout: float,
    public_http_deadline: float,
) -> dict[str, Any]:
    try:
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete
    except ImportError as exc:
        raise BenchError("hub_commit", "huggingface_hub lacks required commit operations", EXIT_HUB_COMMIT) from exc

    index_bytes = finalize_space_index(context.index_template_bytes, payload_bytes)
    desired: dict[str, bytes | None] = dict(context.managed_parent_files)
    desired["results.json"] = payload_bytes
    desired["index.html"] = index_bytes
    if context.readme_bytes is not None:
        desired["README.md"] = context.readme_bytes

    def operations_between(current: Mapping[str, bytes | None], target: Mapping[str, bytes | None]) -> list[Any]:
        operations: list[Any] = []
        for filename in ("README.md", "index.html", "results.json"):
            before, after = current.get(filename), target.get(filename)
            if before == after:
                continue
            if after is None:
                operations.append(CommitOperationDelete(path_in_repo=filename))
            else:
                operations.append(CommitOperationAdd(path_in_repo=filename, path_or_fileobj=io.BytesIO(after)))
        return operations

    def head_stage() -> tuple[str, str]:
        try:
            info = context.api.repo_info(repo_id=SPACE_ID, repo_type="space")
            runtime = context.api.get_space_runtime(repo_id=SPACE_ID)
        except Exception as exc:
            raise BenchError("provider", f"could not read Space head/runtime: {redact(exc)}", EXIT_PROVIDER) from exc
        return str(getattr(info, "sha", "")), str(getattr(runtime, "stage", "UNKNOWN"))

    def verify_revision(revision: str, expected: Mapping[str, bytes | None]) -> dict[str, str | None]:
        try:
            files = set(context.api.list_repo_files(repo_id=SPACE_ID, repo_type="space", revision=revision))
        except Exception as exc:
            raise BenchError("hub_readback", f"could not inventory immutable revision {revision}: {redact(exc)}", EXIT_HUB_READBACK) from exc
        hashes: dict[str, str | None] = {}
        for filename, expected_bytes in expected.items():
            present = filename in files
            if expected_bytes is None:
                if present:
                    raise BenchError("hub_readback", f"{filename} unexpectedly exists at immutable revision {revision}", EXIT_HUB_READBACK)
                hashes[filename] = None
                continue
            if not present:
                raise BenchError("hub_readback", f"{filename} is absent at immutable revision {revision}", EXIT_HUB_READBACK)
            observed = _download_hub_file_strict(SPACE_ID, filename, revision, context.token, context.download_root)
            if not hmac.compare_digest(sha256_bytes(observed), sha256_bytes(expected_bytes)):
                raise BenchError("hub_readback", f"immutable {filename} readback mismatch", EXIT_HUB_READBACK)
            hashes[filename] = sha256_bytes(observed)
        return hashes

    def compensate(commit_sha: str) -> Mapping[str, Any]:
        try:
            current_head, _ = head_stage()
            if current_head != commit_sha:
                return {"state": "CONCURRENT_DRIFT_REMOTE_MUTATED", "expected_head": commit_sha, "observed_head": current_head}
            rollback_ops = operations_between(desired, context.managed_parent_files)
            rollback = context.api.create_commit(
                repo_id=SPACE_ID,
                repo_type="space",
                operations=rollback_ops,
                commit_message="revert: restore SZL bench plane after failed publication witness",
                parent_commit=commit_sha,
            )
            rollback_sha = str(getattr(rollback, "oid", "") or getattr(rollback, "commit_id", ""))
            if not COMMIT_RE.fullmatch(rollback_sha):
                raise RuntimeError("rollback did not return an immutable commit")
            verify_revision(rollback_sha, context.managed_parent_files)
            deadline = time.monotonic() + provider_timeout
            final_head, final_stage = "", "UNKNOWN"
            while time.monotonic() < deadline:
                final_head, final_stage = head_stage()
                if final_head == rollback_sha and final_stage == context.parent_stage:
                    return {
                        "state": "PUBLISH_FAILED_ROLLBACK_VERIFIED",
                        "rollback_commit": rollback_sha,
                        "restored_stage": final_stage,
                    }
                time.sleep(min(10.0, max(0.0, deadline - time.monotonic())))
            raise RuntimeError(f"rollback did not converge: head={final_head} stage={final_stage}")
        except BaseException as exc:
            state = "ROLLBACK_INTERRUPTED_REMOTE_STATE_UNKNOWN" if isinstance(exc, KeyboardInterrupt) else "ROLLBACK_FAILED_REMOTE_MUTATED"
            return {"state": state, "error": redact(exc)}

    refreshed_head, _ = head_stage()
    if refreshed_head != context.parent_sha:
        raise BenchError(
            "hub_commit",
            "Space head changed after preflight; no publication commit was attempted",
            EXIT_HUB_COMMIT,
            detail={"captured_parent": context.parent_sha, "observed_head": refreshed_head},
        )
    operations = operations_between(context.managed_parent_files, desired)
    commit_sha = context.parent_sha
    mutated = False
    if operations:
        try:
            commit = context.api.create_commit(
                repo_id=SPACE_ID,
                repo_type="space",
                operations=operations,
                commit_message="chore: publish verified SZL bench-plane snapshot",
                parent_commit=context.parent_sha,
            )
        except BaseException as exc:
            observed_head = "UNKNOWN_AFTER_ATTEMPT"
            with contextlib.suppress(BaseException):
                observed_head = str(getattr(context.api.repo_info(repo_id=SPACE_ID, repo_type="space"), "sha", observed_head))
            state = "NO_REMOTE_MUTATION_OBSERVED" if observed_head == context.parent_sha else "UNKNOWN_AFTER_ATTEMPT_REMOTE_MAY_BE_MUTATED"
            raise BenchError(
                "hub_commit",
                f"Hugging Face compare-and-swap commit did not return a confirmed result: {redact(exc)}",
                EXIT_HUB_COMMIT,
                detail={"state": state, "captured_parent": context.parent_sha, "observed_head": observed_head},
            ) from exc
        commit_sha = str(getattr(commit, "oid", "") or getattr(commit, "commit_id", ""))
        if not COMMIT_RE.fullmatch(commit_sha):
            observed_head = "UNKNOWN_AFTER_ATTEMPT"
            with contextlib.suppress(Exception):
                observed_head = str(getattr(context.api.repo_info(repo_id=SPACE_ID, repo_type="space"), "sha", observed_head))
            state = "NO_REMOTE_MUTATION_OBSERVED" if observed_head == context.parent_sha else "UNKNOWN_AFTER_ATTEMPT_REMOTE_MAY_BE_MUTATED"
            raise BenchError(
                "hub_commit",
                "Hugging Face accepted the commit request but did not return an immutable commit SHA",
                EXIT_HUB_COMMIT,
                detail={"state": state, "captured_parent": context.parent_sha, "observed_head": observed_head},
            )
        mutated = True
    if not COMMIT_RE.fullmatch(commit_sha):
        raise BenchError("hub_commit", "Hugging Face did not return an immutable commit SHA", EXIT_HUB_COMMIT)
    try:
        immutable_hashes = verify_revision(commit_sha, desired)
        deadline = time.monotonic() + provider_timeout
        first_head, first_stage = "", "UNKNOWN"
        while time.monotonic() < deadline:
            first_head, first_stage = head_stage()
            if not mutated and first_head != context.parent_sha:
                raise BenchError(
                    "provider",
                    "Space head changed while verifying unchanged publication files; no runtime action was attempted",
                    EXIT_PROVIDER,
                    detail={
                        "state": "CONCURRENT_HEAD_DRIFT_NO_REMOTE_MUTATION",
                        "remote_mutation": "NO_REMOTE_MUTATION",
                        "captured_parent": context.parent_sha,
                        "observed_head": first_head,
                    },
                )
            if (
                not mutated
                and context.current_sdk == "static"
                and first_stage in {"STOPPED", "PAUSED", "SLEEPING", "BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR"}
            ):
                # The Hub restart API explicitly rejects static Spaces. A
                # successful no-op content comparison cannot repair this state.
                raise BenchError(
                    "provider",
                    f"Unchanged static Space is {first_stage}; the provider restart API does not support static Spaces",
                    EXIT_PROVIDER,
                    detail={
                        "state": "STATIC_RUNTIME_RECOVERY_UNAVAILABLE",
                        "remote_mutation": "NO_REMOTE_MUTATION",
                        "content_changed": False,
                        "runtime_repair": "UNAVAILABLE_STATIC_SPACE",
                        "verified_commit": commit_sha,
                        "observed_stage": first_stage,
                        "immutable_file_sha256": immutable_hashes,
                    },
                )
            if first_head == commit_sha and first_stage == "RUNNING":
                break
            time.sleep(min(10.0, max(0.0, deadline - time.monotonic())))
        else:
            raise BenchError("provider", f"Space did not reach RUNNING at expected head: head={first_head} stage={first_stage}", EXIT_PROVIDER)

        public_deadline = time.monotonic() + public_http_deadline
        last_error: str | None = None
        public_bytes: bytes | None = None
        public_index: bytes | None = None
        while time.monotonic() < public_deadline:
            try:
                public_bytes, _ = http_get_bytes(
                    f"{SPACE_URL}/results.json?run={commit_sha}", timeout=10, max_bytes=MAX_HTTP_BYTES, expect_json=True
                )
                public_index, index_headers = http_get_bytes(f"{SPACE_URL}/?run={commit_sha}", timeout=15, max_bytes=MAX_HTTP_BYTES)
                if "text/html" not in str(index_headers.get("Content-Type", "")).lower():
                    raise BenchError("public_runtime", "public index has the wrong content type", EXIT_PROVIDER)
                if not hmac.compare_digest(sha256_bytes(public_bytes), sha256_bytes(payload_bytes)):
                    raise BenchError("public_runtime", "public results payload digest mismatch", EXIT_PROVIDER)
                if not hmac.compare_digest(sha256_bytes(public_index), sha256_bytes(index_bytes)):
                    raise BenchError("public_runtime", "public index digest mismatch", EXIT_PROVIDER)
                break
            except BenchError as exc:
                last_error = str(exc)
            time.sleep(min(5.0, max(0.0, public_deadline - time.monotonic())))
        else:
            raise BenchError("public_runtime", f"public Space bytes were not witnessed: {last_error}", EXIT_PROVIDER)
        final_head, final_stage = head_stage()
        if final_head != commit_sha or final_stage != "RUNNING":
            raise BenchError(
                "public_runtime",
                "Space head/runtime changed during the public witness",
                EXIT_PROVIDER,
                detail={"expected_head": commit_sha, "observed_head": final_head, "observed_stage": final_stage},
            )
    except BaseException as original:
        if mutated:
            rollback = compensate(commit_sha)
            if isinstance(original, KeyboardInterrupt) and rollback.get("state") == "PUBLISH_FAILED_ROLLBACK_VERIFIED":
                raise
            raise BenchError(
                "hub_rollback",
                "publication witness failed after remote mutation; compensating outcome recorded",
                EXIT_PROVIDER if rollback.get("state") == "PUBLISH_FAILED_ROLLBACK_VERIFIED" else EXIT_ROLLBACK,
                detail={"publication_error": redact(original), "compensation": rollback},
            ) from original
        raise
    return {
        "space": SPACE_ID,
        "space_url": SPACE_URL,
        "publisher": context.username,
        "parent_commit": context.parent_sha,
        "commit": commit_sha,
        "changed": mutated,
        "immutable_readback_sha256": immutable_hashes["results.json"],
        "immutable_index_sha256": immutable_hashes["index.html"],
        "immutable_readme_sha256": immutable_hashes["README.md"],
        "provider_stage": "RUNNING",
        "public_results_sha256": sha256_bytes(public_bytes or b""),
        "public_index_sha256": sha256_bytes(public_index or b""),
        "first_head_observation": first_head,
        "final_head_observation": final_head,
        "witnessed_at": utc_now(),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy and truthfully verify the SZL bench evidence plane")
    parser.add_argument("--workdir", default=os.path.expanduser("~/szl-bench"), help="dedicated controller state directory")
    parser.add_argument("--target", choices=("local", "published"), default="local", help="required completion boundary (default: local; publication must be explicit)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--audit-only", action="store_true", help="statically verify pinned blobs, receipts, and deterministic output only")
    modes.add_argument("--preflight-only", action="store_true", help="verify machine, Docker, and target credentials without changing services")
    parser.add_argument("--report", help="durable JSON report path (default: WORKDIR/evidence/RUN_ID.json)")
    parser.add_argument("--space-readme", help="reviewed UTF-8 README.md with sdk: static metadata; committed atomically when needed")
    parser.add_argument("--space-index", help="reviewed fail-closed index.html; required for the published target")
    parser.add_argument("--export-space-bundle", metavar="DIRECTORY", help="audit-only: write the reviewed static bundle to a new directory inside WORKDIR; requires both Space assets; never publishes")
    parser.add_argument("--hf-token-env", default="HF_TOKEN", help="name of the environment variable containing the HF token")
    parser.add_argument("--expected-hf-user", default="betterwithage", help="required Hugging Face publisher identity")
    parser.add_argument("--git-bin", help="absolute trusted Git executable (trusted system defaults are used when omitted)")
    parser.add_argument("--docker-bin", help="absolute trusted Docker executable (Linux deployment only)")
    parser.add_argument("--nvidia-smi-bin", help="absolute trusted nvidia-smi executable")
    parser.add_argument("--http-deadline", type=float, default=90.0, help="seconds allowed for each local service to become ready")
    parser.add_argument("--provider-timeout", type=float, default=600.0, help="seconds allowed for the Space to reach RUNNING")
    parser.add_argument("--public-http-deadline", type=float, default=180.0, help="seconds allowed for public payload convergence")
    parser.add_argument("--quiet", action="store_true", help="suppress successful command output")
    args = parser.parse_args(argv)
    if args.export_space_bundle and (not args.audit_only or not args.space_readme or not args.space_index):
        parser.error("--export-space-bundle requires --audit-only, --space-readme, and --space-index")
    for name in ("http_deadline", "provider_timeout", "public_http_deadline"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


def execute(args: argparse.Namespace) -> int:
    if sys.version_info < (3, 11):
        raise BenchError("preflight", "Python 3.11 or newer is required", EXIT_PREFLIGHT)
    if os.name != "nt":
        os.umask(0o077)
    workdir = validate_workdir(args.workdir)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    report_path = pathlib.Path(os.path.abspath(pathlib.Path(args.report).expanduser())) if args.report else workdir / "evidence" / f"{run_id}.json"
    if not report_path.is_relative_to(workdir):
        raise BenchError("cli", "--report must be inside the dedicated --workdir", EXIT_CLI)
    if report_path.exists():
        raise BenchError("cli", f"refusing to overwrite an existing evidence report: {report_path}", EXIT_CLI)
    report = EvidenceReport(report_path, run_id, args)
    runner = CommandRunner(quiet=args.quiet)
    try:
        if os.name == "nt" and not args.audit_only:
            raise BenchError(
                "platform",
                "Windows supports static --audit-only verification; deployment requires the qualified Linux bench node",
                EXIT_PREFLIGHT,
            )
        with contextlib.ExitStack() as leases:
            leases.enter_context(RunLease(workdir / "lock" / "active", run_id))
            if not args.audit_only and not args.preflight_only:
                leases.enter_context(RunLease(global_deploy_lease_path(), run_id))
            run_root = ensure_private_directory(workdir / "runs" / run_id, phase="preflight", exit_code=EXIT_PREFLIGHT)
            git_bin = resolve_trusted_executable("git", args.git_bin)
            if git_bin is None:
                raise BenchError("preflight", "trusted Git executable is unavailable", EXIT_PREFLIGHT)
            git_helpers = validate_git_helper_root(runner, git_bin)
            nvidia_smi = resolve_trusted_executable("nvidia_smi", args.nvidia_smi_bin, required=False)
            docker_bin: str | None = None
            docker_client: DockerClient | None = None
            if not args.audit_only:
                docker_bin = resolve_trusted_executable("docker", args.docker_bin)
                if docker_bin is None:
                    raise BenchError("docker_preflight", "trusted Docker executable is unavailable", EXIT_PREFLIGHT)
                docker_client = DockerClient(runner, docker_bin, run_root / "docker-client")
            tool_records = {
                "git": {"path": git_bin, "sha256": sha256_file(pathlib.Path(git_bin)), **git_helpers},
                "nvidia_smi": {"path": nvidia_smi, "sha256": sha256_file(pathlib.Path(nvidia_smi))} if nvidia_smi else None,
                "docker": {"path": docker_bin, "sha256": sha256_file(pathlib.Path(docker_bin))} if docker_bin else None,
                "selection": "ABSOLUTE_EXPLICIT_OR_SYSTEM_DEFAULT_PATHS_NO_INHERITED_PATH",
            }
            report.layer("toolchain", "RESOLVED_ABSOLUTE_PATHS", **tool_records)
            machine = probe_machine(runner, nvidia_smi)
            if args.audit_only:
                report.layer("machine", "OBSERVED_NOT_QUALIFIED_FOR_AUDIT", observation=machine)
            else:
                qualify_machine(machine)
                report.layer("machine", "QUALIFIED", observation=machine, policy=EXPECTED_MACHINE)

            docker_info: dict[str, Any] | None = None
            hub: HubContext | None = None
            if not args.audit_only:
                if docker_client is None:
                    raise BenchError("docker_preflight", "internal Docker client initialization failure", EXIT_INTERNAL)
                docker_info = docker_preflight(docker_client)
            if args.preflight_only:
                if args.target == "published":
                    hub = hub_preflight(args, run_root / "hub-downloads")
                    report.layer(
                        "publication",
                        "PREFLIGHT_VERIFIED",
                        space=SPACE_ID,
                        parent_commit=hub.parent_sha,
                        publisher=hub.username,
                        current_sdk=hub.current_sdk or "UNCONFIGURED",
                    )
                report.layer("local_runtime", "PREFLIGHT_VERIFIED", docker=docker_info)
                report.finish("PREFLIGHT_VERIFIED", 0)
                print(f"PREFLIGHT_VERIFIED report={report.path}")
                return 0

            sources: dict[str, dict[str, Any]] = {}
            snapshots: list[GitSnapshot] = []
            receipt_sets: list[dict[str, Any]] = []
            for spec in REPOS:
                snapshot = materialize_source(spec, run_root, runner, git_bin)
                snapshots.append(snapshot)
                sources[spec.plane] = dict(snapshot.record)
            report.layer("source", "VERIFIED_EXACT_PINS_NO_CHECKOUT", repositories=sources)
            report.layer(
                "source_tests",
                "NOT_EXECUTED_UNTRUSTED_SOURCE",
                explanation="Reviewed test-file digests matched the exact pins; repository code was not executed on the controller host.",
            )
            report.layer(
                "source_authenticity",
                next(iter({record["commit_authenticity"] for record in sources.values()}))
                if len({record["commit_authenticity"] for record in sources.values()}) == 1
                else "MIXED_SIGNATURE_STATUS_NOT_VERIFIED",
                repositories={plane: record["commit_authenticity"] for plane, record in sources.items()},
                explanation="Exact commit SHAs and reviewed file hashes matched. Signature presence is observed from commit objects; this controller does not verify signer identities.",
            )
            report.layer(
                "source_ci",
                "UNVERIFIED_EXTERNAL",
                explanation="This controller does not treat repository-controlled tests or unauthenticated remote status as CI proof.",
            )
            receipt_key = load_receipt_auth_key()
            try:
                for snapshot in snapshots:
                    receipt_sets.append(verify_receipts(snapshot.spec, snapshot, receipt_key))
            finally:
                if receipt_key is not None:
                    for index in range(len(receipt_key)):
                        receipt_key[index] = 0
            report.layer(
                "receipts",
                "VERIFIED_CHAINS_MEASUREMENTS_REQUIRE_HMAC",
                planes={item["plane"]: {key: value for key, value in item.items() if key not in {"results", "inventory"}} for item in receipt_sets},
            )
            merged, per_plane = assemble_payload(receipt_sets)
            payload_bytes = pretty_json_bytes(merged)
            report.layer(
                "data",
                merged["data_state"],
                count=merged["count"],
                results_sha256=merged["results_sha256"],
                generated_at=merged["generated_at"],
            )
            if args.audit_only:
                if args.export_space_bundle:
                    bundle = export_space_bundle(args, workdir, payload_bytes)
                    report.layer("bundle_export", "VERIFIED_LOCAL_BUNDLE", **bundle)
                report.layer("local_runtime", "NOT_RUN_AUDIT_ONLY")
                report.layer("publication", "NOT_RUN_AUDIT_ONLY")
                report.layer("provider", "NOT_RUN_AUDIT_ONLY")
                report.layer("public_runtime", "NOT_RUN_AUDIT_ONLY")
                report.finish("AUDIT_VERIFIED", 0)
                print(f"AUDIT_VERIFIED data={merged['data_state']} count={merged['count']} report={report.path}")
                return 0

            if args.target == "published":
                hub = hub_preflight(args, run_root / "hub-downloads")
                report.layer(
                    "publication",
                    "PREFLIGHT_VERIFIED",
                    space=SPACE_ID,
                    parent_commit=hub.parent_sha,
                    publisher=hub.username,
                    current_sdk=hub.current_sdk or "UNCONFIGURED",
                    parent_stage=hub.parent_stage,
                )
            generation = ensure_private_directory(run_root / "generation", phase="build", exit_code=EXIT_BUILD)
            atomic_write(generation / "merged-results.json", payload_bytes, mode=0o600)
            if docker_client is None:
                raise BenchError("docker_preflight", "internal Docker client missing", EXIT_INTERNAL)
            deployer = DockerDeployer(docker_client, run_id, args.http_deadline)
            build_records: dict[str, Any] = {}
            candidate_records: dict[str, Any] = {}
            try:
                for spec in REPOS:
                    context = prepare_build_context(spec, generation, per_plane[spec.plane], run_id)
                    build_records[spec.plane] = deployer.build(spec, context, per_plane[spec.plane]["results_sha256"])
                for spec in REPOS:
                    candidate_records[spec.plane] = deployer.stage(spec, per_plane[spec.plane])
            finally:
                deployer.cleanup_candidates()
            cutover = deployer.cutover(per_plane)
            report.layer(
                "local_runtime",
                "LOCAL_EVIDENCE_APIS_OPERATIONAL",
                docker=docker_info,
                images=build_records,
                candidate_witness=candidate_records,
                cutover=cutover,
            )
            if args.target == "local":
                report.finish("LOCAL_EVIDENCE_APIS_OPERATIONAL", 0)
                print(
                    f"LOCAL_EVIDENCE_APIS_OPERATIONAL data={merged['data_state']} count={merged['count']} "
                    f"publication=NOT_REQUESTED report={report.path}"
                )
                return 0
            if hub is None:
                raise BenchError("hub_auth", "internal error: published target has no Hub context", EXIT_INTERNAL)
            publication = publish_and_witness(
                hub,
                payload_bytes,
                provider_timeout=args.provider_timeout,
                public_http_deadline=args.public_http_deadline,
            )
            report.layer(
                "publication",
                "VERIFIED_IMMUTABLE_READBACK",
                space=publication["space"],
                parent_commit=publication["parent_commit"],
                commit=publication["commit"],
                changed=publication["changed"],
                results_sha256=publication["immutable_readback_sha256"],
                index_sha256=publication["immutable_index_sha256"],
                readme_sha256=publication["immutable_readme_sha256"],
                first_head_observation=publication["first_head_observation"],
                final_head_observation=publication["final_head_observation"],
            )
            report.layer("provider", "RUNNING", stage=publication["provider_stage"], commit=publication["commit"])
            report.layer(
                "public_runtime",
                "PUBLIC_PAYLOAD_WITNESSED",
                url=publication["space_url"],
                results_sha256=publication["public_results_sha256"],
                index_sha256=publication["public_index_sha256"],
                witnessed_at=publication["witnessed_at"],
            )
            report.finish("PUBLISHED_EVIDENCE_SURFACE_OPERATIONAL", 0)
            print(
                f"PUBLISHED_EVIDENCE_SURFACE_OPERATIONAL data={merged['data_state']} count={merged['count']} "
                f"commit={publication['commit']} report={report.path}"
            )
            return 0
    except KeyboardInterrupt:
        report.finish("INTERRUPTED", EXIT_INTERRUPTED, {"phase": "signal", "message": "operator interrupted the run"})
        print(f"INTERRUPTED report={report.path}", file=sys.stderr)
        return EXIT_INTERRUPTED
    except BenchError as exc:
        detail = exc.detail
        overall = "BLOCKED"
        if exc.phase == "hub_rollback" and isinstance(detail, Mapping):
            compensation = detail.get("compensation")
            if isinstance(compensation, Mapping) and isinstance(compensation.get("state"), str):
                overall = str(compensation["state"])
        elif exc.phase == "hub_commit" and isinstance(detail, Mapping) and str(detail.get("state", "")).startswith("UNKNOWN_AFTER_ATTEMPT"):
            overall = "UNKNOWN_AFTER_ATTEMPT_REMOTE_MAY_BE_MUTATED"
        elif exc.phase == "rollback":
            overall = "ROLLBACK_FAILED_LOCAL_MUTATED"
        report.finish(
            overall,
            exc.exit_code,
            {"phase": exc.phase, "message": redact(exc), "detail": detail},
        )
        print(f"{overall} phase={exc.phase} reason={redact(exc)} report={report.path}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        report.finish(
            "FAILED",
            EXIT_INTERNAL,
            {"phase": "internal", "message": redact(exc), "type": type(exc).__name__},
        )
        print(f"FAILED reason={redact(exc)} report={report.path}", file=sys.stderr)
        return EXIT_INTERNAL


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return execute(args)
    except BenchError as exc:
        print(f"BLOCKED phase={exc.phase} reason={redact(exc)}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
