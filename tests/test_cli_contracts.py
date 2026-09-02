from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from candy_eval import DEFAULT_MODEL, normalize_endpoint, parse_args, test_response
from cup_eval import parse_args as parse_cup_args
from response_client import AccountTarget
from responses_stream import ResponseResult
from sub2api_eval import parse_args as parse_sub2api_eval_args, run_one
from sub2api_info_extract import parse_args as parse_extract_args


class CandyEvalCLIContractTest(unittest.TestCase):
    def test_url_and_key_are_the_only_required_cli_arguments(self) -> None:
        argv = ["candy_eval.py", "--url", "https://example.com", "--key", "secret"]
        with patch.object(sys, "argv", argv):
            args = parse_args()

        self.assertEqual("https://example.com", args.url)
        self.assertEqual("secret", args.key)
        self.assertEqual(DEFAULT_MODEL, args.model)
        self.assertEqual("responses", args.protocol)
        self.assertEqual(600.0, args.timeout)

    def test_default_protocol_stays_responses_for_claude_model(self) -> None:
        expected = ResponseResult("21", 1, 1, 1)
        with patch("candy_eval.stream_response_request", return_value=expected) as request:
            result = test_response("https://example.com", "secret", "claude-opus-5")

        self.assertEqual(expected, result)
        self.assertEqual("https://example.com/v1/responses", request.call_args.args[0])
        self.assertEqual(600.0, request.call_args.kwargs["timeout"])

    def test_anthropic_protocol_is_selected_explicitly(self) -> None:
        expected = ResponseResult("21", 1, 1, None)
        with patch("candy_eval.stream_anthropic_request", return_value=expected) as request:
            result = test_response(
                "https://example.com/v1/responses",
                "secret",
                protocol="anthropic",
            )

        self.assertEqual(expected, result)
        self.assertEqual("https://example.com/v1/messages", request.call_args.args[0])
        self.assertEqual(600.0, request.call_args.kwargs["timeout"])

    def test_common_provider_urls_are_normalized_without_duplicate_v1(self) -> None:
        cases = {
            "https://example.com": "https://example.com/v1/responses",
            "https://example.com/v1": "https://example.com/v1/responses",
            "https://example.com/v1/responses": "https://example.com/v1/responses",
            "https://example.com/responses": "https://example.com/responses",
            "https://example.com/v1/chat/completions": "https://example.com/v1/responses",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(expected, normalize_endpoint(source, "responses"))

    @patch("sub2api_eval.evaluate_once")
    def test_sub2api_eval_delegates_each_run_to_candy_eval(self, evaluate_once) -> None:
        evaluate_once.return_value = {
            "run": 1, "text": "21", "correct": True, "input_tokens": 10,
            "output_tokens": 5, "reasoning_tokens": 3, "attempts": 1,
            "elapsed_seconds": 1.0, "error": None,
        }
        target = AccountTarget("provider", "https://example.com/v1/responses", "secret", "model")
        args = SimpleNamespace(reasoning_effort="high", timeout=10, protocol="anthropic")

        _, correct, record = run_one(target, 1, args)

        self.assertTrue(correct)
        self.assertEqual("provider", record["account"])
        evaluate_once.assert_called_once_with(
            1, target.responses_url, target.api_key, model=target.model,
            reasoning_effort="high", timeout=10, protocol="anthropic",
        )

    def test_sub2api_entry_defaults_match_direct_entry(self) -> None:
        with patch.object(sys, "argv", ["sub2api_eval.py"]):
            eval_args = parse_sub2api_eval_args()
        with patch.object(sys, "argv", ["sub2api_info_extract.py"]):
            extract_args = parse_extract_args()

        self.assertEqual(DEFAULT_MODEL, eval_args.model)
        self.assertEqual("responses", eval_args.protocol)
        self.assertEqual(DEFAULT_MODEL, extract_args.model)
        self.assertEqual(600.0, eval_args.timeout)
        self.assertEqual(600.0, extract_args.timeout)

    def test_cup_entry_uses_the_same_connection_contract(self) -> None:
        argv = ["cup_eval.py", "--url", "https://example.com", "--key", "secret"]
        with patch.object(sys, "argv", argv):
            args = parse_cup_args()

        self.assertEqual("https://example.com", args.url)
        self.assertEqual("secret", args.key)
        self.assertEqual(DEFAULT_MODEL, args.model)
        self.assertEqual("responses", args.protocol)
        self.assertEqual(600.0, args.timeout)

    def test_timeout_can_be_overridden(self) -> None:
        argv = [
            "candy_eval.py", "--url", "https://example.com", "--key", "secret",
            "--timeout", "42.5",
        ]
        with patch.object(sys, "argv", argv):
            self.assertEqual(42.5, parse_args().timeout)

    def test_documented_protocol_aliases_are_normalized(self) -> None:
        for supplied, expected in (("response", "responses"), ("anthropics", "anthropic")):
            argv = [
                "candy_eval.py", "--url", "https://example.com", "--key", "secret",
                "--protocol", supplied,
            ]
            with self.subTest(protocol=supplied), patch.object(sys, "argv", argv):
                self.assertEqual(expected, parse_args().protocol)


if __name__ == "__main__":
    unittest.main()
