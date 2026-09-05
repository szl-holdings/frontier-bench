import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SpaceBundleTests(unittest.TestCase):
    def test_static_space_metadata_is_present(self) -> None:
        readme = (ROOT / "site" / "README.md").read_text(encoding="utf-8")
        self.assertTrue(readme.startswith("---\n"))
        self.assertIn("\nsdk: static\n", readme)
        self.assertIn("\napp_file: index.html\n", readme)
        self.assertNotIn("colorFrom: cyan", readme)
        self.assertNotIn("\nlicense:", readme)

    def test_accessible_truth_surface_files_exist(self) -> None:
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "site" / "style.css").read_text(encoding="utf-8")
        script = (ROOT / "site" / "app.js").read_text(encoding="utf-8")
        self.assertIn("SZL Bench Suite", html)
        self.assertIn('role="tablist"', html)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("forced-colors", css)
        self.assertIn("results.json", script)
        self.assertIn("deployment.json", script)

    def test_only_this_repository_publishes_the_space(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(
            encoding="utf-8"
        )
        publisher = (ROOT / "tools" / "publish_space.py").read_text(encoding="utf-8")
        self.assertIn("tools/publish_space.py", workflow)
        self.assertEqual(1, publisher.count('TARGET = "betterwithage/szl-bench-suite"'))
        self.assertIn("parent_commit=before_sha", publisher)
        self.assertIn("_bundle_matches(before_sha)", publisher)
        self.assertNotIn("SKIPPED: HF_TOKEN", workflow)

    def test_uncredentialed_publisher_has_no_schedule(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  schedule:", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)


if __name__ == "__main__":
    unittest.main()
