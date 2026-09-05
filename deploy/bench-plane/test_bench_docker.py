#!/usr/bin/env python3
"""Exercise real Docker evidence APIs on a disposable GitHub-hosted Linux runner.

No benchmark runs or hardware qualification occur here. The test uses empty
fixture payloads, invokes no provider publication, and never admits a receipt.
Production CLI machine qualification remains mandatory and unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import uuid


HERE = Path(__file__).resolve().parent
MODULE_SPEC = importlib.util.spec_from_file_location("bench_docker_controller", HERE / "finish_bench_plane.py")
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError("could not load packaged controller")
bench = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = bench
MODULE_SPEC.loader.exec_module(bench)


class RecordingDockerClient(bench.DockerClient):
    """Real Docker calls, with immutable IDs recorded for bounded cleanup."""

    def __init__(self, runner, executable: str, config_dir: Path, run_id: str):
        super().__init__(runner, executable, config_dir)
        self.run_id = run_id
        self.created: dict[str, str] = {}

    def inspect(self, reference: str):
        result = super().run("container", "inspect", reference, check=False, timeout=30)
        if result.returncode:
            if "no such object:" in result.output.lower() or "no such container:" in result.output.lower():
                return None
            raise RuntimeError(f"Docker container absence could not be confirmed: {bench.redact(result.output)}")
        records = json.loads(result.output)
        if not isinstance(records, list) or len(records) != 1:
            raise RuntimeError("Docker returned an ambiguous container identity")
        return records[0]

    def run(self, *args: str, **kwargs):
        name = args[args.index("--name") + 1] if args and args[0] == "run" and "--name" in args else None
        if name and self.inspect(name) is not None:
            raise RuntimeError(f"refusing to create over an existing container: {name}")
        try:
            return super().run(*args, **kwargs)
        finally:
            # A failed start can still leave a newly created container. Record
            # it only if its full run label and immutable ID identify this test.
            if name:
                record = self.inspect(name)
                if record:
                    labels = record.get("Config", {}).get("Labels") or {}
                    identity = record.get("Id", "")
                    if labels.get("io.szl.run-id") != self.run_id or not re.fullmatch(r"[0-9a-f]{64}", identity):
                        raise RuntimeError(f"container creation identity was not attributable to this test: {name}")
                    self.created[identity] = name

    def cleanup_created(self) -> list[str]:
        errors: list[str] = []
        for identity in reversed(self.created):
            try:
                record = self.inspect(identity)
                if record is None:
                    continue
                labels = record.get("Config", {}).get("Labels") or {}
                if record.get("Id") != identity or labels.get("io.szl.run-id") != self.run_id:
                    raise RuntimeError("immutable cleanup target identity changed")
                super().run("rm", "--force", identity, timeout=60)
                if self.inspect(identity) is not None:
                    raise RuntimeError("container remained after removal")
            except BaseException as exc:
                errors.append(f"{identity}: {bench.redact(exc)}")
        return errors


def require_disposable_runner() -> None:
    if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Docker integration runs only on GitHub Actions Linux")
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("Docker integration requires a disposable GitHub-hosted runner")
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACEHUB_API_TOKEN", "SZL_BENCH_RECEIPT_HMAC_KEY_HEX", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name):
            raise RuntimeError(f"integration must not receive a credential: {name}")


def empty_payload(spec) -> dict:
    return {
        "schema_version": "szl-bench-results/v2",
        "generated_at": "2026-09-05T00:00:00Z",
        "data_state": "EMPTY_HONEST",
        "count": 0,
        "results_sha256": bench.sha256_bytes(bench.canonical_json_bytes([])),
        "results": [],
        "source": {"repo": spec.repo, "revision": spec.revision, "integrity": "CI_EMPTY_FIXTURE_NO_RECEIPT_ADMISSION"},
        "measurements": "NOT_PERFORMED",
    }


def main() -> int:
    require_disposable_runner()
    os.umask(0o077)
    run_id = "ci-docker-" + uuid.uuid4().hex
    evidence = {
        "schema": "szl-bench-docker-integration/v1",
        "state": "INCOMPLETE",
        "run_id": run_id,
        "source_revision": os.environ.get("GITHUB_SHA"),
        "started_at": bench.utc_now(),
        "measurements": "NOT_PERFORMED",
        "machine_qualification": "NOT_PERFORMED_CI_FIXTURE",
        "provider_publication": "NOT_PERFORMED",
        "data_state": "EMPTY_HONEST",
    }
    client = None
    code = 1
    with tempfile.TemporaryDirectory(prefix="szl-bench-docker-ci-") as raw:
        generation = Path(raw)
        try:
            executable = bench.resolve_trusted_executable("docker")
            if executable is None:
                raise RuntimeError("trusted Docker executable unavailable")
            client = RecordingDockerClient(bench.CommandRunner(), executable, generation / "docker-client", run_id)
            evidence["docker"] = bench.docker_preflight(client)
            deployer = bench.DockerDeployer(client, run_id, 45)
            payloads = {spec.plane: empty_payload(spec) for spec in bench.REPOS}
            for spec in bench.REPOS:
                for name in (f"szl-bench-{spec.plane}", f"szl-bench-{spec.plane}-candidate-{run_id[-8:]}"):
                    if client.inspect(name) is not None:
                        raise RuntimeError(f"refusing to touch a pre-existing container: {name}")
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", spec.port))
            evidence["builds"] = {}
            evidence["staged"] = {}
            for spec in bench.REPOS:
                payload = payloads[spec.plane]
                context = bench.prepare_build_context(spec, generation, payload, run_id)
                evidence["builds"][spec.plane] = deployer.build(spec, context, payload["results_sha256"])
                evidence["staged"][spec.plane] = deployer.stage(spec, payload)
            evidence["cutover"] = deployer.cutover(payloads)
            for phase in ("initial_witness", "restart_witness"):
                records = evidence["cutover"][phase]
                if set(records) != {spec.plane for spec in bench.REPOS}:
                    raise RuntimeError(f"missing {phase} plane witness")
                if any(item.get("data_state") != "EMPTY_HONEST" or item.get("count") != 0 for item in records.values()):
                    raise RuntimeError("empty integration fixture produced nonempty evidence")
            evidence["state"] = "DOCKER_EVIDENCE_API_INTEGRATION_VERIFIED"
            code = 0
        except BaseException as exc:
            evidence["state"] = "FAILED"
            evidence["error"] = bench.redact(exc)
        finally:
            errors = client.cleanup_created() if client else []
            evidence["created_container_ids"] = list(client.created) if client else []
            evidence["cleanup"] = {"state": "VERIFIED" if not errors else "FAILED", "errors": errors}
            if errors:
                evidence["state"] = "FAILED_CLEANUP"
                code = 1
            evidence["finished_at"] = bench.utc_now()
            bench.atomic_write(Path.cwd() / "runtime-smoke-evidence.json", bench.pretty_json_bytes(evidence))
            print(json.dumps(evidence, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
