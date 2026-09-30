"""frontier-bench API — honest engine-bench surface.

Serves generic engine assertions without authenticating their measurement claims.
Nonempty input remains UNVERIFIED, including caller-declared MEASURED rows.
Valid empty input is EMPTY_HONEST; unreadable or malformed input is UNAVAILABLE.
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
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("invalid result collection")
        if type(data.get("count")) is not int or data["count"] != len(data["results"]):
            raise ValueError("result count mismatch")
        if not isinstance(data.get("generated_at"), str) or not data["generated_at"].strip():
            raise ValueError("missing generation time")
        rows = []
        for row in data["results"]:
            if not isinstance(row, dict) or row.get("plane") not in ("engine", "retrieval", "quant"):
                raise ValueError("invalid result row")
            if row["plane"] == PLANE:
                # This endpoint has no authenticated admission boundary. Neither
                # local-file presence, a digest, nor caller truth flags add one.
                assertion = {key: row[key] for key in (
                    "plane", "machine", "measured_at", "method", "metrics", "receipt"
                ) if key in row}
                assertion["status"] = "UNVERIFIED"
                rows.append(assertion)
        return rows, data["generated_at"], "UNVERIFIED" if rows else "EMPTY_HONEST"
    except (OSError, ValueError):
        return [], None, "UNAVAILABLE"


@app.get("/healthz")
def healthz():
    return {"status": "ok", "plane": PLANE, "ts": _now()}


@app.get("/api/results")
def results():
    rows, generated_at, state = load_rows()
    return {
        "plane": PLANE,
        "state": state,
        "authenticity": "UNVERIFIED",
        "results_are_measured_only": False,
        "generated_at": generated_at,
        "count": len(rows),
        "results": rows,
        "served_at": _now(),
    }
