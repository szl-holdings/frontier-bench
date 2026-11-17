"""Pin and quarantine contracts for screenshot-led external research."""
import json
from pathlib import Path
import re
import unittest

from research.security_math_v0.admission import TOOL_FAMILIES

CATALOG = Path(__file__).resolve().parents[1] / (
    "research/security_math_v0/upstream-catalog.v1.json"
)


class UpstreamCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))

    def test_all_twelve_are_canonical_and_pinned(self):
        records = self.catalog["source_locks"]
        self.assertEqual(len(records), len(TOOL_FAMILIES))
        self.assertEqual(set(x["tool"] for x in records), set(TOOL_FAMILIES))
        self.assertEqual(len(set(x["github_repository"] for x in records)), 12)
        for record in records:
            self.assertRegex(record["github_repository"], r"^[^/ ]+/[^/ ]+$")
            self.assertTrue(re.fullmatch(r"[0-9a-f]{40}", record["source_commit"]))
            self.assertIsInstance(record["license_spdx_metadata"], str)
            self.assertTrue(record["license_spdx_metadata"])

    def test_metadata_snapshot_is_never_scanner_certification(self):
        self.assertEqual(
            self.catalog["scope"],
            "read-only public repo metadata, NOT scanner evaluation or trust certification",
        )
        self.assertFalse(self.catalog["benchmark_ground_truth_included"])
        for record in self.catalog["source_locks"]:
            self.assertIs(record["installed_in_szl"], False)
            self.assertIs(record["scan_executed"], False)
            self.assertIs(record["security_independently_validated"], False)
            self.assertIs(record["model_privacy_permitted"], False)

    def test_actively_mutating_tools_remain_not_executed(self):
        rows = {x["tool"]: x for x in self.catalog["source_locks"]}
        self.assertEqual(rows["strix"]["mode"], "ACTIVE_DO_NOT_RUN")
        self.assertEqual(rows["nuclei"]["mode"], "ACTIVE_TEMPLATE_DO_NOT_RUN")
        self.assertEqual(rows["microsoft-pyrit"]["mode"], "ACTIVE_AI_REDTEAM_DO_NOT_RUN")
        self.assertEqual(rows["bla"]["mode"], "REJECT_FOR_EVIDENCE_OR_TRAINING_DECOYS")
        self.assertIn(
            rows["nvidia-skillspector"]["mode"],
            ("OFFLINE_STATIC_ONLY_NO_LLM_NO_OSV_EGRESS",),
        )

    def test_distinct_tool_licensing_is_not_mutated(self):
        rows = {x["tool"]: x for x in self.catalog["source_locks"]}
        self.assertEqual(rows["trufflehog"]["license_spdx_metadata"], "AGPL-3.0")
        self.assertEqual(rows["clamav"]["license_spdx_metadata"], "GPL-2.0")
        self.assertEqual(rows["semgrep"]["license_spdx_metadata"], "LGPL-2.1")
        self.assertEqual(rows["cloudflare-security-audit-skill"]["license_spdx_metadata"], "MIT")


if __name__ == "__main__":
    unittest.main()
