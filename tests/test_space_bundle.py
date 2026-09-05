import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deploy" / "bench-plane"


class SpaceBundleTests(unittest.TestCase):
    def test_static_space_metadata_is_present(self) -> None:
        readme = (PACKAGE / "szl-bench-suite.README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("---\n"))
        self.assertIn("\nsdk: static\n", readme)
        self.assertIn("\napp_file: index.html\n", readme)
        self.assertNotIn("colorFrom: cyan", readme)
        self.assertNotIn("\nlicense:", readme)

    def test_accessible_truth_surface_files_exist(self) -> None:
        html = (PACKAGE / "szl-bench-suite.index.html").read_text(encoding="utf-8")
        self.assertIn("SZL Bench Suite", html)
        self.assertIn('<nav aria-label="Bench planes">', html)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn("results.json", html)
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("__RESULTS_JSON_SHA256__", html)

    def test_only_this_repository_publishes_the_space(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("tools/publish_space.py", workflow)
        self.assertIn("--export-space-bundle", workflow)
        self.assertIn("--bundle-dir", workflow)
        self.assertNotIn("python tools/sync_results.py", workflow)
        self.assertNotIn("Require the scoped provider credential", workflow)
        self.assertNotIn("SKIPPED: HF_TOKEN", workflow)

    def test_uncredentialed_publisher_has_no_schedule(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  schedule:", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)

    def test_receipt_key_is_scoped_to_trusted_main_admission(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target", workflow)
        audit = workflow.split("- name: Audit reviewed sources and export the public bundle", 1)[1].split("\n  publish:", 1)[0]
        self.assertIn("if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'", audit)
        self.assertIn("SZL_BENCH_RECEIPT_HMAC_KEY_HEX: ${{ secrets.SZL_BENCH_RECEIPT_HMAC_KEY_HEX }}", audit)
        publication = workflow.split("\n  publish:", 1)[1]
        self.assertIn("github.event_name == 'workflow_dispatch'", publication)
        self.assertIn("github.ref == 'refs/heads/main'", publication)
        self.assertEqual(2, publication.count("SZL_BENCH_RECEIPT_HMAC_KEY_HEX: ${{ secrets.SZL_BENCH_RECEIPT_HMAC_KEY_HEX }}"))


if __name__ == "__main__":
    unittest.main()
