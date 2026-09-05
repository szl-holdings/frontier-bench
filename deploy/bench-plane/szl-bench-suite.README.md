---
title: SZL Bench Suite
emoji: 📐
colorFrom: gray
colorTo: green
sdk: static
app_file: index.html
pinned: false
short_description: Receipt-gated engine, retrieval, and quantization evidence
---

# SZL Bench Suite

This static Space is the consolidated evidence surface for three SZL bench planes:

- **Engine** — rows admitted from `szl-holdings/frontier-bench` receipts.
- **Retrieval** — rows admitted from `szl-holdings/retrieval-bench` receipts.
- **Quantization** — rows admitted from `szl-holdings/quant-curve` receipts.

The page does not run benchmarks and does not turn demos, fixtures, projections, or
in-memory harness output into measurements. `results.json` is assembled only after
the deployment controller independently recomputes each receipt hash and chain,
checks the pinned source revision, requires an operator-provisioned HMAC on every
`MEASURED` envelope, uploads with a parent-commit precondition, and verifies immutable
and public readback.

`EMPTY_HONEST` means the validated payload contains zero admitted measured rows.
Local service health and public runtime health require separate evidence.
`UNAVAILABLE` means the payload could not be fetched or validated;
it is never displayed as an honest empty result set.

The existing `BLOCKED` genesis receipts are unsigned and establish integrity continuity,
not authorship. A measured row is rejected unless its source, workload, raw-artifact
digests, hardware-evidence digest, machine, time, method, and metrics are authenticated
with the operator-held key. HMAC authenticates an operator assertion; it is not an
independent witness that the workload ran. No separate runtime-witness repository is
claimed until an addressable, independently verifiable witness exists.

The browser verifies the SHA-256 digest of the exact result bytes and validates the
exported schema and authentication metadata. HMAC verification occurs in the controller;
the public page has neither the shared secret nor an independently verifiable signature.

Sources: [frontier-bench](https://github.com/szl-holdings/frontier-bench) ·
[retrieval-bench](https://github.com/szl-holdings/retrieval-bench) ·
[quant-curve](https://github.com/szl-holdings/quant-curve)
