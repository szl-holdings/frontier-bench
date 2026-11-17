"""Synthetic development corpus isolation and exact-math admission tests."""
import json
import unittest

from research.security_math_v0.examples import development_cases


class DevelopmentCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = development_cases()

    def test_no_external_benchmark_answer_or_data(self):
        text = json.dumps(self.corpus)
        self.assertFalse(self.corpus["public_benchmark_content_included"])
        self.assertNotIn("Poindexter", text)
        self.assertNotIn("Block Picard", text)
        self.assertEqual(self.corpus["model_evaluations_run"], 0)
        self.assertEqual(self.corpus["training_runs"], 0)

    def test_original_development_cases_all_independently_labelled(self):
        rows = self.corpus["cases"]
        self.assertEqual(len(rows), 8)
        self.assertEqual(len({x["id"] for x in rows}), len(rows))
        for row in rows:
            self.assertEqual(row["source_origin"], "SZL_ORIGINAL_SYNTHETIC")
            self.assertEqual(row["split"], "OPEN_DEVELOPMENT_ONLY_NOT_BLIND_EVAL")
            self.assertEqual(row["training_disposition"], "SYNTHETIC_TRAINING_CANDIDATE")
            self.assertFalse(row["benchmark_score_eligible"])
            self.assertFalse(row["training_executed"])
            self.assertIsNone(row["github_or_hub_model_revision"])
            self.assertEqual(len(row["synthetic_receipt_sha256"]), 64)

    def test_known_distinct_group_fixtures(self):
        rows = {r["group"]: r for r in self.corpus["cases"]}
        self.assertEqual(rows["C2"]["reference_orbit_count"], 32)
        self.assertEqual(rows["C3"]["reference_orbit_count"], 81)
        self.assertEqual(rows["C4"]["reference_orbit_count"], 512)
        self.assertEqual(rows["S3"]["reference_orbit_count"], 502)
        self.assertEqual(rows["Q8"]["reference_orbit_count"], 4352)

    def test_isomorphic_c6_and_product_share_split_identity(self):
        rows = {r["group"]: r for r in self.corpus["cases"]}
        self.assertEqual(rows["C6"]["isomorphism_class"], "C6")
        self.assertEqual(rows["C2_C3"]["isomorphism_class"], "C6")
        self.assertEqual(rows["C6"]["reference_orbit_count"], 2592)
        self.assertEqual(rows["C2_C3"]["reference_orbit_count"], 2592)

    def test_deterministic_content_digest(self):
        again = development_cases()
        self.assertEqual(self.corpus, again)
        self.assertEqual(len(self.corpus["content_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
