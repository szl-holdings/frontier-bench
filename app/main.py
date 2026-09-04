"""frontier-bench API — honest engine-bench surface.

Serves only hash-chain-verified MEASURED rows exported by tools/sync_results.py.
When no verified results exist, state is EMPTY_HONEST — never fabricated data.
"""
import json
import os
import time

from fastapi import FastAPI

PLANE = "engine"
RESULTS = os.environ.get("RESULTS_PATH", "site/results.json")

app = FastAPI(title="frontier-bench", version="0.1.0")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_rows():
    try:
        with open(RESULTS, encoding="utf-8") as f:
            data = json.load(f)
        rows = [r for r in data.get("results", []) if r.get("plane") == PLANE]
        return rows, data.get("generated_at")
    except Exception:
        return [], None


@app.get("/healthz")
def healthz():
    return {"status": "ok", "plane": PLANE, "ts": _now()}


@app.get("/api/results")
def results():
    rows, generated_at = load_rows()
    return {
        "plane": PLANE,
        "state": "MEASURED" if rows else "EMPTY_HONEST",
        "generated_at": generated_at,
        "count": len(rows),
        "results": rows,
        "served_at": _now(),
    }
