"""Open-answer, original synthetic development examples (NOT benchmark scores).

This generator does not fetch Poindexter material, execute a model, train,
publish, or produce blind held-out evaluations.
"""
from __future__ import annotations

import hashlib
import json
from .admission import training_admission
from .surface_groups import (
    cyclic_group, quaternion_group, symmetric_group_three,
    direct_product_group, independently_checked_receipt,
)


def development_cases() -> dict:
    """Return exact labeled *development* examples, never blind model results."""
    c2 = cyclic_group(2)
    c3 = cyclic_group(3)
    s3 = symmetric_group_three()
    groups = (
        cyclic_group(1),
        c2,
        c3,
        cyclic_group(4),
        cyclic_group(6),
        s3,
        quaternion_group(),
        direct_product_group(c2, c3),
        # S3 x C2 is covered separately by factorization tests; its full
        # direct enumeration exceeds the default research ceiling.
    )
    rows = []
    for group in groups:
        receipt = independently_checked_receipt(group, 5)
        rights = training_admission({
            "source_family": "SZL_ORIGINAL_SYNTHETIC",
            "rights_confirmed": True,
            "benchmark_answer_public": False,
            "independent_math_check": True,
        })
        rows.append({
            "id": "szl-surface-orbit-v0-" + group.name,
            "source_origin": "SZL_ORIGINAL_SYNTHETIC",
            "split": "OPEN_DEVELOPMENT_ONLY_NOT_BLIND_EVAL",
            "nonorientable_genus": 5,
            "group": group.name,
            # C2 x C3 is isomorphic to C6 and must never enter a different split.
            "isomorphism_class": "C6" if group.name == "C2_C3" else group.name,
            "multiplication_table": [list(x) for x in group.multiplication],
            "identity": group.identity,
            "question": "Count simultaneous conjugacy orbits of tuples "
                        "(x_1,...,x_5) in G^5 with x_1^2...x_5^2=e, "
                        "using the indexed multiplication table and "
                        "independently verify by a second algorithm.",
            "reference_orbit_count": receipt["burnside_orbits"],
            "synthetic_receipt_sha256": receipt["content_sha256"],
            "training_disposition": rights["disposition"],
            "training_executed": False,
            "benchmark_score_eligible": False,
            "github_or_hub_model_revision": None,
        })
    result = {
        "schema": "szl.synthetic.surface-orbit-development/v0",
        "rights": "ORIGINAL_SOURCES_CONSTRUCTED_FROM_STANDARD_FINITE_GROUPS",
        "public_benchmark_content_included": False,
        "open_answer_dev_only": True,
        "model_evaluations_run": 0,
        "training_runs": 0,
        "cases": rows,
    }
    result["content_sha256"] = hashlib.sha256(json.dumps(
        result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return result
