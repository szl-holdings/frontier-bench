"""
Engine registry for the frontier inference benchmark harness.

Each engine is registered with:
- name: identifier used in reports
- endpoint_env: environment variable holding its OpenAI-compatible base URL
- kind: family of engine (server | local)
- notes: operational caveats
"""
from dataclasses import dataclass
from typing import Optional
import os


@dataclass(frozen=True)
class EngineSpec:
    name: str
    endpoint_env: str
    kind: str
    default_model: Optional[str] = None
    notes: str = ""

    def resolve_endpoint(self) -> Optional[str]:
        return os.environ.get(self.endpoint_env)

    def is_configured(self) -> bool:
        return self.resolve_endpoint() is not None


REGISTRY = {
    "vllm": EngineSpec(
        name="vllm",
        endpoint_env="VLLM_ENDPOINT",
        kind="server",
        notes="OpenAI-compatible server via `vllm serve`. Requires GPU.",
    ),
    "sglang": EngineSpec(
        name="sglang",
        endpoint_env="SGLANG_ENDPOINT",
        kind="server",
        notes="OpenAI-compatible server via `python -m sglang.launch_server`. Requires GPU.",
    ),
    "tgi": EngineSpec(
        name="tgi",
        endpoint_env="TGI_ENDPOINT",
        kind="server",
        notes="Hugging Face Text Generation Inference server.",
    ),
    "llama_cpp": EngineSpec(
        name="llama_cpp",
        endpoint_env="LLAMACPP_ENDPOINT",
        kind="server",
        notes="llama.cpp server (`llama-server`), OpenAI-compatible endpoint, CPU/GPU capable.",
    ),
    "mlx": EngineSpec(
        name="mlx",
        endpoint_env="MLX_ENDPOINT",
        kind="server",
        notes="mlx_lm.server on Apple Silicon. OpenAI-compatible endpoint.",
    ),
    "transformers": EngineSpec(
        name="transformers",
        endpoint_env="TRANSFORMERS_ENDPOINT",
        kind="server",
        notes="Reference HF Transformers generate() wrapped behind a local FastAPI server.",
    ),
}


def available_engines():
    """Return only engines whose endpoint env var is actually set."""
    return {k: v for k, v in REGISTRY.items() if v.is_configured()}


def unavailable_engines():
    """Return engines missing endpoint configuration (will be marked BLOCKED)."""
    return {k: v for k, v in REGISTRY.items() if not v.is_configured()}
