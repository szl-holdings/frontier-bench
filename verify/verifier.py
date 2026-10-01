"""Receipt verifier for the SZL bench planes.

Schema (v1):
  plane        str   one of "engine" | "retrieval" | "quant" | "calibration"
  status       str   one of MEASURED | BLOCKED | INVALID | FAILED | PROMOTED
  machine      obj   {cpu: str, ram_gb: number, gpu: str}
  measured_at  str   ISO-8601 date or datetime (UTC)
  method       str   free text naming harness + version
  metrics      obj   plane-specific measured values (empty for non-MEASURED)
  prev_hash    str   sha256 hex of previous receipt, or 64 zeros for genesis
  hash         str   sha256 of canonical JSON of this receipt without "hash"

Rules: fail closed. Any malformed receipt, bad hash, or broken chain exits 1.
MEASURED is a producer assertion, not an authenticated measurement. This module
checks schema and self-hash chain consistency only, not origin or authenticity.
"""
import hashlib
import datetime
import json
import math
import os
import re
import sys

STATUSES = {"MEASURED", "BLOCKED", "INVALID", "FAILED", "PROMOTED"}
PLANES = {"engine", "retrieval", "quant", "calibration"}
GENESIS = "0" * 64
REQUIRED = ("plane", "status", "machine", "measured_at", "method", "metrics", "prev_hash", "hash")


def digest(receipt):
    body = {k: v for k, v in receipt.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def utc_timestamp(value):
    """Parse the v1 UTC date/datetime contract, rejecting naive datetimes."""
    if not isinstance(value, str):
        raise ValueError("invalid UTC timestamp")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.datetime.combine(
            datetime.date.fromisoformat(value), datetime.time(), datetime.timezone.utc
        )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise ValueError("invalid UTC timestamp")
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _check_finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for child in value.values():
            _check_finite_json(child)
    elif isinstance(value, list):
        for child in value:
            _check_finite_json(child)


def strict_loads(raw):
    value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    _check_finite_json(value)  # also catches exponent overflow, e.g. 1e999
    return value


def check_values(r, i):
    """Validate common receipt/row values without making an origin claim."""
    errs = []
    m = r.get("machine")
    if (not isinstance(m, dict)
            or not all(isinstance(m.get(k), str) and m[k].strip() for k in ("cpu", "gpu"))
            or not finite_number(m.get("ram_gb")) or m["ram_gb"] < 0):
        errs.append(f"[{i}] invalid machine")
    try:
        utc_timestamp(r.get("measured_at"))
    except (ValueError, OverflowError):
        errs.append(f"[{i}] invalid measured_at")
    if not isinstance(r.get("method"), str) or not r["method"].strip():
        errs.append(f"[{i}] invalid method")
    metrics = r.get("metrics")
    if (not isinstance(metrics, dict)
            or not all(isinstance(k, str) and k.strip() and finite_number(v) for k, v in metrics.items())):
        errs.append(f"[{i}] metrics must be finite numeric values")
    return errs


def check_receipt(r, i):
    if not isinstance(r, dict):
        return [f"[{i}] receipt must be an object"]
    errs = [f"[{i}] missing field: {f}" for f in REQUIRED if f not in r]
    if errs:
        return errs
    if not isinstance(r["plane"], str) or r["plane"] not in PLANES:
        errs.append(f"[{i}] unknown plane")
    if not isinstance(r["status"], str) or r["status"] not in STATUSES:
        errs.append(f"[{i}] unknown status")
    errs.extend(check_values(r, i))
    if r["status"] == "MEASURED" and not r["metrics"]:
        errs.append(f"[{i}] MEASURED with empty metrics")
    if r["status"] != "MEASURED" and r["metrics"] != {}:
        errs.append(f"[{i}] non-MEASURED metrics must be empty")
    for field in ("hash", "prev_hash"):
        if not isinstance(r[field], str) or not re.fullmatch(r"[0-9a-f]{64}", r[field]):
            errs.append(f"[{i}] invalid {field}")
    try:
        if digest(r) != r["hash"]:
            errs.append(f"[{i}] hash mismatch (recomputed != declared)")
    except (TypeError, ValueError, OverflowError, RecursionError):
        errs.append(f"[{i}] invalid canonical JSON")
    return errs


def verify_snapshots(paths):
    """Return parsed snapshots of the bytes checked; return none on any error.

    Consumers must use these snapshots, never reopen a verified path. A valid
    self-hash chain is unauthenticated, even when it declares MEASURED.
    """
    if isinstance(paths, (str, bytes)):
        return ["receipt paths must be an iterable collection"], []
    try:
        paths = list(paths)
    except TypeError:
        return ["receipt paths must be an iterable collection"], []
    if not paths:
        return ["empty receipt chain"], []
    all_errs, snapshots, prev = [], [], GENESIS
    for i, p in enumerate(paths):
        if not isinstance(p, (str, bytes, os.PathLike)):
            all_errs.append(f"[{i}] receipt path must be a filesystem path")
            continue
        try:
            with open(p, "rb") as f:
                r = strict_loads(f.read())
        except (OSError, TypeError, ValueError, RecursionError):
            all_errs.append(f"[{i}] unreadable or invalid JSON")
            continue
        all_errs += check_receipt(r, i)
        if not isinstance(r, dict):
            continue
        if r.get("prev_hash", prev) != prev:
            all_errs.append(f"[{i}] chain break (prev_hash mismatch)")
        prev = r.get("hash", prev)
        snapshots.append((p, r))
    return all_errs, [] if all_errs else snapshots


def verify(paths):
    """Compatibility API: paths declaring MEASURED, not publication approval."""
    errors, snapshots = verify_snapshots(paths)
    return errors, [p for p, r in snapshots if r["status"] == "MEASURED"]


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("usage: verifier.py <receipt.json> [...]")
        sys.exit(2)
    errors, ok = verify(args)
    for e in errors:
        print("FAIL", e)
    print(f"checked={len(args)} declared_measured={len(ok)} authenticity=UNVERIFIED errors={len(errors)}")
    sys.exit(1 if errors else 0)
