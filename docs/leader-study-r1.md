# frontier-bench domain leader study r1 (propose-only)

Date: 2026-11-25. Method: web-verified leader landscape + governing-seat ratification (Kimi nonce T13-RULING-20261124-K1). Study pinned to frontier-bench HEAD 3ec9faac8050720810b0c819d5e6a91543fab61d, fetched 2026-11-25. This card is documentation only; it changes no code, CI, config, or existing PR status. All absorb actions are FUTURE PROPOSALS (non-authorizing); implementation and merges remain separately reviewed and gated. L Conjecture 1: OPEN (advisory, never a closed theorem). No speedup, energy, or proven-trust claims; upstream performance claims are labeled as upstream claims only. No unverified signing, attestation, or provenance-level claims; this study verifies nothing about this repo's artifacts.

## Domain: Vendor-neutral benchmarking of OpenAI-compatible inference engines, reporting only real measured latency and throughput while marking unconfigured engines BLOCKED rather than fabricating results.

## Leaders studied (web-verified 2026-11-25)
1. vLLM (https://github.com/vllm-project/vllm) - moat: A widely adopted serving engine with an OpenAI-compatible API and a large community around high-throughput LLM inference.
2. SGLang (https://github.com/sgl-project/sglang) - moat: An inference serving project combining structured generation support with an OpenAI-compatible serving interface and active performance engineering.
3. lm-evaluation-harness (https://github.com/EleutherAI/lm-evaluation-harness) - moat: A broadly used, reproducible framework for running many standardized language-model evaluations with explicit task configuration.

## Absorb actions (FUTURE PROPOSALS, non-authorizing)
1. (vLLM) Propose a documentation-only design note reviewing how frontier-bench describes vLLM configuration, OpenAI-compatible request coverage, and BLOCKED handling, with any upstream performance statements kept explicitly labeled as upstream claims. Cost: doc-only, about 0.5-1 maintainer day including review; limit: note only, no code, no infra changes.
2. (SGLang) Propose a documentation-only design note reviewing SGLang endpoint setup, workload comparability, and measurement-reporting language so missing configuration remains BLOCKED and no cross-engine advantage is implied without measurements. Cost: doc-only, about 0.5-1 maintainer day including review; limit: note only, no code, no infra changes.
3. (lm-evaluation-harness) Propose a documentation-only design note comparing its reproducibility and task-configuration practices with frontier-bench's benchmark metadata and result-labeling documentation, without treating evaluation scores as inference performance measurements. Cost: doc-only, about 0.5-1 maintainer day including review; limit: note only, no code, no infra changes.
4. Baseline preserve honestly: preserve Doctrine v11's requirement for real measured TTFT, p50, p95, p99, and throughput only, retain BLOCKED for unconfigured engines, never fabricate numbers, and keep all upstream performance claims explicitly labeled as upstream claims rather than findings of this study. Cost: doc-only, about 0.25 maintainer day for a documentation consistency review; limit: note only, no code, no infra changes.

## Do not do (per governing seat)

Do not merge, bypass the signature wall, or claim implementation beyond this card.
