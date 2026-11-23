# Leader-Study Upgrade Plan - W27 loop-6 - 2026-11-23

Status: PROPOSE-ONLY DRAFT - DO NOT MERGE. Owner-gated: merging requires governing approval and the estate signature-wall owner unlock.

Repo: frontier-bench - Frontier Inference Engine Benchmark Harness — vendor-neutral OpenAI-compatible benchmarking for vLLM, SGLang, TGI, llama.cpp, MLX, Transformers. Real measured TTFT/p50/p95/p99/throughput only; unconfigured engines report BLOCKED, never fabricated numbers. Doctrine v11. Apache-2.0.

Frame: OUROBOROS loop-6 leader study. Governing seat: Kimi kimi-k3.1-pro (json_ok, think off). Advisory seats: Astra/Sol/Claude community rails (provenance unverified, advisory only). Leader evidence web-verified 2026-11-23.

## Leader moats to absorb
- vLLM: High-throughput OpenAI-compatible inference serving with PagedAttention, prefix caching since v0.6, and benchmark_serving doctrine covering TTFT and TPOT p50/p99 tail control; approximately 12.5k tok/s class on H100 8B. Moat: PagedAttention memory management, prefix caching, and an established serving-benchmark methodology that makes latency tails and throughput first-class measurements.
- SGLang: OpenAI-compatible inference serving with RadixAttention radix-tree prefix caching, TTFT reductions up to 6.4x on prefix-heavy traffic, and approximately 16.2k tok/s class throughput. Moat: Radix-tree reuse of shared prefixes, producing large TTFT gains on prefix-heavy workloads while sustaining high throughput.
- TGI: One of the named OpenAI-compatible inference engines in the vendor-neutral benchmark target set. Moat: The supplied facts name TGI as a leader but provide no specific mechanism or measured result; no further moat is asserted.
- MLPerf Inference v5.0: Vendor-neutral inference submission rules. Moat: Standardized submission rules provide a common basis for comparable, reviewable benchmark claims across vendors.
- EleutherAI lm-evaluation-harness: A named leader in the evaluation-harness ecosystem. Moat: The supplied facts identify it as a leader but give no implementation details; its relevance here is evaluation-harness discipline rather than an asserted performance result.

## Honest gaps (grounded in repo facts)
- The README declares the current public state EMPTY_HONEST: no measured rows are admitted on the engine, retrieval, or quantization planes, so there is no public measured evidence yet for TTFT, p50/p95/p99, or throughput.
- No dedicated measurement node is provisioned, and hardware identity is checked against the dedicated-node policy; consequently the harness cannot presently back its measurement plane with the intended dedicated hardware evidence.
- The receipt HMAC authenticates an operator assertion and no independent hardware witness is claimed, leaving hardware identity dependent on operator attestation rather than independent verification.
- The supplied facts do not demonstrate TPOT p50/p99 reporting or controlled prefix-heavy benchmark scenarios comparable to the stated vLLM and SGLang doctrines.
- The README excerpt describing the evidence controller ends mid-sentence at 'exports one digest-bo', so the complete export and readback contract cannot be established from the provided facts.
- The facts provide pushed_at=null; no inference about implementation completeness or activity is made beyond recording that no push timestamp was supplied.

## Dosed upgrade plan
- Provision and document a dedicated measurement node before attempting any measured publication; until then retain BLOCKED or EMPTY_HONEST states and publish no performance numbers.
- Define a minimal vendor-neutral run contract covering engine and version, model and revision, hardware identity, date, method, request workload, stream versus non-stream mode, concurrency, and prefix-sharing conditions.
- Pilot one receipt-gated streaming run on the dedicated node, retaining token-observed TTFT, rejecting empty generated-text responses, and recording latency percentiles and throughput without cross-engine claims.
- Add explicit TPOT and tail-latency fields plus cold-prefix and prefix-heavy scenario labels to the run contract; admit results only when the required measurements and receipts are present.
- Have the evidence controller continue reading reviewed immutable Git objects without executing producer code, verify all three receipt chains, require authentication for measured rows, and test fail-closed behavior with missing, invalid, or unauthenticated evidence.
- Complete and review the truncated digest/export documentation, then exercise the consolidated Space readback separately from source, receipt admission, CI, and service-runtime evidence.
- Publish only the admitted pilot rows with hardware, date, and method attached; expand engines and workloads in later independently receipted batches rather than projecting leader numbers onto frontier-bench.
- Risk note: The principal risk is converting an honest empty surface into an overclaimed one: running on non-dedicated hardware, relying on operator-attested identity as though independently witnessed, comparing unlike workloads, or weakening receipt and readback gates could produce misleading numbers. Prefix-cache and tail-latency additions also increase workload and metadata complexity, so each expansion must remain small, measured, and fail-closed.
- Propose-only note: These are proposed changes only. No repository files, receipts, infrastructure, CI, or public Space content have been modified, and no benchmark result should be admitted until its actual evidence passes the existing gates.

## Advisory notes (unverified rails, non-binding)
- astra: frontier-bench: hardware-specific kernel optimization suites retrieval-bench: industry-standard gold-set evaluation pipelines szl-calibration: probabilistic forecasting uncertainty baselines
- sol: frontier-bench: Hardware-specific kernel optimization profiles retrieval-bench: End-to-end latency-throughput Pareto frontiers szl-calibration: Domain-specific uncertainty-aware calibration baselines
- claude: frontier-bench: Hardware-specific kernel optimization profiles retrieval-bench: Domain-specific synthetic query generation pipelines szl-calibration: Out-of-distribution uncertainty quantification frameworks

## DO NOT MERGE
This plan is a proposal artifact of the 10-pass OUROBOROS loop (loop-6 of 10). No code, no CI, no behavior changes in this file. A reviewer with authority may convert to a work order later.