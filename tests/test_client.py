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


class UsageOnlyEventTests(unittest.TestCase):
    def test_empty_choices_usage_events_do_not_abort_or_start_ttft(self):
        for usage_first in (False, True):
            with self.subTest(usage_first=usage_first):
                usage = _event({"choices": [], "usage": {"completion_tokens": 1}})
                text = _event({"choices": [{"delta": {"content": "hello"}}]})
                response = _Response(lines=([usage, text] if usage_first else [text, usage]) + [b"data: [DONE]\n\n"])
                with patch("harness.client.urllib.request.urlopen", return_value=response), patch(
                    "harness.client.time.perf_counter", side_effect=[10.0, 10.4, 10.9]
                ):
                    result = chat_completion("http://engine.invalid", "model", "prompt")
                self.assertTrue(result.ok, result.error)
                self.assertEqual(result.text, "hello")
                self.assertAlmostEqual(result.ttft_s, 0.4)
                self.assertEqual(result.completion_tokens, 1)

    def test_usage_only_empty_choices_never_prove_generated_text(self):
        response = _Response(lines=[_event({"choices": [], "usage": {"completion_tokens": 9}}), b"data: [DONE]\n\n"])
        with patch("harness.client.urllib.request.urlopen", return_value=response):
            result = chat_completion("http://engine.invalid", "model", "prompt")
        self.assertFalse(result.ok)
        self.assertIsNone(result.ttft_s)
        self.assertEqual(result.error, "response contained no generated text")

    def test_invalid_usage_returns_failed_sample_instead_of_escaping(self):
        for usage in ([], [1], "bad", 1):
            for stream in (False, True):
                with self.subTest(usage=usage, stream=stream):
                    response = _Response(
                        lines=[_event({"choices": [{"delta": {"content": "hello"}}], "usage": usage})],
                        body=json.dumps({"choices": [{"message": {"content": "hello"}}], "usage": usage}).encode(),
                    )
                    with patch("harness.client.urllib.request.urlopen", return_value=response):
                        result = chat_completion("http://engine.invalid", "model", "prompt", stream=stream)
                    self.assertFalse(result.ok)
                    self.assertIn("usage must be an object", result.error)

    def test_null_usage_is_optional_metadata(self):
        response = _Response(lines=[_event({"choices": [{"delta": {"content": "hello"}}], "usage": None}), b"data: [DONE]\n\n"])
        with patch("harness.client.urllib.request.urlopen", return_value=response):
            result = chat_completion("http://engine.invalid", "model", "prompt")
        self.assertTrue(result.ok)
        self.assertIsNone(result.completion_tokens)


# Offline protocol admission regressions. No engine or model is contacted.
DONE = b'data: [DONE]\n\n'

def event(value):
    return ('data: ' + json.dumps(value) + '\n\n').encode()

def text(value='hello'):
    return event({'choices': [{'delta': {'content': value}}]})

class Response:

    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self.lines)

class StreamProtocolTests(unittest.TestCase):

    def check_stream(self, lines, expected_error=None):
        with patch('harness.client.urllib.request.urlopen', return_value=Response(lines)):
            result = chat_completion('http://synthetic.invalid', 'fixture', 'fixture')
        if expected_error:
            self.assertFalse(result.ok)
            self.assertIn(expected_error, result.error)
            self.assertEqual(result.text, '')
            self.assertIsNone(result.ttft_s)
        else:
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.text, 'hello')
        return result

    def test_complete_text(self):
        self.check_stream([text(), DONE])

    def test_text_then_eof(self):
        self.check_stream([text()], 'before the [DONE]')

    def test_empty_eof(self):
        self.check_stream([], 'before the [DONE]')

    def test_invalid_json_after_text(self):
        self.check_stream([text(), b'data: {bad}\n', DONE], 'invalid JSON')

    def test_provider_error_after_text(self):
        self.check_stream([text(), event({'error': {'message': 'private'}}), DONE], 'provider error')

    def test_usage_and_nullable_role(self):
        self.check_stream([event({'choices': [{'delta': {'role': 'assistant', 'content': None}}]}), text(), event({'choices': [], 'usage': {'completion_tokens': 1}}), DONE])

    def test_empty_completed_stream_is_not_success(self):
        self.check_stream([DONE], 'no generated text')

    def test_utf8_error_rejects(self):
        self.check_stream([text(), b'data: \xff\n', DONE], 'UnicodeDecodeError')

    def test_comments_remain_allowed(self):
        self.check_stream([b': keepalive\n', text(), DONE])

    def test_empty_string_is_not_a_text_event(self):
        self.check_stream([text(''), text(), DONE])

def install_case(name, payload, error):

    def case(self):
        self.check_stream([text(), event(payload), DONE], error)
    setattr(StreamProtocolTests, 'test_' + name, case)
for name, value in [('null', None), ('list', []), ('number', 17), ('bool', True), ('string', 'bad')]:
    install_case('object_' + name, value, 'must be an object')
for name, value in [('null', None), ('dict', {}), ('number', 17), ('bool', False), ('string', 'bad')]:
    install_case('choices_' + name, {'choices': value}, 'choices must be an array')
for name, value in [('null', None), ('list', []), ('number', 17), ('bool', False), ('string', 'bad')]:
    install_case('choice_' + name, {'choices': [value]}, 'choice must be an object')
for name, value in [('null', None), ('list', []), ('number', 17), ('bool', False), ('string', 'bad')]:
    install_case('delta_' + name, {'choices': [{'delta': value}]}, 'delta must be an object')
for name, value in [('list', ['bad']), ('dict', {'bad': 1}), ('number', 17), ('bool', True)]:
    install_case('content_' + name, {'choices': [{'delta': {'content': value}}]}, 'content must be a string or null')
install_case('invalid_second_choice', {'choices': [{'delta': {}}, 17]}, 'choice must be an object')

# Missing members differ from present empty metadata containers.
def install_missing_member_case(name, payload, error):
    for position in ("before", "after"):
        def case(self, payload=payload, error=error, position=position):
            chunks = [event(payload), text()] if position == "before" else [text(), event(payload)]
            self.check_stream(chunks + [DONE], error)
        setattr(StreamProtocolTests, "test_missing_" + name + "_" + position, case)

install_missing_member_case("choices_empty_object", {}, "choices must be an array")
install_missing_member_case("choices_usage_object", {"usage": {"completion_tokens": 1}}, "choices must be an array")
install_missing_member_case("delta_empty_choice", {"choices": [{}]}, "delta must be an object")
install_missing_member_case("delta_index_only", {"choices": [{"index": 0}]}, "delta must be an object")
install_missing_member_case("delta_finish_only", {"choices": [{"index": 0, "finish_reason": "stop"}]}, "delta must be an object")
install_missing_member_case("delta_second_choice", {"choices": [{"delta": {}}, {"index": 1}]}, "delta must be an object")

def explicit_empty_delta_finish_remains_valid(self):
    self.check_stream([text(), event({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}), DONE])
StreamProtocolTests.test_explicit_empty_delta_finish_remains_valid = explicit_empty_delta_finish_remains_valid

def explicit_usage_array_preserves_counts(self):
    result = self.check_stream([text(), event({"choices": [], "usage": {"completion_tokens": 2, "prompt_tokens": 3}}), DONE])
    self.assertEqual(result.completion_tokens, 2)
    self.assertEqual(result.prompt_tokens, 3)
StreamProtocolTests.test_explicit_usage_array_preserves_counts = explicit_usage_array_preserves_counts

class UsageTokenAdmissionTests(unittest.TestCase):
    def request(self, usage_payload, stream=True, prefix=()):
        # Every transport response is synthetic; no engine is contacted.
        response = _Response(
            lines=list(prefix) + [text(), event({"choices": [], **usage_payload}), DONE],
            body=json.dumps({"choices": [{"message": {"content": "hello"}}], **usage_payload}).encode(),
        )
        with patch("harness.client.urllib.request.urlopen", return_value=response):
            return chat_completion("http://synthetic.invalid", "fixture", "fixture", stream=stream)

    def test_invalid_counts_return_empty_failed_samples(self):
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            for value in (True, False, -1, 1.5, "2", [], {}, float("inf"), float("nan")):
                for stream in (False, True):
                    with self.subTest(field=field, value=value, stream=stream):
                        result = self.request({"usage": {field: value}}, stream)
                        self.assertFalse(result.ok)
                        self.assertIn(field + " must be a non-negative integer or null", result.error)
                        self.assertEqual(result.text, "")
                        self.assertIsNone(result.ttft_s)
                        self.assertIsNone(result.total_s)
                        self.assertIsNone(result.prompt_tokens)
                        self.assertIsNone(result.completion_tokens)

    def test_invalid_earlier_usage_cannot_be_replaced_by_valid_final_usage(self):
        result = self.request({"usage": {"completion_tokens": 1}}, prefix=[
            event({"choices": [], "usage": {"completion_tokens": -1}}),
        ])
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "")
        self.assertIsNone(result.completion_tokens)

    def test_optional_usage_and_valid_integer_counts_remain_compatible(self):
        for payload in ({}, {"usage": None}, {"usage": {}},
                        {"usage": {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}},
                        {"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}},
                        {"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}):
            for stream in (False, True):
                with self.subTest(payload=payload, stream=stream):
                    result = self.request(payload, stream)
                    self.assertTrue(result.ok, result.error)
                    expected = payload.get("usage") or {}
                    self.assertEqual(result.prompt_tokens, expected.get("prompt_tokens"))
                    self.assertEqual(result.completion_tokens, expected.get("completion_tokens"))

    def test_runner_retains_valid_sample_after_malformed_usage(self):
        from harness.runner import run_engine
        from unittest.mock import Mock
        spec = Mock()
        spec.resolve_endpoint.return_value = "http://synthetic.invalid"
        invalid = _Response(lines=[text(), event({"choices": [], "usage": {"completion_tokens": True}}), DONE])
        valid = _Response(lines=[text(), event({"choices": [], "usage": {"completion_tokens": 2}}), DONE])
        with patch("harness.runner.health_check", return_value=True), patch(
            "harness.client.urllib.request.urlopen", side_effect=[invalid, valid]
        ), patch("harness.client.time.perf_counter", side_effect=iter(range(20))):
            result = run_engine("fixture", spec, ["prompt"], "fixture", 2, 8)
        self.assertEqual(result["status"], "MEASURED")
        self.assertEqual(result["summary"]["n_requests"], 2)
        self.assertEqual(result["summary"]["n_success"], 1)
        self.assertEqual(result["summary"]["n_failed"], 1)
        self.assertEqual(len(result["summary"]["errors"]), 1)
        self.assertIn("completion_tokens", result["summary"]["errors"][0])
        self.assertIsNotNone(result["summary"]["p50_total_ms"])

if __name__ == '__main__':
    unittest.main()
