# Frontier Inference Engine Benchmark Harness

Real, runnable benchmark harness for comparing OpenAI-compatible LLM inference
engines (vLLM, SGLang, TGI, llama.cpp, MLX, Transformers) on identical prompts.

## Design guarantees

- No fabricated data. Engines without a configured endpoint are reported
  as BLOCKED, never simulated or guessed.
- No vendor lock-in. Any engine exposing an OpenAI-compatible
  /v1/chat/completions endpoint can be registered and measured.
- Real timing only. TTFT and total latency come from actual HTTP
  streaming responses, measured with time.perf_counter().
- Health-gated. A request is only issued to an engine after its health
  endpoint responds successfully.

## Usage

1. Start the engine(s) you want to test (example for vLLM):
   ```bash
   vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
   export VLLM_ENDPOINT=http://localhost:8000
   ```

2. Repeat for any other engine (SGLANG_ENDPOINT, TGI_ENDPOINT,
   LLAMACPP_ENDPOINT, MLX_ENDPOINT, TRANSFORMERS_ENDPOINT).

3. Run the benchmark:
   ```bash
   python -m harness.runner --config configs/prompts.json --repeats 5 --out results.json
   ```

4. Inspect results.json — each engine will show either:
   - "status": "MEASURED" with p50/p95/p99 TTFT, p50/p95/p99 total latency,
     and mean throughput (tokens/sec), or
   - "status": "BLOCKED" with the reason (endpoint not configured, or
     health check failed).

## Tests

```bash
python tests/test_metrics.py          # unit tests for percentile/statistics logic
python tests/mock_server.py &         # starts a local mock OpenAI-compatible server
VLLM_ENDPOINT=http://127.0.0.1:8899 python -m harness.runner --config configs/prompts.json --repeats 2
```

The unit tests and a full end-to-end run against the mock server (real HTTP
round trips, real percentile math) passed before this code was pushed.

## Files

- harness/engine_registry.py — engine definitions, env-var-based endpoint resolution
- harness/client.py — minimal dependency-free OpenAI-compatible streaming client
- harness/metrics.py — percentile and throughput aggregation (no external stats libs)
- harness/runner.py — CLI entrypoint tying it together
- configs/prompts.json — shared prompt set + per-engine model identifiers
- tests/ — unit tests and a mock server for full pipeline validation

## Extending

To add a new engine, add an EngineSpec entry to REGISTRY in
engine_registry.py with its endpoint env var name, then set that env var
before running. No other code changes needed since the client speaks the
generic OpenAI chat-completions protocol.
