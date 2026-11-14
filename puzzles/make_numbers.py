"""Scripted numbers: aggregate certified-puzzle receipts into a table.

Honest nulls: a tier with no measured rows prints `null (no measured rows)`
and never a zero. Numbers are read from saved receipts only; nothing is
computed from memory or estimation.
"""
from __future__ import annotations

import argparse
import json


def load_rows(receipt_paths):
    rows = []
    for path in receipt_paths:
        with open(path, "r", encoding="utf-8") as handle:
            receipt = json.load(handle)
        if receipt.get("kind") != "szl.estate-battery/certified-puzzle-v1":
            raise SystemExit(f"{path}: not a certified-puzzle receipt")
        if receipt.get("state") != "MEASURED":
            continue
        rows.extend(receipt.get("rows", []))
    return rows


def per_tier(rows):
    tiers = {}
    for row in rows:
        tier = row.get("tier", "?")
        verdict = row.get("verdict")
        bucket = tiers.setdefault(
            tier, {"PASS": 0, "FAIL": 0, "ABSTAIN": 0, "ERROR": 0}
        )
        if verdict in bucket:
            bucket[verdict] += 1
    return tiers


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipts", nargs="+")
    args = parser.parse_args(argv)
    rows = load_rows(args.receipts)
    tiers = per_tier(rows)
    print(f"tiers={len(tiers)} measured_rows={len(rows)}")
    for tier in sorted(tiers):
        b = tiers[tier]
        scored = b["PASS"] + b["FAIL"] + b["ABSTAIN"]
        if scored == 0:
            accuracy = "null (no measured rows)"
        else:
            accuracy = f"{b['PASS']}/{scored}"
        abstain_note = f" abstain={b['ABSTAIN']}" if b["ABSTAIN"] else ""
        print(
            f"  {tier}: accuracy={accuracy} fail={b['FAIL']} "
            f"error={b['ERROR']}{abstain_note}"
        )
    if not tiers:
        print("  null (no measured rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
