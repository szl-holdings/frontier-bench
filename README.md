# Frontier Bench

Measured inference-engine evidence for the SZL stack — the public bench for the **szl-forge** engine.

## What this is

The honest companion to every engine claim. If the estate says the engine is fast, this is where the number lives — with the machine, the date, and the method attached. Unmeasured claims say so.

## Guarantees

- **Honest benchmarks** — every published number is labeled with hardware, date, and method; projections are marked as projections or omitted.
- **Receipts** — benchmark runs are hashed and chained so results history is tamper-evident.
- **Portable engine evidence** — the same tree compiles for CPU, CUDA, and Metal targets.
- **Fail-closed display** — the public surface renders only verified results; anything unverifiable appears as absent.

## Public surface

The consolidated public bench lives at [betterwithage/szl-bench-suite](https://huggingface.co/spaces/betterwithage/szl-bench-suite) (Engine Bench tab) — one evidence surface for engine, retrieval, and quantization claims.

Hardware truth is sourced from the published runtime witness ([szl-holdings/lutar-runtime-witness](https://github.com/szl-holdings/lutar-runtime-witness)), whose verifier recomputes every digest from source and fails closed on drift.

**Division of labor:** this repo is the sole publisher for the consolidated public Space. It checks out only receipt data from the retrieval and quantization sources at exact revisions, verifies all three planes with the canonical verifier here, combines the admitted results, and publishes one atomic Space commit. Producer code never runs in the credential-bearing publisher job. The measurement harness that produces receipted engine runs lives in [szl-holdings/szl-engine-bench](https://github.com/szl-holdings/szl-engine-bench); the Wave 1 consolidated bakeoff report is [szl-holdings/szl-wave1-report](https://github.com/szl-holdings/szl-wave1-report).

## Status

Foundation (2026-09-03): honest-results contract, bench schema, and verifier in place. Measured results land here from the dedicated GPU node as runs complete.
