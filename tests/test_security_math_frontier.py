"""Bounded *synthetic* group-counting and security-admission tests."""
import unittest

from research.security_math_v0.admission import (
    TOOL_FAMILIES, security_proposal, training_admission, validate_finding,
)
from research.security_math_v0.surface_groups import (
    FiniteGroup, InvalidFiniteGroup, CostCeilingExceeded, cyclic_group,
    symmetric_group_three, quaternion_group, direct_product_group,
    count_orbits_burnside, count_orbits_direct, abelian_reference_formula,
    independently_checked_receipt, verified_product_count,
)

HEAD = "a" * 40


class SurfaceMathTests(unittest.TestCase):
    def test_cyclic_one(self):
        g = cyclic_group(1)
        self.assertEqual((count_orbits_burnside(g), count_orbits_direct(g)), (1, 1))

    def test_cyclic_two(self):
        g = cyclic_group(2)
        self.assertEqual(count_orbits_burnside(g), 32)
        self.assertEqual(count_orbits_direct(g), 32)
        self.assertEqual(abelian_reference_formula(g), 32)

    def test_cyclic_three(self):
        g = cyclic_group(3)
        self.assertEqual(count_orbits_burnside(g), 81)
        self.assertEqual(count_orbits_direct(g), 81)

    def test_cyclic_four(self):
        self.assertEqual(independently_checked_receipt(cyclic_group(4))["burnside_orbits"], 512)

    def test_nonabelian_s3_independent_algorithms(self):
        group = symmetric_group_three()
        self.assertFalse(group.is_abelian())
        self.assertEqual(count_orbits_burnside(group), 502)
        self.assertEqual(count_orbits_direct(group), 502)

    def test_quaternion_eight_independent_methods(self):
        group = quaternion_group()
        self.assertFalse(group.is_abelian())
        result = independently_checked_receipt(group, 5)
        self.assertEqual(result["burnside_orbits"], 4352)
        self.assertEqual(result["independent_direct_orbits"], 4352)
        self.assertFalse(result["benchmark_answer_verified"])

    def test_nonabelian_product_factorization(self):
        # A nonabelian direct factor tests the componentwise conjugation rule.
        left, right = symmetric_group_three(), cyclic_group(2)
        actual = count_orbits_burnside(direct_product_group(left, right), 5)
        self.assertEqual(actual, 502 * 32)
        self.assertEqual(
            actual,
            verified_product_count((left, right), 5)["orbit_count"],
        )

    def test_nonabelian_not_abelian_formula(self):
        with self.assertRaises(InvalidFiniteGroup):
            abelian_reference_formula(symmetric_group_three())

    def test_product_of_groups_uses_componentwise_orbit_factorization(self):
        left, right = cyclic_group(2), cyclic_group(3)
        result = verified_product_count((left, right))
        self.assertEqual(result["orbit_count"], 2592)
        combined = direct_product_group(left, right)
        self.assertEqual(independently_checked_receipt(combined)["burnside_orbits"], 2592)

    def test_receipt_not_a_claim_of_benchmark_success(self):
        r = independently_checked_receipt(symmetric_group_three())
        self.assertFalse(r["benchmark_answer_verified"])
        self.assertFalse(r["proof_assistant_kernel_checked"])
        self.assertFalse(r["external_actions_authorized"])
        self.assertFalse(r["production_eligible"])
        self.assertEqual(len(r["content_sha256"]), 64)
        self.assertEqual(r, independently_checked_receipt(symmetric_group_three()))

    def test_invalid_identity_is_rejected(self):
        with self.assertRaises(InvalidFiniteGroup):
            FiniteGroup("invalid", ((0, 1), (1, 0)), 1)

    def test_invalid_associativity_or_missing_inverse_rejected(self):
        bad = ((0, 1, 2), (1, 2, 0), (2, 0, 0))
        with self.assertRaises(InvalidFiniteGroup):
            FiniteGroup("broken", bad)

    def test_boolean_not_accepted_as_group_order(self):
        with self.assertRaises(InvalidFiniteGroup):
            cyclic_group(True)
        with self.assertRaises(InvalidFiniteGroup):
            FiniteGroup("bad", ((True,),))

    def test_group_size_limit(self):
        with self.assertRaises(InvalidFiniteGroup):
            cyclic_group(13)

    def test_genus_limit(self):
        with self.assertRaises(CostCeilingExceeded):
            count_orbits_burnside(cyclic_group(2), 6)
        with self.assertRaises(CostCeilingExceeded):
            count_orbits_direct(cyclic_group(2), 0)

    def test_enumeration_limit(self):
        with self.assertRaises(CostCeilingExceeded):
            count_orbits_direct(symmetric_group_three(), 5, max_assignments=200)

    def test_direct_product_limit(self):
        with self.assertRaises(CostCeilingExceeded):
            direct_product_group(cyclic_group(4), cyclic_group(4))

    def test_product_empty_rejected(self):
        with self.assertRaises(CostCeilingExceeded):
            verified_product_count(())


class SecurityBoundaryTests(unittest.TestCase):
    def request(self, tool="semgrep", **kwargs):
        return {"tool": tool, "source_head": HEAD, "target_owned": True,
                "offline": True, "read_only": True, **kwargs}

    def test_twelve_source_families_explicit(self):
        self.assertEqual(len(TOOL_FAMILIES), 12)

    def test_offline_static_scope_is_still_only_a_proposal(self):
        x = security_proposal(self.request())
        self.assertEqual(x["disposition"], "REVIEW_ONLY_CANDIDATE")
        self.assertFalse(x["execution_authorized"])
        self.assertFalse(x["publish_or_merge_authorized"])
        self.assertFalse(x["model_training_authorized"])

    def test_unknown_or_malformed_tool_fails_closed(self):
        for tool in ("unknown", [], None):
            with self.subTest(tool=tool):
                r = security_proposal(self.request(tool=tool))
                self.assertIn("UNRECOGNIZED_TOOL", r["blockers"])

    def test_missing_immutable_head_fails_closed(self):
        x = security_proposal(self.request(source_head="main"))
        self.assertIn("UNPINNED_SOURCE", x["blockers"])

    def test_active_scanners_not_auto_authorized(self):
        for tool in ("strix", "nuclei", "microsoft-pyrit"):
            r = security_proposal(self.request(tool))
            self.assertIn("ACTIVE_TARGET_SCOPE_APPROVAL_REQUIRED", r["blockers"])
            self.assertFalse(r["execution_authorized"])

    def test_bla_artifact_poisoning_does_not_enter_proof_or_training(self):
        for destination in ("scientific_evidence", "public_model", "receipt", "training"):
            x = security_proposal(self.request("bla", destination_kind=destination))
            self.assertIn("DECOY_POISONING_CONFLICTS_WITH_EVIDENCE_INTEGRITY",
                          x["blockers"])

    def test_cloud_model_egress_requires_privacy_review(self):
        x = security_proposal(self.request("codex-security", offline=False))
        self.assertIn("PRIVATE_CODE_EGRESS_REVIEW_REQUIRED", x["blockers"])
        x = security_proposal(self.request("nvidia-skillspector", external_model_review=True))
        self.assertIn("PRIVATE_CODE_EGRESS_REVIEW_REQUIRED", x["blockers"])

    def test_live_credentials_cannot_be_verified_automatically(self):
        x = security_proposal(self.request("trufflehog", verify_live_secrets=True))
        self.assertIn("LIVE_CREDENTIAL_CHECK_NOT_AUTHORIZED", x["blockers"])

    def test_public_targets_require_target_specific_approval(self):
        x = security_proposal(self.request(public_or_routable_target=True))
        self.assertIn("ACTIVE_TARGET_SCOPE_APPROVAL_REQUIRED", x["blockers"])

    def test_private_target_ownership_not_inferred(self):
        x = security_proposal(self.request(target_owned=False))
        self.assertIn("TARGET_OWNERSHIP_NOT_ESTABLISHED", x["blockers"])

    def test_scanner_output_cannot_confirm_self_asserted_findings(self):
        report = {"producer": "scanner", "independent_verifier": "reviewer",
                  "source_head": HEAD, "source_trace": "path:line:func",
                  "reproduction_receipt": "sha256:fixture", "independently_reproduced": True}
        result = validate_finding(report)
        self.assertEqual(result["verdict"], "VALIDATION_CLAIM_UNVERIFIED")
        self.assertTrue(result["requires_human_triage"])
        self.assertFalse(result["external_action_authorized"])
        self.assertFalse(result["automatically_dismissible"])

    def test_unverified_rejection_does_not_auto_dismiss(self):
        report = {"producer": "scanner", "independent_verifier": "reviewer",
                  "source_head": HEAD, "source_trace": "path:line:func",
                  "reproduction_receipt": "sha256:fixture", "independently_disproved": True}
        result = validate_finding(report)
        self.assertEqual(result["verdict"], "REJECTION_CLAIM_UNVERIFIED")
        self.assertFalse(result["automatically_dismissible"])

    def test_duplicate_verifier_does_not_count(self):
        x = validate_finding({"producer": "same", "independent_verifier": "same",
                              "source_head": HEAD, "source_trace": "line",
                              "reproduction_receipt": "hash", "independently_reproduced": True})
        self.assertEqual(x["verdict"], "NEEDS_VALIDATION")

    def test_public_answer_data_must_be_eval_only(self):
        x = training_admission({"source_family": "POINDEXTER_PDL",
                                "rights_confirmed": True,
                                "benchmark_answer_public": True})
        self.assertEqual(x["disposition"], "QUARANTINE_EVAL_ONLY")
        self.assertFalse(x["model_training_executed"])

    def test_do_not_train_licenses_are_quarantined(self):
        x = training_admission({"source_family": "SZL_ORIGINAL_SYNTHETIC",
                                "rights_confirmed": True,
                                "license_prohibits_training": True})
        self.assertEqual(x["disposition"], "QUARANTINE_EVAL_ONLY")

    def test_synthetic_record_requires_exact_math_checks(self):
        x = training_admission({"source_family": "SZL_ORIGINAL_SYNTHETIC",
                                "rights_confirmed": True,
                                "independent_math_check": False})
        self.assertEqual(x["disposition"], "HOLD")
        self.assertIn("INDEPENDENT_MATH_VALIDATION_MISSING", x["reasons"])

    def test_synthetic_ready_is_not_training_authority(self):
        x = training_admission({"source_family": "SZL_ORIGINAL_SYNTHETIC",
                                "rights_confirmed": True,
                                "independent_math_check": True})
        self.assertEqual(x["disposition"], "SYNTHETIC_TRAINING_CANDIDATE")
        self.assertFalse(x["model_training_executed"])
        self.assertFalse(x["external_publication_authorized"])


if __name__ == "__main__":
    unittest.main()
