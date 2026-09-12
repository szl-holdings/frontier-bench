"""Regression tests for benchmark timing and response admission."""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from harness.client import chat_completion


class _Response:
    def __init__(self, lines=(), body=b""):
        self._lines = list(lines)
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._body


def _event(payload):
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


class StreamingTimingTests(unittest.TestCase):
    def test_ttft_ignores_role_only_metadata_event(self):
        response = _Response(
            lines=[
                _event({"choices": [{"delta": {"role": "assistant"}}]}),
                _event({"choices": [{"delta": {"content": "hello"}}]}),
                _event(
                    {
                        "choices": [{"delta": {}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                    }
                ),
                b"data: [DONE]\n\n",
            ]
        )
        with patch("harness.client.urllib.request.urlopen", return_value=response), patch(
            "harness.client.time.perf_counter", side_effect=[10.0, 10.4, 10.9]
        ):
            result = chat_completion("http://engine.invalid", "model", "prompt")

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "hello")
        self.assertAlmostEqual(result.ttft_s, 0.4)
        self.assertAlmostEqual(result.total_s, 0.9)
        self.assertEqual(result.completion_tokens, 1)

    def test_empty_stream_fails_closed_even_when_usage_claims_tokens(self):
        response = _Response(
            lines=[
                _event({"choices": [{"delta": {"role": "assistant"}}]}),
                _event(
                    {
                        "choices": [{"delta": {}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 8},
                    }
                ),
                b"data: [DONE]\n\n",
            ]
        )
        with patch("harness.client.urllib.request.urlopen", return_value=response), patch(
            "harness.client.time.perf_counter", side_effect=[20.0, 20.5]
        ):
            result = chat_completion("http://engine.invalid", "model", "prompt")

        self.assertFalse(result.ok)
        self.assertIsNone(result.ttft_s)
        self.assertEqual(result.total_s, 0.5)
        self.assertEqual(result.completion_tokens, 8)
        self.assertEqual(result.error, "response contained no generated text")

    def test_nonstream_response_does_not_mislabel_full_latency_as_ttft(self):
        body = json.dumps(
            {
                "choices": [{"message": {"content": "complete response"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }
        ).encode("utf-8")
        with patch(
            "harness.client.urllib.request.urlopen", return_value=_Response(body=body)
        ), patch("harness.client.time.perf_counter", side_effect=[30.0, 30.7]):
            result = chat_completion(
                "http://engine.invalid", "model", "prompt", stream=False
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "complete response")
        self.assertIsNone(result.ttft_s)
        self.assertAlmostEqual(result.total_s, 0.7)

    def test_nonstream_empty_response_fails_closed(self):
        body = json.dumps(
            {
                "choices": [{"message": {"content": ""}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 0},
            }
        ).encode("utf-8")
        with patch(
            "harness.client.urllib.request.urlopen", return_value=_Response(body=body)
        ), patch("harness.client.time.perf_counter", side_effect=[40.0, 40.2]):
            result = chat_completion(
                "http://engine.invalid", "model", "prompt", stream=False
            )

        self.assertFalse(result.ok)
        self.assertIsNone(result.ttft_s)
        self.assertEqual(result.error, "response contained no generated text")


if __name__ == "__main__":
    unittest.main()
