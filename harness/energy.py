"""Colocated-only GPU energy metering for benchmark engine runs.

HONESTY (estate doctrine; Lambda = Conjecture 1, advisory):
- Energy is MEASURED only when ALL of the following hold:
  * the engine endpoint is colocated with this harness (loopback or the
    local hostname) -- client-node NVML can never represent a remote
    engine's energy, so non-colocated endpoints report mode="unmeasured";
  * the canonical estate energy package "szl-energy-attest" is importable
    (optional dependency -- its absence is an honest unmeasured, never a
    failure);
  * NVML is available and grants energy/power readback.
- Otherwise the block reports mode="unmeasured", joules=None, and a
  human-readable reason. No estimates, no scaling, no fabrication.
- Measured joules come from szl_energy_attest.inference_meter.EnergyMeter:
  the NVML hardware energy counter when the device exposes it, else a
  trapezoidal integral of NVML power samples. Board-level energy of the
  colocated node: engine plus colocated workloads. We report what the
  hardware reports and say so.
- Derived figures (avg_power_w, joules_per_request, tokens_per_joule) are
  computed ONLY from measured joules; they are None otherwise.
- FRONTIER_BENCH_ENERGY=off disables metering entirely (still honest nulls).
"""
from __future__ import annotations

import os
import socket
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

METERING_LABEL = "benchmark-node-nvml"

try:  # optional dependency -- absence degrades honestly, never fails
    from szl_energy_attest.inference_meter import (  # type: ignore
        EnergyMeter,
        capability_report,
        nvml_available,
    )
    _METER_IMPORT_ERROR: Optional[str] = None
except Exception as _exc:  # noqa: BLE001 -- record any failure reason
    EnergyMeter = None  # type: ignore
    capability_report = None  # type: ignore
    nvml_available = None  # type: ignore
    _METER_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def endpoint_colocated(endpoint: Optional[str]) -> bool:
    """True only when the endpoint host is this node (loopback or hostname)."""
    if not endpoint:
        return False
    try:
        host = (urlparse(str(endpoint)).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return host == socket.gethostname().strip().lower()
    except OSError:
        return False


def build_block(
    stop_result: Dict[str, Any],
    *,
    n_requests: int,
    completion_tokens: int,
    colocated: bool,
    device_index: int,
) -> Dict[str, Any]:
    """Assemble the honest energy block from a meter stop result.

    Defense in depth: a stop result claiming a measured mode without a valid
    non-negative numeric joules figure is forced to unmeasured with null
    joules. Derived figures are computed only from measured joules.
    """
    if type(n_requests) is not int or n_requests < 0:
        raise ValueError("invalid n_requests")
    if type(completion_tokens) is not int or completion_tokens < 0:
        raise ValueError("invalid completion_tokens")

    mode = str(stop_result.get("mode", "unmeasured"))
    raw_joules = stop_result.get("joules")
    measured = (
        mode != "unmeasured"
        and isinstance(raw_joules, (int, float))
        and not isinstance(raw_joules, bool)
        and raw_joules >= 0
    )
    note = stop_result.get("note")
    if mode != "unmeasured" and not measured:
        mode = "unmeasured"
        prefix = str(note) + " ; " if note else ""
        note = prefix + "meter reported no valid joules; energy unmeasured"

    wall = stop_result.get("wall_seconds")
    wall = float(wall) if isinstance(wall, (int, float)) and wall >= 0 else 0.0
    joules = float(raw_joules) if measured else None

    avg_power_w = round(joules / wall, 6) if measured and joules is not None and wall > 0 else None
    joules_per_request = (
        round(joules / n_requests, 6) if measured and joules is not None and n_requests > 0 else None
    )
    tokens_per_joule = (
        round(completion_tokens / joules, 6) if measured and joules is not None and joules > 0 else None
    )
    return {
        "mode": mode,
        "metering": METERING_LABEL if measured else "unmeasured",
        "joules": round(joules, 6) if measured and joules is not None else None,
        "wall_seconds": round(wall, 9),
        "avg_power_w": avg_power_w,
        "joules_per_request": joules_per_request,
        "tokens_per_joule": tokens_per_joule,
        "n_power_samples": int(stop_result.get("n_power_samples") or 0),
        "colocated": bool(colocated),
        "device_index": int(device_index),
        "n_requests": n_requests,
        "completion_tokens": completion_tokens,
        "note": note,
    }


class _UnmeasuredMeter:
    """Honest no-op meter: wall time is clocked, energy is never fabricated."""

    def __init__(self, reason: str, colocated: bool) -> None:
        self._reason = reason
        self.colocated = bool(colocated)
        self._t0: Optional[float] = None
        self._wall = 0.0

    def start(self) -> None:
        self._t0 = time.perf_counter()

    def sample(self) -> None:  # no-op: nothing is being sampled
        pass

    def stop(self) -> None:
        if self._t0 is not None:
            self._wall = time.perf_counter() - self._t0
            self._t0 = None

    def result(self, *, n_requests: int, completion_tokens: int) -> Dict[str, Any]:
        self.stop()
        return build_block(
            {
                "mode": "unmeasured",
                "joules": None,
                "wall_seconds": self._wall,
                "n_power_samples": 0,
                "note": self._reason,
            },
            n_requests=n_requests,
            completion_tokens=completion_tokens,
            colocated=self.colocated,
            device_index=0,
        )


class _NvmMeter:
    """Real meter over szl_energy_attest.inference_meter.EnergyMeter."""

    def __init__(self, device_index: int, colocated: bool) -> None:
        self._em = EnergyMeter(device_index=device_index, sample_hz=100.0)  # type: ignore
        self.colocated = bool(colocated)
        self._device_index = int(device_index)
        self._stop: Optional[Dict[str, Any]] = None

    def start(self) -> None:
        self._em.start()

    def sample(self) -> None:
        try:  # a sampling hiccup must never kill a benchmark run
            self._em.sample()
        except Exception:  # noqa: BLE001 -- EnergyMeter faults are recorded inside
            pass

    def stop(self) -> None:
        if self._stop is None:
            try:
                self._stop = self._em.stop()
            except Exception as exc:  # noqa: BLE001 -- degrade honestly
                self._stop = {
                    "mode": "unmeasured",
                    "joules": None,
                    "wall_seconds": 0.0,
                    "n_power_samples": 0,
                    "note": f"meter stop failed ({type(exc).__name__}); energy unmeasured",
                }

    def result(self, *, n_requests: int, completion_tokens: int) -> Dict[str, Any]:
        self.stop()
        stop = self._stop or {
            "mode": "unmeasured",
            "joules": None,
            "wall_seconds": 0.0,
            "n_power_samples": 0,
            "note": "meter never stopped; energy unmeasured",
        }
        return build_block(
            stop,
            n_requests=n_requests,
            completion_tokens=completion_tokens,
            colocated=self.colocated,
            device_index=self._device_index,
        )


def engine_energy_meter(endpoint: Optional[str], device_index: int = 0):
    """Pick the honest meter for this run: real NVML when possible, else honest nulls."""
    colocated = endpoint_colocated(endpoint)
    if os.environ.get("FRONTIER_BENCH_ENERGY", "").strip().lower() == "off":
        return _UnmeasuredMeter("metering disabled by FRONTIER_BENCH_ENERGY=off", colocated)
    if not colocated:
        return _UnmeasuredMeter(
            "endpoint not colocated with the harness; client-node NVML would not represent engine energy",
            colocated,
        )
    if EnergyMeter is None:
        return _UnmeasuredMeter(
            f"szl-energy-attest not importable ({_METER_IMPORT_ERROR}); no metering",
            colocated,
        )
    if not nvml_available():  # type: ignore
        cap = capability_report() if capability_report is not None else {}
        preferred = cap.get("preferred_mode", "unmeasured")
        reason = cap.get("import_error") or "no NVML device or readback permission"
        return _UnmeasuredMeter(
            f"NVML unavailable on this node (preferred_mode={preferred}; {reason}); energy unmeasured",
            colocated,
        )
    return _NvmMeter(device_index, colocated)


def capability_summary() -> Dict[str, Any]:
    """Describe, honestly, what benchmark energy measurement is possible here."""
    nvml_ok = False
    preferred = "unmeasured"
    if nvml_available is not None:
        try:
            nvml_ok = bool(nvml_available())
        except Exception:  # noqa: BLE001
            nvml_ok = False
    if capability_report is not None:
        try:
            preferred = capability_report().get("preferred_mode", "unmeasured")
        except Exception:  # noqa: BLE001
            preferred = "unmeasured"
    return {
        "package": "szl-energy-attest",
        "importable": EnergyMeter is not None,
        "import_error": _METER_IMPORT_ERROR,
        "nvml_available": nvml_ok,
        "preferred_mode": preferred,
        "colocated_only": True,
        "disable_env": "FRONTIER_BENCH_ENERGY=off",
    }
