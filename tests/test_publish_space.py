import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "publish_space.py"
SPEC = importlib.util.spec_from_file_location("publish_space", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublishSpaceTests(unittest.TestCase):
    def test_no_write_reads_stay_anonymous_with_available_credentials(self) -> None:
        revision = "a" * 40

        class FakeHfApi:
            instances = []

            def __init__(self, *, token=None):
                self.token = token
                self.authorization_header = (
                    None
                    if token is False
                    else f"Bearer {token or os.environ.get('HF_TOKEN') or 'cached-token'}"
                )
                self.instances.append(self)

            def space_info(self, target):
                self.assert_anonymous_read(target)
                return SimpleNamespace(
                    host="https://betterwithage-szl-bench-suite.static.hf.space",
                    private=False,
                    runtime=SimpleNamespace(stage="RUNNING"),
                    sdk="static",
                    sha=revision,
                )

            def assert_anonymous_read(self, target):
                if target != MODULE.TARGET:
                    raise AssertionError(f"unexpected target: {target}")
                if self.authorization_header is not None:
                    raise AssertionError("read client attached an Authorization header")

            def whoami(self):
                raise AssertionError("no-write verification must not authenticate")

        def fake_fetch(base_url, path, expected_revision):
            self.assertEqual(revision, expected_revision)
            self.assertEqual(
                "https://betterwithage-szl-bench-suite.static.hf.space/",
                base_url,
            )
            if not path:
                return 200, MODULE.EXPECTED_MARKER.encode("utf-8")
            return 200, b"canonical-readback"

        credential_modes = (
            ("environment", ["publish_space.py"], "hf-environment-token"),
            ("cached", ["publish_space.py", "--cached-auth"], ""),
        )
        for name, argv, environment_token in credential_modes:
            with self.subTest(credential=name):
                FakeHfApi.instances = []
                fake_module = SimpleNamespace(HfApi=FakeHfApi)
                with (
                    mock.patch.dict(
                        os.environ,
                        {"HF_TOKEN": environment_token},
                        clear=False,
                    ),
                    mock.patch.dict(sys.modules, {"huggingface_hub": fake_module}),
                    mock.patch.object(sys, "argv", argv),
                    mock.patch.object(MODULE, "_bundle_matches", return_value=True),
                    mock.patch.object(MODULE, "_fetch", side_effect=fake_fetch),
                    mock.patch.object(
                        MODULE,
                        "_canonical_bytes",
                        return_value=b"canonical-readback",
                    ),
                ):
                    self.assertEqual(0, MODULE.main())

                self.assertEqual(1, len(FakeHfApi.instances))
                self.assertIs(FakeHfApi.instances[0].token, False)
                self.assertIsNone(FakeHfApi.instances[0].authorization_header)

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
