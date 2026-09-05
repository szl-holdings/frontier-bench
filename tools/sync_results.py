"""Sync verified receipts into site/results.json for the public bench surface.

Fail-closed: any verification error aborts with exit 1 and writes nothing.
Only MEASURED receipts are exported. Output is UNSIGNED-honest: integrity via
the hash chain, not signer identity.

usage: sync_results.py [receipts_dir] [out_path] [expected_plane]
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "verify"))
from verifier import verify  # noqa: E402


def main(receipts_dir="receipts", out="site/results.json", expected_plane=None):
    paths = sorted(glob.glob(os.path.join(receipts_dir, "*.json")))
    if not paths:
        print(f"FAIL receipt chain is missing: {receipts_dir}")
        print("aborting: nothing published")
        return 1
    errors, measured = verify(paths)
    if errors:
        for e in errors:
            print("FAIL", e)
        print("aborting: nothing published")
        return 1
    receipts = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            receipt = json.load(f)
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
    for p in measured:
        with open(p, encoding="utf-8") as f:
            r = json.load(f)
        rows.append({
            "plane": r["plane"],
            "machine": r["machine"],
            "measured_at": r["measured_at"],
            "method": r["method"],
            "metrics": r["metrics"],
            "receipt": r["hash"],
        })
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {
        "generated_at": max(timestamps),
        "count": len(rows),
        "results": rows,
    }
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    print(f"published {len(rows)} measured rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
