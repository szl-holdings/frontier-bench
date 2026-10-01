"""Sync verified receipts into site/results.json for the public bench surface.

Fail-closed: any verification error aborts with exit 1 and writes nothing.
Only receipts declaring MEASURED are exported, explicitly UNVERIFIED. A
self-hash chain is an integrity check, not evidence of authentic measurement.

usage: sync_results.py [receipts_dir] [out_path] [expected_plane]
"""
import glob
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "verify"))
from verifier import verify_snapshots, utc_timestamp  # noqa: E402


def _write_atomic(out, payload):
    path = Path(out)
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(receipts_dir="receipts", out="site/results.json", expected_plane=None):
    paths = sorted(glob.glob(os.path.join(receipts_dir, "*.json")))
    if not paths:
        print(f"FAIL receipt chain is missing: {receipts_dir}")
        print("aborting: nothing published")
        return 1
    errors, snapshots = verify_snapshots(paths)
    if errors:
        for e in errors:
            print("FAIL", e)
        print("aborting: nothing published")
        return 1
    receipts = []
    for p, receipt in snapshots:
        receipts.append(receipt)
        if expected_plane:
            if receipt.get("plane") != expected_plane:
                print(
                    f"FAIL {p}: expected plane {expected_plane!r}, "
                    f"got {receipt.get('plane')!r}"
                )
                print("aborting: nothing published")
                return 1
    timestamps = [receipt.get("measured_at") for receipt in receipts]
    if not all(isinstance(value, str) and value for value in timestamps):
        print("FAIL receipt chain contains an invalid measured_at value")
        print("aborting: nothing published")
        return 1
    rows = []
    for _, r in snapshots:
        if r["status"] != "MEASURED":
            continue
        rows.append({
            "status": "UNVERIFIED",
            "declared_status": "MEASURED",
            "plane": r["plane"],
            "machine": r["machine"],
            "measured_at": r["measured_at"],
            "method": r["method"],
            "metrics": r["metrics"],
            "receipt": r["hash"],
        })
    payload = {
        "generated_at": max(timestamps, key=utc_timestamp),
        "count": len(rows),
        "results": rows,
        "provenance": {
            "chain_integrity": "SELF_HASH_CONSISTENT",
            "authenticity": "UNVERIFIED",
            "results_are_measured_only": False,
        },
    }
    try:
        _write_atomic(out, payload)
    except (OSError, ValueError):
        print("FAIL output write failed; nothing replaced")
        return 1
    print(f"exported {len(rows)} unauthenticated assertion rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
