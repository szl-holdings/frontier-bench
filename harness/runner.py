"""
Frontier engine benchmark runner.

Usage:
    python -m harness.runner --config configs/prompts.json --repeats 5 --out results.json

Rules enforced:
- Engines with no configured endpoint are reported as BLOCKED, never simulated.
- Health check must pass before any timed request is issued.
- All timing comes from real HTTP round trips (client.py); nothing is fabricated.
"""
import argparse
import json
import sys
from pathlib import Path

from harness.engine_registry import REGISTRY, available_engines, unavailable_engines
from harness.client import chat_completion, health_check
from harness.metrics import RunSample, summarize


def load_prompts(path: str):
    with open(path) as f:
        data = json.load(f)
    return data["prompts"], data.get("model_by_engine", {})


def run_engine(engine_key, spec, prompts, model, repeats, max_tokens):
    endpoint = spec.resolve_endpoint()
    if not health_check(endpoint):
        return {
            "engine": engine_key,
            "status": "BLOCKED",
            "reason": f"health check failed at {endpoint}",
        }

    samples = []
    for prompt in prompts:
        for _ in range(repeats):
            result = chat_completion(endpoint, model, prompt, max_tokens=max_tokens)
            samples.append(
                RunSample(
                    ok=result.ok,
                    ttft_s=result.ttft_s,
                    total_s=result.total_s,
                    completion_tokens=result.completion_tokens,
                    error=result.error,
                )
            )
    summary = summarize(engine_key, model, samples)
    return {"engine": engine_key, "status": "MEASURED", "summary": summary.to_dict()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--out", default="results.json")
    args = parser.parse_args()

    prompts, model_by_engine = load_prompts(args.config)

    results = []
    for key, spec in unavailable_engines().items():
        results.append({
            "engine": key,
            "status": "BLOCKED",
            "reason": f"endpoint env var {spec.endpoint_env} not set",
            "notes": spec.notes,
        })

    for key, spec in available_engines().items():
        model = model_by_engine.get(key, spec.default_model or "unknown")
        print(f"Running {key} @ {spec.resolve_endpoint()} model={model}", file=sys.stderr)
        results.append(run_engine(key, spec, prompts, model, args.repeats, args.max_tokens))

    Path(args.out).write_text(json.dumps({"results": results}, indent=2))
    print(json.dumps({"results": results}, indent=2))


if __name__ == "__main__":
    main()
