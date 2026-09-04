"""Merge independently verified bench planes into one source-bound payload.

The canonical ``sync_results.py`` program admits every plane's receipt data.
This module combines those already-verified JSON outputs, rejects cross-plane
or duplicate receipts, and records the exact Git revision used for each input
repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


EXPECTED_PLANES = ("engine", "retrieval", "quant")
EXPECTED_REPOSITORIES = {
    "engine": "szl-holdings/frontier-bench",
    "retrieval": "szl-holdings/retrieval-bench",
    "quant": "szl-holdings/quant-curve",
}


def _parse_mapping(values: Iterable[str], option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item:
            raise ValueError(f"{option} must use PLANE=VALUE: {value!r}")
        if key in parsed:
            raise ValueError(f"duplicate {option} plane: {key}")
        parsed[key] = item
    missing = sorted(set(EXPECTED_PLANES) - set(parsed))
    extra = sorted(set(parsed) - set(EXPECTED_PLANES))
    if missing or extra:
        raise ValueError(
            f"{option} planes must be {EXPECTED_PLANES}; missing={missing}, extra={extra}"
        )
    return parsed


def _parse_source(value: str) -> tuple[str, str]:
    repository, separator, revision = value.rpartition("@")
    if not separator or repository.count("/") != 1:
        raise ValueError(f"source must use OWNER/REPO@SHA: {value!r}")
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise ValueError(f"source revision must be a lowercase 40-character SHA: {revision!r}")
    return repository, revision


def _load_payload(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(f"invalid verified-result payload: {path}")
    if payload.get("count") != len(payload["results"]):
        raise ValueError(f"verified-result count mismatch: {path}")
    if not isinstance(payload.get("generated_at"), str) or not payload["generated_at"]:
        raise ValueError(f"verified-result timestamp is missing: {path}")
    return payload, hashlib.sha256(raw).hexdigest()


def build_payloads(
    inputs: dict[str, Path], sources: dict[str, str], generated_at: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    seen_receipts: set[str] = set()

    for plane in EXPECTED_PLANES:
        payload, payload_sha256 = _load_payload(inputs[plane])
        repository, revision = _parse_source(sources[plane])
        if repository != EXPECTED_REPOSITORIES[plane]:
            raise ValueError(
                f"{plane} source must be {EXPECTED_REPOSITORIES[plane]}, got {repository}"
            )
        source_rows.append(
            {
                "plane": plane,
                "repository": repository,
                "revision": revision,
                "verified_results_sha256": payload_sha256,
            }
        )
        for candidate in payload["results"]:
            if not isinstance(candidate, dict) or candidate.get("plane") != plane:
                raise ValueError(f"{plane} input contains a cross-plane or malformed row")
            receipt = candidate.get("receipt")
            if (
                not isinstance(receipt, str)
                or len(receipt) != 64
                or any(char not in "0123456789abcdef" for char in receipt)
            ):
                raise ValueError(f"{plane} input contains an invalid receipt digest")
            if receipt in seen_receipts:
                raise ValueError(f"duplicate receipt across planes: {receipt}")
            seen_receipts.add(receipt)
            row = dict(candidate)
            row["source_repository"] = repository
            row["source_revision"] = revision
            rows.append(row)

    rows.sort(
        key=lambda row: (
            EXPECTED_PLANES.index(row["plane"]),
            str(row.get("measured_at", "")),
            row["receipt"],
        )
    )
    results = {
        "schema": "szl.bench-suite.results/v1",
        "generated_at": generated_at,
        "count": len(rows),
        "results": rows,
        "sources": source_rows,
    }
    deployment = {
        "schema": "szl.bench-suite.deployment/v1",
        "generated_at": generated_at,
        "target": "betterwithage/szl-bench-suite",
        "publisher": "szl-holdings/frontier-bench",
        "sources": source_rows,
        "truth": {
            "receipt_rows": len(rows),
            "results_are_measured_only": True,
            "unsigned_honest": True,
        },
    }
    return results, deployment


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--deployment-output", required=True, type=Path)
    parser.add_argument("--input", action="append", default=[], dest="inputs")
    parser.add_argument("--source", action="append", default=[], dest="sources")
    args = parser.parse_args()

    try:
        inputs = {
            plane: Path(value)
            for plane, value in _parse_mapping(args.inputs, "input").items()
        }
        sources = _parse_mapping(args.sources, "source")
        generated_at = max(_load_payload(path)[0]["generated_at"] for path in inputs.values())
        results, deployment = build_payloads(inputs, sources, generated_at)
        _write_json_atomic(args.output, results)
        _write_json_atomic(args.deployment_output, deployment)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"publisher merge blocked: {type(error).__name__}: {error}")
        return 2

    print(
        json.dumps(
            {
                "deployment_output": str(args.deployment_output),
                "output": str(args.output),
                "result_count": results["count"],
                "source_count": len(results["sources"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
