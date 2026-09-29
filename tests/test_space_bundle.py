import hashlib
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deploy" / "bench-plane"
KANCHAY_ROOTS = (PACKAGE / "kanchay", ROOT / "site" / "kanchay")
KANCHAY_FONTS = {"Inter-latin.woff2", "JetBrainsMono-latin.woff2", "SpaceGrotesk-latin.woff2"}
COLOR_LITERAL = re.compile(r"#[0-9A-Fa-f]{3,8}\b|\b(?:rgba?|hsla?)\(")
FONT_LITERAL = re.compile(r"(?i)\b(?:monospace|menlo|consolas|segoe|ui-sans-serif|sfmono|inter|space grotesk|jetbrains)\b")


class KanchayVendoringTests(unittest.TestCase):
    """SZL Kanchay v1.0.0 is vendored byte for byte once per served root."""

    def test_each_served_root_carries_an_exact_copy_of_the_export(self) -> None:
        for root in KANCHAY_ROOTS:
            with self.subTest(root=root.relative_to(ROOT).as_posix()):
                manifest = json.loads((root / "SOURCE.json").read_text(encoding="utf-8"))
                self.assertEqual((manifest["name"], manifest["version"]), ("szl-kanchay", "1.0.0"))
                vendored = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
                self.assertIn("kanchay.css", vendored)
                self.assertEqual({name.split("/", 1)[1] for name in vendored if name.startswith("fonts/")}, KANCHAY_FONTS)
                for name in vendored - {"SOURCE.json"}:
                    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
                    self.assertEqual(manifest["sha256"][name], digest, name)

    def test_space_page_links_only_vendored_styles_and_fonts(self) -> None:
        html = (PACKAGE / "szl-bench-suite.index.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'<link rel="stylesheet" href="([^"]+)">', html),
                         ["kanchay/kanchay.css", "kanchay/kanchay-components.css"])
        self.assertIn("font-src 'self'", html)
        self.assertNotIn("fonts.googleapis", html)
        markup = html.split("<script>", 1)[0]
        self.assertIsNone(COLOR_LITERAL.search(markup))

    def test_site_page_loads_kanchay_before_its_own_tokens_only_stylesheet(self) -> None:
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'<link rel="stylesheet" href="([^"]+)">', html), ["kanchay/kanchay.css", "style.css"])
        self.assertNotIn("fonts.googleapis", html)
        self.assertIsNone(COLOR_LITERAL.search(html))
        css = (ROOT / "site" / "style.css").read_text(encoding="utf-8")
        self.assertIsNone(COLOR_LITERAL.search(css))
        declarations = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        self.assertIsNone(FONT_LITERAL.search(declarations))
        self.assertIn(":focus-visible { outline: var(--border-focus) solid var(--color-focus); outline-offset: 2px; }", css)


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
