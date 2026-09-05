"""Publish the canonical static bench bundle with compare-and-set semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


TARGET = "betterwithage/szl-bench-suite"
EXPECTED_MARKER = "SZL Bench Suite"
READBACK_FILES = ("results.json", "deployment.json")
ALLOWED_LIVE_HOSTS = {
    "betterwithage-szl-bench-suite.hf.space",
    "betterwithage-szl-bench-suite.static.hf.space",
}
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".svg", ".txt"}


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _stage(info: Any) -> str:
    runtime = _field(info, "runtime", {})
    return str(_field(runtime, "stage", "UNKNOWN") or "UNKNOWN").upper()


def _live_url(info: Any) -> str:
    value = str(_field(info, "host", "")).rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_LIVE_HOSTS
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"provider returned an unexpected live host: {value!r}")
    return value + "/"


def _fetch(base_url: str, path: str, revision: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"{base_url}{path}?source_verify={revision}",
        headers={"Cache-Control": "no-cache", "User-Agent": "SZL-Bench-Publisher/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(response.status), response.read(2 * 1024 * 1024)


def _repo_file(path: str, revision: str) -> bytes | None:
    encoded_path = urllib.parse.quote(path)
    request = urllib.request.Request(
        f"https://huggingface.co/spaces/{TARGET}/resolve/{revision}/{encoded_path}",
        headers={"Cache-Control": "no-cache", "User-Agent": "SZL-Bench-Publisher/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def _bundle_files() -> list[Path]:
    files = sorted(path for path in Path("site").rglob("*") if path.is_file())
    if not files or any(path.is_symlink() for path in files):
        raise ValueError("site bundle must contain regular files and no symlinks")
    relative_paths = {path.relative_to("site").as_posix() for path in files}
    required_paths = {"README.md", "index.html", "style.css", "app.js", *READBACK_FILES}
    if not required_paths.issubset(relative_paths):
        missing = sorted(required_paths - relative_paths)
        raise ValueError(f"site bundle is incomplete; missing={missing}")
    return files


def _canonical_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return raw
    text = raw.decode("utf-8")
    if "\x00" in text:
        raise ValueError(f"text bundle file contains a NUL byte: {path}")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _bundle_matches(revision: str) -> bool:
    files = _bundle_files()
    for local_path in files:
        relative = local_path.relative_to("site").as_posix()
        if _repo_file(relative, revision) != _canonical_bytes(local_path):
            return False
    return True


def _materialize_upload_bundle(directory: Path) -> None:
    for local_path in _bundle_files():
        relative = local_path.relative_to("site")
        output = directory / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_canonical_bytes(local_path))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cached-auth",
        action="store_true",
        help="use the local Hugging Face cached login for an attended owner recovery",
    )
    args = parser.parse_args()

    environment_token = os.environ.get("HF_TOKEN")
    if args.cached_auth and environment_token:
        print("publisher blocked: cached auth and HF_TOKEN cannot be combined")
        return 2
    credential: str | bool = True if args.cached_auth else (environment_token or False)

    from huggingface_hub import HfApi

    read_api = HfApi()

    before = read_api.space_info(TARGET)
    before_sha = str(_field(before, "sha", ""))
    if len(before_sha) != 40:
        print("publisher blocked: provider did not return an exact pre-write revision")
        return 2

    changed = not _bundle_matches(before_sha)
    needs_write = changed or _stage(before) != "RUNNING"
    write_api = None
    if needs_write:
        if not credential:
            print(
                "publisher blocked: HF_TOKEN is required because the bundle or runtime needs a write"
            )
            return 2
        write_api = HfApi(token=credential)
        identity = write_api.whoami()
        if identity.get("name") != "betterwithage":
            print("publisher blocked: credential identity is not betterwithage")
            return 2

    if changed:
        assert write_api is not None
        with tempfile.TemporaryDirectory(prefix="szl-bench-publisher-") as temporary:
            _materialize_upload_bundle(Path(temporary))
            commit = write_api.upload_folder(
                folder_path=temporary,
                path_in_repo="",
                repo_id=TARGET,
                repo_type="space",
                parent_commit=before_sha,
                commit_message="fix: publish source-bound consolidated bench suite",
            )
        expected_sha = str(_field(commit, "oid", ""))
        if len(expected_sha) != 40:
            print("publisher blocked: provider did not return an exact commit revision")
            return 2
    else:
        expected_sha = before_sha
        if needs_write:
            assert write_api is not None
            write_api.restart_space(TARGET)

    observed = None
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        observed = read_api.space_info(TARGET)
        if str(_field(observed, "sha", "")) == expected_sha and _stage(observed) == "RUNNING":
            break
        time.sleep(10)
    else:
        print(
            json.dumps(
                {
                    "after_sha": str(_field(observed, "sha", "")),
                    "before_sha": before_sha,
                    "expected_sha": expected_sha,
                    "outcome": "UNKNOWN_AFTER_ATTEMPT",
                    "stage": _stage(observed),
                    "target": TARGET,
                },
                sort_keys=True,
            )
        )
        return 3

    if bool(_field(observed, "private", True)):
        print("publisher verification failed: target is private")
        return 3
    if str(_field(observed, "sdk", "")) != "static":
        print("publisher verification failed: target SDK is not static")
        return 3

    live_url = _live_url(observed)
    status, body_bytes = _fetch(live_url, "", expected_sha)
    body = body_bytes.decode("utf-8", "replace")
    if status != 200 or EXPECTED_MARKER not in body:
        print("publisher verification failed: live body identity did not match")
        return 3

    artifact_hashes: dict[str, str] = {}
    for name in READBACK_FILES:
        artifact_status, live_bytes = _fetch(live_url, name, expected_sha)
        local_bytes = _canonical_bytes(Path("site") / name)
        local_hash = hashlib.sha256(local_bytes).hexdigest()
        live_hash = hashlib.sha256(live_bytes).hexdigest()
        if artifact_status != 200 or live_hash != local_hash:
            print(f"publisher verification failed: live {name} did not match the uploaded file")
            return 3
        artifact_hashes[name] = live_hash

    print(
        json.dumps(
            {
                "after_sha": expected_sha,
                "authenticated_write": bool(needs_write),
                "before_sha": before_sha,
                "changed": changed,
                "live_http": status,
                "readback_sha256": artifact_hashes,
                "outcome": "VERIFIED",
                "private": False,
                "sdk": "static",
                "stage": _stage(observed),
                "target": TARGET,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
