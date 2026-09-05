# Frontier Bench

Measured inference-engine evidence for the SZL stack — the public bench for the **szl-forge** engine.

## What this is

The honest companion to every engine claim. If the estate says the engine is fast, this is where the number lives — with the machine, the date, and the method attached. Unmeasured claims say so.

## Guarantees

- **Honest benchmarks** — every published number is labeled with hardware, date, and method; projections are marked as projections or omitted.
- **Receipts** — benchmark runs are hashed and chained so results history is tamper-evident.
- **Bounded claims** — source, receipt admission, CI, service runtime, and public readback have separate evidence.
- **Fail-closed display** — the public surface renders only verified results; anything unverifiable appears as absent.

## Public surface

The consolidated public bench lives at [betterwithage/szl-bench-suite](https://huggingface.co/spaces/betterwithage/szl-bench-suite) (Engine Bench tab) — one evidence surface for engine, retrieval, and quantization claims.

Hardware identity is declared by receipts and checked against the dedicated-node policy.
The receipt HMAC authenticates an operator assertion; no independent hardware witness is claimed.

**Division of labor:** this repository is the sole publisher for the consolidated Space.
The [evidence controller](deploy/bench-plane/finish_bench_plane.py) reads reviewed immutable
Git objects without executing producer code, verifies all three receipt chains, requires
authentication for measured rows, and exports one digest-bound static bundle.
The canonical publisher repeats receipt admission before committing and verifies immutable
files, provider state, and exact public bytes. See the [operations guide](deploy/bench-plane/BENCH_PLANE_OPERATIONS.md).
An unchanged healthy public bundle is verified without a provider credential; any
content or runtime write fails closed unless the owner credential is available.
Scheduled publication stays disabled until a scoped repository credential is installed.

## Status

The current chains contain only their BLOCKED genesis receipts: zero admitted measured
rows. The evidence services can run with that dataset. Real benchmark execution and
signed raw evidence remain the responsibility of the dedicated-node measurement producers.
