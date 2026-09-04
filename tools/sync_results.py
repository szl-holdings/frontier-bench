"""Sync verified receipts into site/results.json for the public bench surface.

Fail-closed: any verification error aborts with exit 1 and writes nothing.
Only MEASURED receipts are exported. Output is UNSIGNED-honest: integrity via
the hash chain, not signer identity.

usage: sync_results.py [receipts_dir] [out_path]
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "verify"))
from verifier import verify  # noqa: E402


def main(receipts_dir="receipts", out="site/results.json"):
    paths = sorted(glob.glob(os.path.join(receipts_dir, "*.json")))
    errors, measured = verify(paths)
    if errors:
        for e in errors:
            print("FAIL", e)
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
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(rows),
        "results": rows,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    print(f"published {len(rows)} measured rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
