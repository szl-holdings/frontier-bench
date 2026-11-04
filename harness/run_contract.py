"""Fail-closed contract for raw benchmark executions.

This module binds what the runner was asked to execute.  The model revision is
still a caller declaration because the generic OpenAI-compatible endpoint does
not expose a portable revision readback.  A raw run is therefore never eligible
for publication by this contract alone.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Iterable, Mapping


WORKLOAD_SCHEMA = "szl.frontier-bench.workload/v1"
RUN_CONTRACT_SCHEMA = "szl.frontier-bench.run-contract/v1"
RAW_RUN_SCHEMA = "szl.frontier-bench.raw-run/v1"
EVALUATOR_REPOSITORY = "szl-holdings/frontier-bench"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")


@dataclass(frozen=True)
class Workload:
    identity: Mapping[str, str]
    prompts: tuple[Mapping[str, str], ...]
    prompt_texts: tuple[str, ...]
    model_by_engine: Mapping[str, str]
    config_sha256: str


def canonical_sha256(value) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_tree(value) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for child in value.values():
            _finite_tree(child)
    elif isinstance(value, list):
        for child in value:
            _finite_tree(child)


def load_workload(path: str | Path, known_engines: Iterable[str]) -> Workload:
    raw = Path(path).read_bytes()
    if not raw or len(raw) > 1024 * 1024:
        raise ValueError("workload must be a nonempty JSON file no larger than 1 MiB")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("workload must be strict UTF-8 JSON") from error
    _finite_tree(payload)
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "workload",
        "prompts",
        "model_by_engine",
    }:
        raise ValueError("workload must contain only schema, workload, prompts, and model_by_engine")
    if payload["schema"] != WORKLOAD_SCHEMA:
        raise ValueError("unsupported workload schema")

    identity = payload["workload"]
    if not isinstance(identity, dict) or set(identity) != {
        "id",
        "split",
        "license",
        "content_sha256",
    }:
        raise ValueError("invalid workload identity")
    if not isinstance(identity["id"], str) or not ID_RE.fullmatch(identity["id"]):
        raise ValueError("invalid workload id")
    for field in ("split", "license"):
        if not isinstance(identity[field], str) or not identity[field].strip():
            raise ValueError(f"invalid workload {field}")

    prompts = payload["prompts"]
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts must be a nonempty array")
    normalized_prompts = []
    prompt_ids = set()
    prompt_texts = set()
    for prompt in prompts:
        if not isinstance(prompt, dict) or set(prompt) != {"id", "text"}:
            raise ValueError("each prompt must contain only id and text")
        prompt_id = prompt["id"]
        text = prompt["text"]
        if not isinstance(prompt_id, str) or not ID_RE.fullmatch(prompt_id):
            raise ValueError("invalid prompt id")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("invalid prompt text")
        if prompt_id in prompt_ids or text in prompt_texts:
            raise ValueError("prompt ids and texts must be unique")
        prompt_ids.add(prompt_id)
        prompt_texts.add(text)
        normalized_prompts.append({"id": prompt_id, "text": text})

    content_sha256 = canonical_sha256(normalized_prompts)
    if identity["content_sha256"] != content_sha256:
        raise ValueError("workload content digest mismatch")

    models = payload["model_by_engine"]
    engine_names = set(known_engines)
    if not isinstance(models, dict) or set(models) != engine_names:
        raise ValueError("model_by_engine must cover the exact engine registry")
    if not all(isinstance(value, str) and value.strip() for value in models.values()):
        raise ValueError("model ids must be nonempty strings")

    return Workload(
        identity=dict(identity),
        prompts=tuple(normalized_prompts),
        prompt_texts=tuple(item["text"] for item in normalized_prompts),
        model_by_engine=dict(models),
        config_sha256=hashlib.sha256(raw).hexdigest(),
    )


def parse_model_revisions(
    values: Iterable[str], configured_engines: Iterable[str]
) -> dict[str, str]:
    revisions = {}
    for value in values:
        engine, separator, revision = value.partition("=")
        if not separator or not engine or not SHA40_RE.fullmatch(revision):
            raise ValueError("model revisions must use ENGINE=lowercase-40-character-SHA")
        if engine in revisions:
            raise ValueError(f"duplicate model revision for {engine}")
        revisions[engine] = revision
    expected = set(configured_engines)
    if set(revisions) != expected:
        missing = sorted(expected - set(revisions))
        extra = sorted(set(revisions) - expected)
        raise ValueError(f"model revisions must cover configured engines; missing={missing}, extra={extra}")
    return revisions


def build_run_contract(
    workload: Workload,
    *,
    source_revision: str,
    configured_engines: Iterable[str],
    model_revisions: Mapping[str, str],
    repeats: int,
    max_tokens: int,
    seed: int,
) -> dict:
    engine_names = sorted(configured_engines)
    if not SHA40_RE.fullmatch(source_revision):
        raise ValueError("source revision must be a lowercase 40-character SHA")
    if type(repeats) is not int or repeats <= 0:
        raise ValueError("repeats must be a positive integer")
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ValueError("max_tokens must be a positive integer")
    if type(seed) is not int or seed < 0 or seed > 2**32 - 1:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if set(model_revisions) != set(engine_names):
        raise ValueError("model revision coverage changed after validation")
    if any(not SHA40_RE.fullmatch(value) for value in model_revisions.values()):
        raise ValueError("invalid model revision")

    contract = {
        "schema": RUN_CONTRACT_SCHEMA,
        "evaluator": {
            "repository": EVALUATOR_REPOSITORY,
            "revision": source_revision,
            "identity_state": "CALLER_DECLARED",
        },
        "workload": {
            **workload.identity,
            "config_sha256": workload.config_sha256,
            "prompt_count": len(workload.prompts),
        },
        "generation": {
            "repeats": repeats,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "stream": True,
            "seed": seed,
        },
        "models": [
            {
                "engine": engine,
                "id": workload.model_by_engine[engine],
                "revision": model_revisions[engine],
                "identity_state": "CALLER_DECLARED_NOT_READ_BACK",
            }
            for engine in engine_names
        ],
        "evaluation_policy": {
            "kind": "DESCRIPTIVE_ONLY",
            "promotion_eligible": False,
            "thresholds": None,
            "tolerance": None,
        },
    }
    contract["contract_sha256"] = canonical_sha256(contract)
    return contract


def failure_summary(results: Iterable[Mapping]) -> dict[str, int]:
    counts = {status: 0 for status in ("MEASURED", "FAILED", "BLOCKED", "INVALID")}
    requests_total = requests_succeeded = requests_failed = 0
    result_count = 0
    for result in results:
        result_count += 1
        status = result.get("status")
        if status not in counts:
            raise ValueError("unexpected result status")
        counts[status] += 1
        summary = result.get("summary")
        if summary is not None:
            if not isinstance(summary, Mapping):
                raise ValueError("invalid result summary")
            values = [summary.get(key) for key in ("n_requests", "n_success", "n_failed")]
            if any(type(value) is not int or value < 0 for value in values):
                raise ValueError("invalid request counts")
            if values[1] + values[2] != values[0]:
                raise ValueError("request counts do not reconcile")
            requests_total += values[0]
            requests_succeeded += values[1]
            requests_failed += values[2]
    return {
        "engines_total": result_count,
        "engines_measured": counts["MEASURED"],
        "engines_failed": counts["FAILED"],
        "engines_blocked": counts["BLOCKED"],
        "engines_invalid": counts["INVALID"],
        "requests_total": requests_total,
        "requests_succeeded": requests_succeeded,
        "requests_failed": requests_failed,
    }
