import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "publish_space.py"
SPEC = importlib.util.spec_from_file_location("publish_space", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublishSpaceTests(unittest.TestCase):
    def test_accepts_provider_static_host(self) -> None:
        info = SimpleNamespace(
            host="https://betterwithage-szl-bench-suite.static.hf.space"
        )
        self.assertEqual(
            "https://betterwithage-szl-bench-suite.static.hf.space/",
            MODULE._live_url(info),
        )

    def test_accepts_provider_application_host(self) -> None:
        info = SimpleNamespace(host="https://betterwithage-szl-bench-suite.hf.space")
        self.assertEqual(
            "https://betterwithage-szl-bench-suite.hf.space/",
            MODULE._live_url(info),
        )

    def test_rejects_unexpected_host_or_url_components(self) -> None:
        bad_hosts = (
            "http://betterwithage-szl-bench-suite.static.hf.space",
            "https://example.com",
            "https://betterwithage-szl-bench-suite.static.hf.space/other",
            "https://betterwithage-szl-bench-suite.static.hf.space?redirect=1",
        )
        for host in bad_hosts:
            with self.subTest(host=host), self.assertRaises(ValueError):
                MODULE._live_url(SimpleNamespace(host=host))

    def test_rejects_incomplete_local_bundle_before_network_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "site").mkdir()
            (root / "site" / "README.md").write_text("test", encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(root)
                with self.assertRaisesRegex(ValueError, "bundle is incomplete"):
                    MODULE._bundle_matches("0" * 40)
            finally:
                os.chdir(previous)

    def test_text_bundle_bytes_are_platform_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            path.write_bytes(b'{\r\n  "ok": true\r\n}\r\n')
            self.assertEqual(b'{\n  "ok": true\n}\n', MODULE._canonical_bytes(path))


if __name__ == "__main__":
    unittest.main()
