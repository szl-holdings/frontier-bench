"""Deterministic *proposal-only* security and training authority boundary.

No code path executes scanners, sends content to model APIs, verifies live
credentials, contacts targets or trains weights. Catalog labels are scope
declarations, not vendor certifications or safety probabilities.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

SCHEMA = "szl.security-math.admission.v0"
_SHA = re.compile(r"[0-9a-f]{40}")

# Properties are about the proposed use, not an assertion that tools are safe.
TOOL_FAMILIES = {
    "strix": "ACTIVE_AGENT_PENETRATION_TEST",
    "cloudflare-security-audit-skill": "AGENT_GUIDED_CODE_AUDIT",
    "trivy": "DEPENDENCY_IMAGE_IAC_STATIC_AUDIT",
    "nuclei": "ACTIVE_TEMPLATE_TARGET_SCAN",
    "trufflehog": "SECRET_SCANNING_POSSIBLE_PROVIDER_VERIFICATION",
    "nvidia-skillspector": "AGENT_SKILL_STATIC_AND_OPTIONAL_REMOTE_REVIEW",
    "semgrep": "SOURCE_STATIC_ANALYSIS",
    "codex-security": "MODEL_ASSISTED_SOURCE_AUDIT",
    "clamav": "MALWARE_SIGNATURE_SCAN",
    "claude-code-security-review": "MODEL_ASSISTED_DIFF_AUDIT",
    "bla": "ADVERSARIAL_ARTIFACT_DECOY",
    "microsoft-pyrit": "ACTIVE_AI_RED_TEAM",
}
ACTIVE = frozenset({"strix", "nuclei", "microsoft-pyrit"})
EGRESS_RISK = frozenset({"codex-security", "claude-code-security-review", "nvidia-skillspector"})
BINARY_PROTECTION = frozenset({"bla"})


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def security_proposal(request: dict[str, Any]) -> dict[str, Any]:
    """Classify proposed scanner use, never grant execution permission."""
    if not isinstance(request, dict):
        raise TypeError("REQUEST_MUST_BE_OBJECT")
    candidate_tool = request.get("tool")
    tool = candidate_tool if isinstance(candidate_tool, str) else None
    head = request.get("source_head")
    owned = request.get("target_owned") is True
    offline = request.get("offline") is True
    read_only = request.get("read_only") is True
    external_model = request.get("external_model_review") is True
    credential_check = request.get("verify_live_secrets") is True
    targets_public = request.get("public_or_routable_target") is True
    destination = request.get("destination_kind", "source")
    blockers: list[str] = []
    if tool not in TOOL_FAMILIES:
        blockers.append("UNRECOGNIZED_TOOL")
    if not isinstance(head, str) or not _SHA.fullmatch(head):
        blockers.append("UNPINNED_SOURCE")
    if not owned:
        blockers.append("TARGET_OWNERSHIP_NOT_ESTABLISHED")
    if tool in ACTIVE or targets_public:
        blockers.append("ACTIVE_TARGET_SCOPE_APPROVAL_REQUIRED")
    if external_model or (tool in EGRESS_RISK and not offline):
        blockers.append("PRIVATE_CODE_EGRESS_REVIEW_REQUIRED")
    if credential_check:
        blockers.append("LIVE_CREDENTIAL_CHECK_NOT_AUTHORIZED")
    if tool == "trufflehog" and not offline:
        blockers.append("SECRET_VERIFICATION_NETWORK_UNREVIEWED")
    if tool in BINARY_PROTECTION and destination in ("scientific_evidence", "training", "public_model", "receipt"):
        blockers.append("DECOY_POISONING_CONFLICTS_WITH_EVIDENCE_INTEGRITY")
    if not offline or not read_only:
        blockers.append("OFFLINE_READONLY_BOUNDARY_NOT_MET")
    disposition = "NEEDS_OWNER_APPROVAL" if blockers else "REVIEW_ONLY_CANDIDATE"
    record = {
        "schema": SCHEMA,
        "tool": tool if tool in TOOL_FAMILIES else "UNAVAILABLE",
        "family": TOOL_FAMILIES.get(tool, "UNKNOWN"),
        "source_head": head if isinstance(head, str) and _SHA.fullmatch(head) else None,
        "disposition": disposition,
        "blockers": sorted(set(blockers)),
        "execution_authorized": False,
        "credential_verification_authorized": False,
        "model_training_authorized": False,
        "publish_or_merge_authorized": False,
        "finding_verdict": "NOT_EXECUTED",
        "human_owner_approval": "NOT_ESTABLISHED",
    }
    record["content_sha256"] = _digest(record)
    return record


def validate_finding(raw: dict[str, Any]) -> dict[str, Any]:
    """Treat scanner reports as untrusted leads until independently verified.

    A confirmed result needs a separate reproducible validation receipt and
    a different human/tool verifier. Severity alone is never proof or authority.
    """
    if not isinstance(raw, dict):
        raise TypeError("FINDING_MUST_BE_OBJECT")
    producer = raw.get("producer")
    verifier = raw.get("independent_verifier")
    trace = raw.get("source_trace")
    source_head = raw.get("source_head")
    reproduction = raw.get("reproduction_receipt")
    meaningful = (
        isinstance(producer, str) and 0 < len(producer) <= 80
        and isinstance(verifier, str) and 0 < len(verifier) <= 80
        and producer != verifier
        and isinstance(trace, str) and 0 < len(trace) <= 200
        and isinstance(reproduction, str) and 0 < len(reproduction) <= 200
        and isinstance(source_head, str) and _SHA.fullmatch(source_head)
    )
    if meaningful and raw.get("independently_reproduced") is True:
        verdict = "VALIDATION_CLAIM_UNVERIFIED"
    elif raw.get("independently_disproved") is True and meaningful:
        verdict = "REJECTION_CLAIM_UNVERIFIED"
    else:
        verdict = "NEEDS_VALIDATION"
    result = {
        "schema": "szl.security-math.finding-review.v0",
        "source_sha": source_head if isinstance(source_head, str) and _SHA.fullmatch(source_head) else None,
        "verdict": verdict,
        "requires_human_triage": True,
        "automatically_dismissible": False,
        "external_action_authorized": False,
    }
    return {**result, "content_sha256": _digest(result)}


def training_admission(record: dict[str, Any]) -> dict[str, Any]:
    """Keep published-answer benchmarks and unknown-rights data out of training."""
    if not isinstance(record, dict):
        raise TypeError("TRAINING_RECORD_MUST_BE_OBJECT")
    reasons = []
    if record.get("benchmark_answer_public") is True or record.get("source_family") == "POINDEXTER_PDL":
        reasons.append("PUBLIC_BENCHMARK_ANSWER_QUARANTINE")
    if record.get("license_prohibits_training") is True:
        reasons.append("DO_NOT_TRAIN_SOURCE")
    if record.get("rights_confirmed") is not True:
        reasons.append("RIGHTS_UNVERIFIED")
    if record.get("source_family") != "SZL_ORIGINAL_SYNTHETIC":
        reasons.append("NON_ORIGINAL_SYNTHETIC_SOURCE")
    if not reasons and record.get("independent_math_check") is not True:
        reasons.append("INDEPENDENT_MATH_VALIDATION_MISSING")
    # Even perfect local synthetic training data does not authorize running GPUs.
    return {
        "schema": "szl.security-math.training-admission.v0",
        "disposition": "QUARANTINE_EVAL_ONLY" if any(
            x in reasons for x in ("PUBLIC_BENCHMARK_ANSWER_QUARANTINE", "DO_NOT_TRAIN_SOURCE")
        ) else ("SYNTHETIC_TRAINING_CANDIDATE" if not reasons else "HOLD"),
        "reasons": sorted(reasons),
        "model_training_executed": False,
        "gpu_budget_approval": "NOT_OBSERVED",
        "external_publication_authorized": False,
    }
