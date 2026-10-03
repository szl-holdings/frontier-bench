# Frontier Bench

Measured inference-engine evidence for the SZL stack — the public bench for the **szl-forge** engine.

## What this is

The honest companion to every engine claim. If the estate says the engine is fast, this is where the number lives — with the machine, the date, and the method attached. Unmeasured claims say so.

## Guarantees

- **Honest benchmarks** — every published number is labeled with hardware, date, and method; projections are marked as projections or omitted.
- **Receipts** — benchmark runs are hashed and chained so results history is tamper-evident.
- **Bounded claims** — source, receipt admission, CI, service runtime, and public readback have separate evidence.
- **Fail-closed display** — the public surface renders only verified results; anything unverifiable appears as absent.
- **Token-observed TTFT** — streaming TTFT starts at the first non-empty generated-text event, not an SSE role or usage event; non-streaming runs do not claim TTFT.
- **Empty-response rejection** — a nominally successful HTTP stream with no generated text is a failed sample, even if its usage metadata claims completion tokens.

## Public surface

The consolidated public bench lives at [SZLHOLDINGS/szl-bench-suite](https://huggingface.co/spaces/SZLHOLDINGS/szl-bench-suite) (Engine Bench tab) — one evidence surface for engine, retrieval, and quantization claims.

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
The canonical workflow publishes on main pushes, Mondays at 06:17 UTC, and manual
main dispatches, under one Space-scoped writer lock. Writes require the scoped
`HF_TOKEN` Actions secret; pull requests never publish.

For an explicitly read-only deployment check, first export the reviewed bundle as
described in the operations guide, then run:

```bash
python -I -B tools/publish_space.py --bundle-dir /path/to/audit/bundle --verify-only
```

This re-admits the source receipts and checks immutable and public bytes without
using publishing credentials or changing the Space. Any content/runtime drift
fails closed and must be handled by the canonical publisher.

## Status

The current chains contain only their BLOCKED genesis receipts: zero admitted measured
rows. The evidence services can run with that dataset. Real benchmark execution and
signed raw evidence remain the responsibility of the dedicated-node measurement producers.
