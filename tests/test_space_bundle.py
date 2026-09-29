import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deploy" / "bench-plane"
SZL_ROOTS = (PACKAGE / "szl", ROOT / "site" / "szl")
SZL_FILES = {"SOURCE.json", "szl-design-system.css", "logos/szl_favicon.svg"}
COLOR_LITERAL = re.compile(r"#[0-9A-Fa-f]{3,8}\b|\b(?:rgba?|hsla?)\(")
FONT_LITERAL = re.compile(r"(?i)\b(?:monospace|menlo|consolas|segoe|system-ui|sfmono|inter|space grotesk|jetbrains|ibm plex)\b")
WEBFONT = re.compile(r"(?i)fonts\.googleapis|fonts\.gstatic|fontshare|@font-face|\.woff2?\b")


class FounderDesignSystemVendoringTests(unittest.TestCase):
    """SZL KANCHAY v1.1.0 (founder direction) is vendored byte for byte once per served root."""

    def test_each_served_root_carries_an_exact_copy_of_the_export(self) -> None:
        for root in SZL_ROOTS:
            with self.subTest(root=root.relative_to(ROOT).as_posix()):
                manifest = json.loads((root / "SOURCE.json").read_text(encoding="utf-8"))
                self.assertEqual((manifest["name"], manifest["version"]), ("szl-kanchay", "1.1.0"))
                vendored = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
                self.assertEqual(vendored, SZL_FILES)
                for name in vendored - {"SOURCE.json"}:
                    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
                    self.assertEqual(manifest["sha256"][name], digest, name)

    def test_no_webfonts_or_v1_kanchay_files_remain(self) -> None:
        tracked = [path for path in ROOT.rglob("*") if ".git" not in path.parts and path.is_file()]
        self.assertEqual([path for path in tracked if path.suffix in {".woff", ".woff2", ".ttf", ".otf"}], [])
        self.assertEqual([path for path in tracked if "kanchay" in path.relative_to(ROOT).parts], [])
        for page in (PACKAGE / "szl-bench-suite.index.html", ROOT / "site" / "index.html", ROOT / "site" / "style.css"):
            self.assertIsNone(WEBFONT.search(page.read_text(encoding="utf-8")), page.name)

    def test_space_page_links_only_vendored_assets(self) -> None:
        html = (PACKAGE / "szl-bench-suite.index.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'<link rel="stylesheet" href="([^"]+)">', html), ["szl/szl-design-system.css"])
        self.assertIn('src="szl/logos/szl_favicon.svg"', html)
        self.assertNotIn("font-src", html)
        markup = html.split("<script>", 1)[0]
        self.assertIsNone(COLOR_LITERAL.search(markup))
        self.assertIsNone(FONT_LITERAL.search(markup))

    def test_site_page_loads_the_design_system_before_its_own_tokens_only_stylesheet(self) -> None:
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'<link rel="stylesheet" href="([^"]+)">', html), ["szl/szl-design-system.css", "style.css"])
        self.assertIsNone(COLOR_LITERAL.search(html))
        css = (ROOT / "site" / "style.css").read_text(encoding="utf-8")
        self.assertIsNone(COLOR_LITERAL.search(css))
        declarations = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        self.assertIsNone(FONT_LITERAL.search(declarations))
        self.assertNotIn("--accent", declarations)


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

    def test_publisher_runs_on_main_and_weekly_but_never_on_pull_requests(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("\n  schedule:", workflow)
        self.assertIn('cron: "17 6 * * 1"', workflow)
        publication = workflow.split("\n  publish:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", publication)
        self.assertIn("github.ref == 'refs/heads/main'", publication)

    def test_receipt_key_is_scoped_to_trusted_main_admission(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target", workflow)
        audit = workflow.split("- name: Audit reviewed sources and export the public bundle", 1)[1].split("\n  publish:", 1)[0]
        self.assertIn("if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'", audit)
        self.assertIn("SZL_BENCH_RECEIPT_HMAC_KEY_HEX: ${{ secrets.SZL_BENCH_RECEIPT_HMAC_KEY_HEX }}", audit)
        publication = workflow.split("\n  publish:", 1)[1]
        self.assertIn("github.event_name != 'pull_request'", publication)
        self.assertIn("github.ref == 'refs/heads/main'", publication)
        self.assertEqual(2, publication.count("SZL_BENCH_RECEIPT_HMAC_KEY_HEX: ${{ secrets.SZL_BENCH_RECEIPT_HMAC_KEY_HEX }}"))


if __name__ == "__main__":
    unittest.main()
