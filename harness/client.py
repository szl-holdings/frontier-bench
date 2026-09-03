"""Minimal OpenAI-compatible HTTP client used by the benchmark runner.

No vendor SDK dependency: any engine exposing a /v1/chat/completions
or /v1/completions endpoint can be measured with this client.
"""
import time
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional


@dataclass
class GenerationResult:
    ok: bool
    text: str = ""
    ttft_s: Optional[float] = None
    total_s: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    error: Optional[str] = None
    status_code: Optional[int] = None


def chat_completion(base_url: str, model: str, prompt: str, max_tokens: int = 128,
                     temperature: float = 0.0, timeout: float = 60.0,
                     stream: bool = True) -> GenerationResult:
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    t_start = time.perf_counter()
    ttft = None
    text_chunks = []
    usage = {}

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if stream:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if ttft is None:
                        ttft = time.perf_counter() - t_start
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    if "content" in delta and delta["content"]:
                        text_chunks.append(delta["content"])
                    if "usage" in obj and obj["usage"]:
                        usage = obj["usage"]
            else:
                body = json.loads(resp.read().decode("utf-8"))
                ttft = time.perf_counter() - t_start
                text_chunks.append(body["choices"][0]["message"]["content"])
                usage = body.get("usage", {})
    except urllib.error.HTTPError as e:
        return GenerationResult(ok=False, error=str(e), status_code=e.code)
    except urllib.error.URLError as e:
        return GenerationResult(ok=False, error=str(e))
    except Exception as e:
        return GenerationResult(ok=False, error=repr(e))

    total = time.perf_counter() - t_start
    return GenerationResult(
        ok=True,
        text="".join(text_chunks),
        ttft_s=ttft,
        total_s=total,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )


def health_check(base_url: str, timeout: float = 5.0) -> bool:
    for path in ("/health", "/v1/models", "/healthz"):
        url = base_url.rstrip("/") + path
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            continue
    return False
