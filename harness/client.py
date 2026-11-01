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


def _validated_usage(value):
    """Validate each usage event before it can enter or replace sample metadata."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("usage must be an object")
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        count = value.get(field)
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError(field + " must be a non-negative integer or null")
    return value


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
                saw_done = False
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if chunk == "[DONE]":
                        saw_done = True
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError as exc:
                        raise ValueError("stream contained invalid JSON data") from exc
                    if not isinstance(obj, dict):
                        raise ValueError("stream JSON data must be an object")
                    if "error" in obj:
                        raise ValueError("stream contained a provider error")
                    choices = obj.get("choices")
                    if not isinstance(choices, list):
                        raise ValueError("choices must be an array")
                    # A valid usage-only SSE chunk has an empty choices array.
                    # It must preserve usage without fabricating text or TTFT.
                    for choice in choices:
                        if not isinstance(choice, dict):
                            raise ValueError("stream choice must be an object")
                        delta = choice.get("delta")
                        if not isinstance(delta, dict):
                            raise ValueError("stream delta must be an object")
                        content = delta.get("content")
                        # OpenAI role/finish metadata may omit content or use null.
                        if content is not None and not isinstance(content, str):
                            raise ValueError("stream content must be a string or null")
                    delta = choices[0]["delta"] if choices else {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        # Metadata-only SSE events (for example, the initial
                        # assistant-role event) are not generated tokens.  Stop
                        # the TTFT clock only when observable text arrives.
                        if ttft is None:
                            ttft = time.perf_counter() - t_start
                        text_chunks.append(content)
                    if obj.get("usage") is not None:
                        usage = _validated_usage(obj["usage"])
                if not saw_done:
                    raise ValueError("stream ended before the [DONE] event")
            else:
                body = json.loads(resp.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                if isinstance(content, str) and content:
                    text_chunks.append(content)
                usage = _validated_usage(body.get("usage"))
    except urllib.error.HTTPError as e:
        return GenerationResult(ok=False, error=str(e), status_code=e.code)
    except urllib.error.URLError as e:
        return GenerationResult(ok=False, error=str(e))
    except Exception as e:
        return GenerationResult(ok=False, error=repr(e))

    total = time.perf_counter() - t_start
    text = "".join(text_chunks)
    if not text:
        return GenerationResult(
            ok=False,
            total_s=total,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            error="response contained no generated text",
        )
    return GenerationResult(
        ok=True,
        text=text,
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
