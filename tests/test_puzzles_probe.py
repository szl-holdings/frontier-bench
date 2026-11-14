import json
import os
import tempfile
import unittest

from puzzles.families import generate
from puzzles.probe import (
    RECEIPT_SCHEMA,
    build_receipt,
    extract_json_object,
    main as probe_main,
    normalize_solution,
)
from puzzles.render import render


class TestVerdictLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.puzzle, _ = generate("verdict-test")
        cls.spec = cls.puzzle["spec"]

    def test_extract_json_object(self):
        self.assertEqual(extract_json_object('prefix {"a": 1} suffix'), {"a": 1})
        self.assertEqual(extract_json_object('{"a": {"b": [1,2]}} tail'), {"a": {"b": [1, 2]}})
        self.assertEqual(extract_json_object('no braces'), None)
        self.assertEqual(extract_json_object('broken {"a": }'), None)

    def test_exact_answer_passes(self):
        parsed = {"solution": self.puzzle["certified_solution"]}
        normalized, abstained = normalize_solution(parsed, self.spec)
        self.assertFalse(abstained)
        self.assertEqual(normalized, self.puzzle["certified_solution"])

    def test_wrong_answer_fails(self):
        if self.spec["family"] != "zebra":
            self.skipTest("zebra fixture")
        cats = self.spec["categories"]
        wrong = {
            cat: {v: ((self.puzzle["certified_solution"][cat][v] % self.spec["houses"]) + 1)
                  for v in values}
            for cat, values in cats
        }
        # ensure the perturbation actually changed something
        changed = any(
            wrong[cat][v] != self.puzzle["certified_solution"][cat][v]
            for cat, _ in cats for v in wrong[cat]
        )
        if not changed:
            self.skipTest("perturbation produced the same solution")
        normalized, abstained = normalize_solution({"solution": wrong}, self.spec)
        self.assertFalse(abstained)
        self.assertNotEqual(normalized, self.puzzle["certified_solution"])

    def test_abstain_detected(self):
        parsed = {"solution": None}
        _, abstained = normalize_solution(parsed, self.spec)
        self.assertTrue(abstained)
        parsed = {"solution": "cannot determine from the clues"}
        _, abstained = normalize_solution(parsed, self.spec)
        self.assertTrue(abstained)

    def test_incomplete_answer_is_error_not_fail(self):
        parsed = {"solution": {"color": {"red": 1}}}
        normalized, abstained = normalize_solution(parsed, self.spec)
        self.assertFalse(abstained)
        self.assertIsNone(normalized)


class TestReceipt(unittest.TestCase):
    def test_receipt_digest_is_canonical_and_stable(self):
        rows = [{"id": "ab", "verdict": "PASS"}]
        r1 = build_receipt("m", "MEASURED", rows, "1/1", 7, "rev", "mrev", "unknown", [])
        r2 = build_receipt("m", "MEASURED", rows, "1/1", 7, "rev", "mrev", "unknown", [])
        self.assertEqual(r1["receipt_sha256"], r2["receipt_sha256"])
        self.assertEqual(r1["kind"], RECEIPT_SCHEMA)
        # key order in the row must not matter
        rows2 = [{"verdict": "PASS", "id": "ab"}]
        r3 = build_receipt("m", "MEASURED", rows2, "1/1", 7, "rev", "mrev", "unknown", [])
        self.assertEqual(r1["receipt_sha256"], r3["receipt_sha256"])

    def test_fail_closed_without_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "receipt.json")
            rc = probe_main(
                [
                    "--seed", "1",
                    "--plan", "f4:1",
                    "--model", "test-model",
                    "--model-revision", "0" * 40,
                    "--source-revision", "0" * 40,
                    "--endpoint-env", "DEFINITELY_UNSET_PUZZLE_ENDPOINT",
                    "--out", out,
                ]
            )
            self.assertEqual(rc, 2)
            with open(out, "r", encoding="utf-8") as handle:
                receipt = json.load(handle)
            self.assertEqual(receipt["state"], "UNAVAILABLE")
            self.assertEqual(receipt["rows"], [])
            self.assertTrue(any("not set" in e for e in receipt["errors"]))


if __name__ == "__main__":
    unittest.main()
