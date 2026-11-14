"""Certified-puzzle battery probe — one puzzle per call, fail-closed.

Schema: szl.estate-battery/certified-puzzle-v1

Rules enforced:
- Puzzles are generated fresh from --seed and certified (dual encodings +
  declarative check) BEFORE any request. A puzzle that fails certification
  is recorded as excluded and never sent.
- ONE puzzle per HTTP call. (Batching puzzles into one prompt collapsed
  solver accuracy in Behnamian et al.; we do not batch.)
- No endpoint -> state UNAVAILABLE, no simulated rows, exit 2.
- Verdicts are exact: PASS only when the extracted JSON equals the
  certified solution. ABSTAIN when the model returns an explicit
  cannot-determine answer. FAIL when a full answer is wrong. Transport or
  parse failures are ERROR rows: listed, never silently scored.
- The receipt binds the prompt sha256 of every probed puzzle, the exact
  prompt bytes being the thing the evaluator certifies.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

from .families import canonical_json, generate
from .render import prompt_sha256, render

RECEIPT_SCHEMA = "szl.estate-battery/certified-puzzle-v1"

ABSTAIN_MARKERS = (
    "cannot determine",
    "can't determine",
    "not enough information",
    "insufficient information",
    "undetermined",
)


def _now_iso():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def extract_json_object(text):
    """First balanced JSON object in `text`, or None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def normalize_solution(parsed, spec):
    """Map the parsed answer onto the certified solution shape.

    Returns (normalized, abstained):
      normalized dict comparable to the certified solution, or None when the
      answer is incomplete/unusable; abstained True when the model explicitly
      declined.
    """
    if not isinstance(parsed, dict):
        return None, False
    sol = parsed.get("solution", parsed)
    if sol is None:
        return None, True
    flat = json.dumps(sol).lower()
    if any(marker in flat for marker in ABSTAIN_MARKERS):
        return None, True
    if not isinstance(sol, dict):
        return None, False
    if spec["family"] == "zebra":
        out = {}
        for cat, values in spec["categories"]:
            got = sol.get(cat)
            if not isinstance(got, dict):
                return None, False
            entry = {}
            for v in values:
                if v not in got:
                    return None, False
                if not isinstance(got[v], int):
                    return None, False
                entry[v] = got[v]
            out[cat] = entry
        return out, False
    if spec["family"] == "futoshiki":
        n = spec["size"]
        out = {}
        for r in range(1, n + 1):
            for c in range(1, n + 1):
                key = f"r{r}c{c}"
                val = sol.get(key)
                if not isinstance(val, int):
                    return None, False
                out[key] = val
        return out, False
    return None, False


def chat_completion(endpoint, model, prompt, max_tokens, timeout_s):
    """One OpenAI-compatible chat request. Returns (ok, text_or_error)."""
    url = endpoint.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return False, f"transport: {type(err).__name__}"
    except json.JSONDecodeError:
        return False, "transport: non-JSON body"
    try:
        text = "".join(
            choice["message"].get("content") or ""
            for choice in body["choices"]
        )
    except (KeyError, TypeError):
        return False, "transport: no message content"
    return True, text


def build_receipt(model, state, rows, score, seed, source_revision, model_revision, gpu, errors):
    receipt = {
        "kind": RECEIPT_SCHEMA,
        "model": model,
        "state": state,
        "score": score,
        "seed": seed,
        "ts": _now_iso(),
        "gpu": gpu,
        "source_revision": source_revision,
        "model_revision": model_revision,
        "model_identity_state": "CALLER_DECLARED_NOT_READ_BACK",
        "rows": rows,
        "errors": errors,
    }
    encoded = canonical_json(receipt).encode("utf-8")
    receipt["receipt_sha256"] = hashlib.sha256(encoded).hexdigest()
    return receipt


def write_json_atomic(path, payload):
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def parse_plan(plan):
    """'z4:2,f4:2,f5:1' -> [(family, n, count), ...]"""
    out = []
    for part in plan.split(","):
        part = part.strip()
        if not part:
            continue
        spec, _, count = part.partition(":")
        count = int(count) if count else 1
        if spec.startswith("z"):
            out.append(("zebra", int(spec[1:]), count))
        elif spec.startswith("f"):
            out.append(("futoshiki", int(spec[1:]), count))
        else:
            raise ValueError(f"unknown plan item: {part}")
    if not out:
        raise ValueError("empty plan")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--plan", default="z4:2,f4:2,f5:1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--endpoint-env", default="PUZZLE_ENDPOINT")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--timeout-s", type=int, default=120)
    parser.add_argument("--gpu", default=os.environ.get("PUZZLE_GPU", "unknown"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    endpoint = os.environ.get(args.endpoint_env, "").strip()
    rows = []
    errors = []
    excluded = 0
    attempted = 0
    passed = 0

    # generate + certify all puzzles first: no request before certification
    puzzles = []
    for family, n, count in parse_plan(args.plan):
        for k in range(count):
            puzzle, exc = generate(args.seed * 1000003 + k * 131 + n, family, n)
            if puzzle is None:
                excluded += 1 + exc
                continue
            puzzles.append(puzzle)

    if not endpoint:
        receipt = build_receipt(
            args.model, "UNAVAILABLE", [], "0/0", args.seed,
            args.source_revision, args.model_revision, args.gpu,
            [f"endpoint env var {args.endpoint_env} not set"],
        )
        write_json_atomic(args.out, receipt)
        print(f"state=UNAVAILABLE: {args.endpoint_env} not set (fail-closed)", file=sys.stderr)
        return 2

    for puzzle in puzzles:
        spec = puzzle["spec"]
        text = render(spec)
        row = {
            "id": spec["id"],
            "family": spec["family"],
            "tier": puzzle["tier"],
            "clue_count": puzzle["clue_count"],
            "minimal": puzzle["minimal"],
            "prompt_sha256": prompt_sha256(text),
            "verdict": None,
            "abstained": False,
        }
        attempted += 1
        ok, result = chat_completion(
            endpoint, args.model, text, args.max_tokens, args.timeout_s
        )
        if not ok:
            row["verdict"] = "ERROR"
            row["error"] = result
            errors.append({"id": spec["id"], "error": result})
            rows.append(row)
            continue
        parsed = extract_json_object(result)
        if parsed is None:
            row["verdict"] = "ERROR"
            row["error"] = "parse: no JSON object in completion"
            errors.append({"id": spec["id"], "error": "parse"})
            rows.append(row)
            continue
        normalized, abstained = normalize_solution(parsed, spec)
        row["abstained"] = abstained
        if abstained:
            row["verdict"] = "ABSTAIN"
        elif normalized == puzzle["certified_solution"]:
            row["verdict"] = "PASS"
            passed += 1
        elif normalized is None:
            row["verdict"] = "ERROR"
            row["error"] = "answer shape unusable"
            errors.append({"id": spec["id"], "error": "shape"})
        else:
            row["verdict"] = "FAIL"
        rows.append(row)

    scored = sum(1 for r in rows if r["verdict"] in ("PASS", "FAIL", "ABSTAIN"))
    state = "MEASURED" if rows else "FAILED"
    receipt = build_receipt(
        args.model, state, rows, f"{passed}/{scored}", args.seed,
        args.source_revision, args.model_revision, args.gpu, errors,
    )
    if excluded:
        receipt["excluded_uncertified"] = excluded
    write_json_atomic(args.out, receipt)
    print(
        f"state={state} score={receipt['score']} excluded={excluded} "
        f"errors={len(errors)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
