"""Frontier engine benchmark runner with an explicit raw-run contract.

Rules enforced:
- Engines with no configured endpoint are reported as BLOCKED, never simulated.
- Health check must pass before any timed request is issued.
- Every executed engine has a caller-declared exact model revision.
- Workload bytes, prompt rows, evaluator revision, seed, and generation settings
  are bound by one deterministic contract digest.
- Raw runs are DESCRIPTIVE_ONLY and never publication-eligible.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

from harness.engine_registry import REGISTRY, available_engines, unavailable_engines
from harness.client import chat_completion, health_check
from harness.metrics import RunSample, summarize
from harness.run_contract import (
    RAW_RUN_SCHEMA,
    build_run_contract,
    failure_summary,
    load_workload,
    parse_model_revisions,
)


def run_engine(engine_key, spec, prompts, model, repeats, max_tokens, seed=0):
    if (
        type(repeats) is not int
        or repeats <= 0
        or type(max_tokens) is not int
        or max_tokens <= 0
        or type(seed) is not int
        or seed < 0
        or seed > 2**32 - 1
        or not isinstance(prompts, (list, tuple))
        or not prompts
        or not all(isinstance(prompt, str) and prompt.strip() for prompt in prompts)
    ):
        return {"engine": engine_key, "status": "INVALID", "reason": "invalid request plan"}
    endpoint = spec.resolve_endpoint()
    if not health_check(endpoint):
        return {
            "engine": engine_key,
            "status": "BLOCKED",
            "reason": "health check failed",
        }

    samples = []
    for prompt in prompts:
        for _ in range(repeats):
            result = chat_completion(
                endpoint,
                model,
                prompt,
                max_tokens=max_tokens,
                seed=seed,
            )
            samples.append(
                RunSample(
                    ok=result.ok,
                    ttft_s=result.ttft_s,
                    total_s=result.total_s,
                    completion_tokens=result.completion_tokens,
                    error=result.error,
                )
            )
    try:
        summary = summarize(engine_key, model, samples)
    except ValueError:
        return {"engine": engine_key, "status": "INVALID", "reason": "invalid sample values"}
    status = "MEASURED" if summary.n_success > 0 and summary.p50_total_ms is not None else "FAILED"
    return {"engine": engine_key, "status": status, "summary": summary.to_dict()}


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--model-revision", action="append", default=[])
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--out", default="results.json")
    args = parser.parse_args(argv)

    try:
        workload = load_workload(args.config, REGISTRY)
        configured = available_engines()
        unavailable = unavailable_engines()
        model_revisions = parse_model_revisions(args.model_revision, configured)
        contract = build_run_contract(
            workload,
            source_revision=args.source_revision,
            configured_engines=configured,
            model_revisions=model_revisions,
            repeats=args.repeats,
            max_tokens=args.max_tokens,
            seed=args.seed,
        )
    except (OSError, ValueError, RecursionError) as error:
        print(f"benchmark plan rejected: {error}", file=sys.stderr)
        return 2

    results = []
    for key, spec in unavailable.items():
        results.append(
            {
                "engine": key,
                "status": "BLOCKED",
                "reason": f"endpoint env var {spec.endpoint_env} not set",
                "notes": spec.notes,
            }
        )

    for key, spec in configured.items():
        model = workload.model_by_engine[key]
        print(f"Running {key} model={model}", file=sys.stderr)
        result = run_engine(
            key,
            spec,
            workload.prompt_texts,
            model,
            args.repeats,
            args.max_tokens,
            args.seed,
        )
        result["model_revision"] = model_revisions[key]
        result["model_identity_state"] = "CALLER_DECLARED_NOT_READ_BACK"
        result["contract_sha256"] = contract["contract_sha256"]
        results.append(result)

    try:
        payload = {
            "schema": RAW_RUN_SCHEMA,
            "observed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "run_contract": contract,
            "failure_summary": failure_summary(results),
            "results": results,
        }
        _write_json_atomic(Path(args.out), payload)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
        print(f"benchmark output blocked: {type(error).__name__}", file=sys.stderr)
        return 2

    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
