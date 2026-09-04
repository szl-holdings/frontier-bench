"""Publish the canonical static bench bundle with compare-and-set semantics."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


TARGET = "betterwithage/szl-bench-suite"
LIVE_URL = "https://betterwithage-szl-bench-suite.hf.space/"
EXPECTED_MARKER = "SZL Bench Suite"
READBACK_FILES = ("results.json", "deployment.json")


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _stage(info: Any) -> str:
    runtime = _field(info, "runtime", {})
    return str(_field(runtime, "stage", "UNKNOWN") or "UNKNOWN").upper()


def _fetch(path: str, revision: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        f"{LIVE_URL}{path}?source_verify={revision}",
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


def _bundle_matches(revision: str) -> bool:
    files = sorted(path for path in Path("site").rglob("*") if path.is_file())
    if not files or any(path.is_symlink() for path in files):
        raise ValueError("site bundle must contain regular files and no symlinks")
    for local_path in files:
        relative = local_path.relative_to("site").as_posix()
        if _repo_file(relative, revision) != local_path.read_bytes():
            return False
    return True


def main() -> int:
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("publisher blocked: HF_TOKEN is unavailable")
        return 2

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    identity = api.whoami()
    if identity.get("name") != "betterwithage":
        print("publisher blocked: credential identity is not betterwithage")
        return 2

    before = api.space_info(TARGET)
    before_sha = str(_field(before, "sha", ""))
    if len(before_sha) != 40:
        print("publisher blocked: provider did not return an exact pre-write revision")
        return 2

    changed = not _bundle_matches(before_sha)
    if changed:
        commit = api.upload_folder(
            folder_path="site",
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
        if _stage(before) != "RUNNING":
            api.restart_space(TARGET)

    observed = None
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        observed = api.space_info(TARGET)
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

    status, body_bytes = _fetch("", expected_sha)
    body = body_bytes.decode("utf-8", "replace")
    if status != 200 or EXPECTED_MARKER not in body:
        print("publisher verification failed: live body identity did not match")
        return 3

    artifact_hashes: dict[str, str] = {}
    for name in READBACK_FILES:
        artifact_status, live_bytes = _fetch(name, expected_sha)
        local_bytes = (Path("site") / name).read_bytes()
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
