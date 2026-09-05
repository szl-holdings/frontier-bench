# SZL bench plane — operational handoff

## What is finished

`finish_bench_plane.py` is now a fail-closed deployment controller rather than a
success-printing shell wrapper. It:

1. acquires an atomic per-workdir lease and, for deployment, a lease scoped to the
   effective user across work directories to coordinate cooperating writers;
2. creates a fresh private bare Git object database for each repository and run, fetches
   only HTTPS `main`, checks the exact immutable revision is reachable, rejects symlinks,
   submodules, unsafe/colliding paths, and reads required blobs directly without checkout,
   hooks, filters, inherited or reused Git configuration, or inherited credentials;
3. verifies a reviewed manifest of repository test-file hashes but deliberately never
   executes unsigned repository code on the controller host;
4. independently parses every receipt with bounded input, duplicate-key, non-finite-number,
   nesting, aggregate-size, and exact metric-contract rejection;
5. recomputes each canonical SHA-256, validates the genesis trust roots and complete chain,
   binds every receipt to the correct plane and bench-node identity, requires an
   operator-provisioned HMAC envelope for every `MEASURED` row, and exports only those rows;
6. keeps `EMPTY_HONEST` separate from service state and records that it did not run a
   benchmark;
7. builds three minimal APIs from controller-owned standard-library code on the pinned
   multi-architecture Python base image
   `python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`;
8. invokes Git, NVIDIA tooling, and Docker only through resolved absolute system paths;
   Linux deployment is fixed to the local `/var/run/docker.sock` and an empty per-run
   Docker client configuration;
9. launches containers by immutable image ID, stages and witnesses every container on
   an ephemeral loopback port before cutover,
   validates run ID, plane, source revision, result digest, count, and exact result rows,
   then witnesses all three fixed ports again after an explicit restart;
10. performs a reversible fixed-port cutover, attempts restoration after failures and
   interruptions, and reports incomplete restoration as a distinct failure;
11. for a published run, authenticates the exact Hugging Face user, conditionally commits changed files among
   `README.md`, `index.html`, and `results.json` with a parent-commit precondition,
   reads every managed file back at the immutable commit, waits for the Space to report
   `RUNNING`, verifies the public bytes between two stable head/runtime observations, and
   makes a conditional compensating commit if a post-commit gate fails;
12. publishes a digest-bound page template: Web Crypto hashes the exact raw
   `results.json` bytes before parsing, then enforces the same exact source, machine,
   admission-metadata, and per-plane metric contracts. HMAC verification happens in the
   controller; the browser cannot independently verify the secret MAC;
13. after CLI/workdir/report initialization succeeds, writes an atomic JSON evidence
   report on success, failure, or interruption.

Tokens and the receipt-authentication key are accepted only through environment variables.
They are not accepted on the command line, printed, persisted, or passed to Git or Docker.
Runtime/security gates use explicit exceptions, not Python `assert`, so `python -O`
cannot disable them.

## Files in this handoff

- `finish_bench_plane.py` — production controller.
- `test_finish_bench_plane.py` — standard-library adversarial regression suite.
- `szl-bench-suite.README.md` — repaired Hugging Face static-Space metadata and truthful
  evidence boundary.
- `szl-bench-suite.index.html` — reviewed fail-closed public template. The controller
  replaces its single digest placeholder at publication time. Invalid, unavailable,
  or byte-mismatched data is shown as `UNAVAILABLE`, not as an honest empty set;
  unsupported “IN CODE” claims were
  removed.
- Local audit reports are generated under the selected work directory; workstation
  paths and machine inventories are not committed to this package.

## Validated here

The local audit path inspects reviewed immutable revisions reachable from fetched main
and records fetched main separately. Reviewed test-file manifests are checked without executing them. All
three genesis digests were
independently recomputed, all three chains contain one valid `BLOCKED` genesis, and the
merged state is:

```text
data_state       EMPTY_HONEST
measured_rows    0
measurements     NOT_PERFORMED
local_runtime    NOT_RUN_AUDIT_ONLY
publication      NOT_RUN_AUDIT_ONLY
source_identity  REVIEWED PINS; SIGNATURE PRESENCE IS RECORDED SEPARATELY
```

The controller regression suite covers optimization bypass, secret
isolation and bearer redaction, atomic writer leasing, hardware qualification, strict
JSON, genesis tampering, wrong-plane receipts, authenticated measurement admission,
exact metric contracts, deterministic empty output, reviewed Space asset pinning,
payload-byte binding, bare-executable rejection, exact HTTP identity/payload witnessing,
stable no-op publication, stale-parent rejection, and conditional remote rollback.

No container or public-runtime claim was made from this workstation. It is an Intel Core
Ultra 9 285H / 31.59 GiB Windows machine, not the declared i9-14900HX / RTX 4000 Ada
20 GB / 128 GB Linux node. Windows is intentionally audit-only. A separate live inventory
observed an RTX 5050 Laptop 8 GB, but the final isolated audit process could not initialize
NVML and therefore recorded no GPU rather than borrowing that observation. Docker Desktop's
engine was stopped in the earlier check. On September 5 the Hugging Face CLI is
authenticated as betterwithage through its existing cache; `HF_TOKEN` remains absent.

## Run on the dedicated node

Use a dedicated Linux node with Python 3.11 or newer, system-installed Git and Docker,
Docker BuildKit with the containerd image store enabled (required to retain build
attestations), a root-owned local `/var/run/docker.sock`, the NVIDIA driver/`nvidia-smi`,
and enough disk space for three images. Keep this package together. Windows can
run `--audit-only`; it cannot deploy.

First, verify source and receipt integrity without touching Docker or Hugging Face:

```bash
python -I -B test_finish_bench_plane.py
python -I -B finish_bench_plane.py \
  --audit-only \
  --target local \
  --workdir ~/szl-bench
```

Then verify the actual node and Docker daemon without changing services:

```bash
python -I -B finish_bench_plane.py \
  --preflight-only \
  --target local \
  --workdir ~/szl-bench
```

For node-local APIs only:

```bash
python -I -B finish_bench_plane.py \
  --target local \
  --workdir ~/szl-bench
```

That run can finish as `LOCAL_EVIDENCE_APIS_OPERATIONAL` with data state `EMPTY_HONEST`. It proves the
three exact local services at `127.0.0.1:7861`, `:7862`, and `:7863`; it does not claim a
public deployment or a measured benchmark.

For the complete public target, install `huggingface_hub` into the isolated operational
environment, provision the Hugging Face token without echoing it, and include both
reviewed Space files:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-publish.txt
read -r -s -p 'Hugging Face token: ' HF_TOKEN
export HF_TOKEN
printf '\n'

./.venv/bin/python -I -B finish_bench_plane.py \
  --preflight-only \
  --target published \
  --workdir ~/szl-bench \
  --space-readme ./szl-bench-suite.README.md \
  --space-index ./szl-bench-suite.index.html

./.venv/bin/python -I -B finish_bench_plane.py \
  --target published \
  --workdir ~/szl-bench \
  --space-readme ./szl-bench-suite.README.md \
  --space-index ./szl-bench-suite.index.html

unset HF_TOKEN
```

The default target is `local`; explicitly select `published` for publication. Missing authentication, invalid Space configuration,
failed immutable readback, provider state other than `RUNNING`, or public payload drift is
a nonzero failure. A token belonging to any identity other than `betterwithage` is also
rejected unless the expected identity is deliberately changed.

The pinned Hub client does not support `restart_space` for static Spaces. An unchanged
static deployment in a terminal stopped/error state reports
`STATIC_RUNTIME_RECOVERY_UNAVAILABLE` without a write; transient build states are still
waited on. Provider-side recovery is required in that case. See the
[Hub restart API contract](https://huggingface.co/docs/huggingface_hub/v1.30.0/en/package_reference/hf_api#huggingface_hub.HfApi.restart_space).

If any repository contains a `MEASURED` receipt, the run additionally requires exactly
32 random key bytes encoded as 64 hexadecimal characters in the fixed variable
`SZL_BENCH_RECEIPT_HMAC_KEY_HEX`. Its key ID is fixed to
`szl-bench-node-hmac-v1`. The same protected key must be used by the separately reviewed
receipt producer. Do not place it in Git, a receipt, the Space, a CLI argument, or the
work directory. Genesis-only `EMPTY_HONEST` audits do not need the key.

A post-commit provider/public failure triggers a conditional compensating commit only if
the Space head still equals this run's commit. Concurrent remote drift is never
overwritten. This minimizes failed releases but cannot provide atomic promotion: a bad
commit may be briefly visible or cached. A separate staging Space/provider promotion
primitive is required for true pre-publication runtime validation.

## Security and trust boundary

### Canonical static publication

`frontier-bench` is the sole canonical publisher. Its workflow now uses the reviewed
controller to export `README.md`, `index.html`, and `results.json` with
`--audit-only --export-space-bundle DIRECTORY`. The new export directory must be inside
the dedicated work directory. This path requires both reviewed Space assets and makes
no Docker or Hugging Face changes.

`tools/publish_space.py --bundle-dir DIRECTORY` independently verifies the source receipts
again, compares the exact exported bytes, and performs the provider commit/readback.
`--use-cached-auth` (also `--cached-auth`) explicitly uses an existing local login for a
direct operator release. Identical, healthy public content can be verified anonymously;
content and runtime writes require the owner credential. Scheduled publication is disabled
until a scoped `HF_TOKEN` Actions secret is installed. Manual workflow dispatch remains
available. The cached personal credential is not copied into CI.

When measured sources are deliberately reviewed and pinned, provision
`SZL_BENCH_RECEIPT_HMAC_KEY_HEX` as a protected Actions secret for the separately
controlled producer key. Both trusted-main export and publication re-admission receive
that key. Pull-request runs receive no receipt key and run fixture-based regression
tests only; live source admission runs on pushes to main and manual main dispatches.
This is not pre-merge receipt authentication. Protect main and review the controller
before provisioning secrets; no branch protection or credential is created by this release.

The active template lives in `deploy/bench-plane/szl-bench-suite.index.html`; the old
`site` files remain as historical source and are no longer selected by the publisher.

The reviewed commits must remain reachable from fetched main. They may be reviewed
ancestors: storing the controller in the engine repository must not require a commit
to contain its own hash. The report records both the admitted pin and observed main.

Run `node --test test_bench_surface.mjs` for the page regression suite. Its explicit
fixtures verify rendering; they are never published as measurements.

Treat the controller, its pinned asset digests, and this handoff as one release unit. An
administrator who can replace the controller can also replace its pins. Root/local
administrator, the OS kernel, installed Git HTTPS helper, Docker daemon, registry, NVIDIA
driver, DNS/TLS trust store, GitHub, and Hugging Face remain outside this controller's
attestation boundary. The per-EUID deployment lease coordinates cooperating runs; a stale lease after a
crash is deliberately not broken automatically. Inspect its `owner.json`, independently
confirm that the recorded process is gone, and remove only that run's `active` lease
directory before retrying.

The browser digest prevents independently modified or mismatched payload bytes from being
rendered against the reviewed page. It imposes no age-based freshness limit. It does not survive total compromise of the Space
account, where an attacker could replace both files. That requires an asymmetric signed
manifest with its public key anchored outside Hugging Face. Likewise, HMAC proves that a
holder of the shared key made an assertion; it does not prove the benchmark actually ran
and is not public non-repudiation.

## Current external state and blockers

This controller does not alter GitHub repositories or manufacture missing measurements.
The refreshed live inventory on 2026-09-05 is:

| Layer | Current state |
|---|---|
| Display source | Public reviewed revisions pinned by the controller; current commits have GitHub-verified signatures |
| GitHub CI before this release | All six display/harness test suites pass; a prior publisher run failed for a missing scoped Actions credential; upstream suspended scheduled publication |
| Branch protection | All six display/harness `main` branches are currently unprotected |
| Receipt chains | Three valid, unsigned integrity chains; each contains only its `BLOCKED` genesis |
| Receipt producer | No repository currently writes the flat display receipt schema end to end |
| Harness compatibility | The three dedicated harness receipt and metric schemas do not match the display contract |
| Hugging Face source | Public at commit `6d4f4cd3b8619c547c71d741283c8c63a4764ced` before this release |
| Hugging Face runtime | `RUNNING`, HTTP 200, at `https://betterwithage-szl-bench-suite.static.hf.space/` |
| Runtime witness | The public pages link `szl-holdings/lutar-runtime-witness`, but that repository is not addressable |

Publication evidence before this release:

- [canonical publisher credential failure](https://github.com/szl-holdings/frontier-bench/actions/runs/33968736210)
- [current Hugging Face Space](https://huggingface.co/spaces/betterwithage/szl-bench-suite)

The reviewed README and index in this bundle upgrade the running Space through the
canonical publisher. They do not supply missing real measurement inputs.

## What “real measurements” still require

The controller deliberately refuses to translate the current demos into `MEASURED`
receipts:

- Engine measurement needs one or more real OpenAI-compatible engine endpoints with a
  loaded, revision-pinned model. The current harness counts streaming chunks as tokens
  and does not bind enough workload or hardware provenance for publication.
- Retrieval measurement needs a versioned external corpus, queries, qrels, model revision,
  and raw-result hashes. The current dedicated CLI always runs a four-document fixture.
- Quantization measurement needs real model weights/inference, prompt or evaluation data,
  revision pins, throughput, memory, and quality observations. The current dedicated CLI
  quantizes deterministic synthetic Gaussian logits, not model weights.
- All three need a canonical producer for `szl-bench-receipt/v3` that binds raw artifact
  digests, exact display-source revision, observed hardware evidence, workload,
  configuration, previous receipt hash, audience, and the protected HMAC key. For public
  non-repudiation, replace the closed-system HMAC with a separately controlled asymmetric
  signer and externally anchored public key.

Until those inputs and producer paths exist, the only honest data state is
`EMPTY_HONEST`. The APIs and public surface can be operational with zero rows, but the
benchmark program itself is not complete and must not be represented as measured.

## Exit-code contract

| Code | Meaning |
|---:|---|
| 0 | Every gate required by the selected mode/target verified |
| 2 | Invalid CLI/work directory |
| 10 | Machine, tool, or Docker preflight failure |
| 11 | Another controller owns the writer lock |
| 20 | Repository fetch, pin, tree, or reviewed blob-manifest failure |
| 30 | Receipt/genesis/chain/metric/authentication failure |
| 31 | Result assembly failure |
| 40 | Immutable image build failure |
| 41 | Candidate/local runtime witness failure |
| 42 | Cutover failure with successful restoration |
| 50 | Required Hugging Face package, token, identity, or Space preflight failure |
| 51 | Compare-and-swap Hub commit failure |
| 52 | Immutable Hub readback mismatch |
| 53 | Provider or public-runtime witness failure |
| 60 | Local cutover or remote publication rollback failed/incomplete |
| 70 | Unexpected internal failure with durable report |
| 130 | Operator interruption |
