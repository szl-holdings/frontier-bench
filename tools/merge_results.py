"""Combine generic bench inputs without authenticating measurement claims.

Input digests bind the bytes consumed. Source revisions are caller assertions.
Neither proves receipt admission or origin: all combined rows stay UNVERIFIED.
Authenticated publication must use its separately governed admission boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verify"))
from verifier import check_values, strict_loads, utc_timestamp  # noqa: E402


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
    payload = strict_loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(f"invalid result payload: {path}")
    if type(payload.get("count")) is not int or payload["count"] != len(payload["results"]):
        raise ValueError(f"result count mismatch: {path}")
    utc_timestamp(payload.get("generated_at"))
    return payload, hashlib.sha256(raw).hexdigest()


def build_payloads(
    inputs: dict[str, Path], sources: dict[str, str], generated_at: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    seen_receipts: set[str] = set()
    timestamps = []
    if set(inputs) != set(EXPECTED_PLANES) or set(sources) != set(EXPECTED_PLANES):
        raise ValueError("exact input and source planes required")
    if generated_at is not None:
        utc_timestamp(generated_at)

    for plane in EXPECTED_PLANES:
        payload, payload_sha256 = _load_payload(inputs[plane])
        timestamps.append(payload["generated_at"])
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
                "input_results_sha256": payload_sha256,
                "source_identity": "CALLER_ASSERTED",
            }
        )
        for candidate in payload["results"]:
            if not isinstance(candidate, dict) or candidate.get("plane") != plane:
                raise ValueError(f"{plane} input contains a cross-plane or malformed row")
            if check_values(candidate, plane) or not candidate["metrics"]:
                raise ValueError(f"{plane} input contains invalid row values")
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
            # Do not carry caller-supplied truth/authentication flags forward.
            row = {key: candidate[key] for key in (
                "plane", "machine", "measured_at", "method", "metrics", "receipt"
            )}
            row["status"] = "UNVERIFIED"
            row["source_repository"] = repository
            row["source_revision"] = revision
            rows.append(row)

    if generated_at is None:
        generated_at = max(timestamps, key=utc_timestamp)
    rows.sort(
        key=lambda row: (
            EXPECTED_PLANES.index(row["plane"]),
            utc_timestamp(row["measured_at"]),
            row["receipt"],
        )
    )
    results = {
        "schema": "szl.bench-suite.results/v1",
        "generated_at": generated_at,
        "count": len(rows),
        "results": rows,
        "sources": source_rows,
        "evidence_state": "UNVERIFIED",
    }
    deployment = {
        "schema": "szl.bench-suite.deployment/v1",
        "generated_at": generated_at,
        "target": "SZLHOLDINGS/szl-bench-suite",
        "publisher": "szl-holdings/frontier-bench",
        "sources": source_rows,
        "truth": {
            "receipt_rows": len(rows),
            "results_are_measured_only": False,
            "unsigned_honest": True,
            "authenticity": "UNVERIFIED",
            "receipt_admission": "NOT_CHECKED",
        },
    }
    return results, deployment


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
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
        results, deployment = build_payloads(inputs, sources)
        _write_json_atomic(args.output, results)
        _write_json_atomic(args.deployment_output, deployment)
    except (OSError, ValueError, RecursionError):
        print("result merge blocked: invalid input or output failure")
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
