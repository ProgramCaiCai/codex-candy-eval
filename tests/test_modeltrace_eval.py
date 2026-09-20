from __future__ import annotations

import json
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from response_client import AccountTarget
from responses_stream import ResponseResult
from responses_stream import _parse_sse_response
from candy_eval import test_response as send_test_response


def number_text(count: int) -> str:
    return ",".join(str(index % 355 + 1) for index in range(count))


class ModelTraceEvalTest(unittest.TestCase):
    def test_responses_stream_enforces_total_deadline(self) -> None:
        stream = io.BytesIO(
            b'data: {"type":"response.output_text.delta","delta":"1"}\n\n'
        )
        with patch("responses_stream.time.monotonic", return_value=2.0):
            with self.assertRaisesRegex(TimeoutError, "total request timeout"):
                _parse_sse_response(stream, deadline=1.0)

    def test_4096_token_limit_maps_to_both_protocol_payloads(self) -> None:
        expected = ResponseResult(number_text(80), 10, 20, 5)
        with patch("candy_eval.stream_response_request", return_value=expected) as send:
            send_test_response("https://example.com", "secret", max_tokens=4096)
        self.assertEqual(4096, send.call_args.args[1]["max_output_tokens"])

        with patch("candy_eval.stream_anthropic_request", return_value=expected) as send:
            send_test_response(
                "https://example.com", "secret", protocol="anthropic", max_tokens=4096,
            )
        self.assertEqual(4096, send.call_args.args[1]["max_tokens"])

    def test_reasoning_effort_defaults_to_low(self) -> None:
        from modeltrace_eval import parse_args
        from sub2api_modeltrace_eval import parse_args as parse_sub2api_args

        direct = ["modeltrace_eval.py", "--url", "https://example.com", "--key", "secret"]
        with patch("sys.argv", direct):
            direct_args = parse_args()
            self.assertEqual("low", direct_args.reasoning_effort)
            self.assertEqual(3, direct_args.max_attempts)
            self.assertEqual(180.0, direct_args.timeout)
            self.assertEqual(0, direct_args.request_retries)
        with patch("sys.argv", ["sub2api_modeltrace_eval.py"]):
            sub2api_args = parse_sub2api_args()
            self.assertEqual("low", sub2api_args.reasoning_effort)
            self.assertEqual(3, sub2api_args.max_attempts)
            self.assertEqual(180.0, sub2api_args.timeout)
            self.assertEqual(0, sub2api_args.request_retries)

    def test_direct_anthropic_url_is_not_pre_normalized_as_responses(self) -> None:
        from modeltrace_eval import main

        argv = [
            "modeltrace_eval.py", "--url", "https://example.com/v1/messages",
            "--key", "secret", "--protocol", "anthropic",
        ]
        with patch("sys.argv", argv), patch("modeltrace_eval.load_bank", return_value={}), patch(
            "modeltrace_eval.evaluate_target",
            return_value={"account": "direct", "error": None, "passed": True, "top_model": {}},
        ) as evaluate, patch("modeltrace_eval.print_result"):
            self.assertEqual(0, main())

        self.assertEqual("https://example.com/v1/messages", evaluate.call_args.args[0].responses_url)

    def test_wrong_count_fails_without_retry(self) -> None:
        from modeltrace_eval import collect_outputs

        challenges = [
            {"id": f"probe-{index}", "expected_count": 100, "prompt": f"prompt-{index}"}
            for index in range(4)
        ]
        responses = ["1,2", number_text(100), number_text(100)]
        target = AccountTarget("direct", "https://example.com", "secret", "actual-model")
        with patch("modeltrace_eval.generate_challenges", return_value=challenges), patch(
            "modeltrace_eval.test_response",
            side_effect=[ResponseResult(text, 10, 20, 5) for text in responses],
        ) as request:
            result = collect_outputs(target, self._args())

        self.assertEqual(3, request.call_count)
        self.assertTrue(all(call.kwargs["max_tokens"] == 4096 for call in request.call_args_list))
        self.assertTrue(all(call.kwargs["max_retries"] == 0 for call in request.call_args_list))
        self.assertTrue(all(call.kwargs["total_timeout"] == 10 for call in request.call_args_list))
        self.assertEqual(2, len(result["outputs"]))
        self.assertEqual(1, len(result["errors"]))
        self.assertIn("数字个数不符", result["errors"][0])

    def test_three_initial_probes_run_concurrently(self) -> None:
        from modeltrace_eval import collect_outputs

        barrier = threading.Barrier(3, timeout=1)

        def fake_response(*args, **kwargs):
            barrier.wait()
            return ResponseResult(number_text(100), 10, 20, 5)

        challenges = [
            {"id": f"probe-{index}", "expected_count": 100, "prompt": f"prompt-{index}"}
            for index in range(6)
        ]
        target = AccountTarget("direct", "https://example.com", "secret", "actual-model")
        with patch("modeltrace_eval.generate_challenges", return_value=challenges), patch(
            "modeltrace_eval.test_response", side_effect=fake_response,
        ) as request:
            result = collect_outputs(target, self._args())

        self.assertEqual(3, request.call_count)
        self.assertEqual(3, len(result["outputs"]))

    def test_fast_mode_only_sends_first_probe(self) -> None:
        from modeltrace_eval import collect_outputs

        challenges = [
            {"id": f"probe-{index}", "expected_count": 100, "prompt": f"prompt-{index}"}
            for index in range(6)
        ]
        target = AccountTarget("direct", "https://example.com", "secret", "actual-model")
        args = self._args()
        args.fast = True
        with patch("modeltrace_eval.generate_challenges", return_value=challenges) as generate, patch(
            "modeltrace_eval.test_response", return_value=ResponseResult(number_text(100), 10, 20, 5),
        ) as request:
            result = collect_outputs(target, args)

        generate.assert_called_once_with(1)
        self.assertEqual(1, request.call_count)
        self.assertEqual(1, result["attempted"])
        self.assertEqual(1, len(result["outputs"]))

    def test_standard_mode_prints_once_after_combining_three_probes(self) -> None:
        from modeltrace_eval import evaluate_target

        target = AccountTarget("station-a", "https://example.com", "secret", "actual-model")
        collected = {
            "outputs": [
                {"text": number_text(80), "expected_count": 100, "parsed_count": 80}
                for _ in range(3)
            ],
            "errors": [], "attempted": 3, "probe_details": [],
            "usage": {"input_tokens": 30, "output_tokens": 60, "reasoning_tokens": 15},
        }
        analysis = {
            "prediction": "actual-model", "used_outputs": 3,
            "results": [{"model": "actual-model", "probability": 0.9}],
        }
        with patch("modeltrace_eval.collect_outputs", return_value=collected), patch(
            "modeltrace_eval.analyze_global_outputs", return_value=analysis,
        ) as analyze:
            result = evaluate_target(target, self._args(), {})

        analyze.assert_called_once_with(collected["outputs"], {})
        self.assertEqual(3, result["used_outputs"])

    def test_analyzes_account_and_preserves_probe_diagnostics(self) -> None:
        from modeltrace_eval import evaluate_target

        collected = {
            "outputs": [{"text": number_text(80), "expected_count": 100, "parsed_count": 80}],
            "errors": ["probe-2: short"],
            "attempted": 2,
            "probe_details": [],
            "usage": {"input_tokens": 10, "output_tokens": 20, "reasoning_tokens": 5},
        }
        analysis = {
            "prediction": "gpt-5.6-sol", "prediction_name": "GPT-5.6 Sol",
            "probability": 0.8, "family_prediction": "gpt",
            "family_prediction_name": "GPT", "family_probability": 0.9,
            "used_outputs": 1, "diagnostics": [], "results": [
                {
                    "model": "gpt-5.6-sol" if index == 1 else f"model-{index}",
                    "probability": 1 / index,
                }
                for index in range(1, 5)
            ],
        }
        target = AccountTarget("station-a", "https://example.com", "secret", "gpt-5.6-sol")
        with patch("modeltrace_eval.collect_outputs", return_value=collected), patch(
            "modeltrace_eval.analyze_global_outputs", return_value=analysis
        ):
            result = evaluate_target(target, self._args(), {"models": []})

        self.assertTrue(result["passed"])
        self.assertEqual("gpt-5.6-sol", result["top_model"]["model"])
        self.assertNotIn("results", result)
        self.assertEqual("station-a", result["account"])
        self.assertEqual(["probe-2: short"], result["probe_errors"])
        self.assertEqual(10, result["usage"]["input_tokens"])

    def test_failed_attribution_preserves_probe_timeout_details(self) -> None:
        from modeltrace_eval import evaluate_target

        collected = {
            "outputs": [], "errors": ["probe-1: total request timeout exceeded"],
            "attempted": 1, "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
            "probe_details": [{"elapsed_seconds": 45.0, "error": "timeout"}],
        }
        target = AccountTarget("station-a", "https://example.com", "secret", "model")
        with patch("modeltrace_eval.collect_outputs", return_value=collected), patch(
            "modeltrace_eval.analyze_global_outputs", side_effect=ValueError("没有可用回答"),
        ):
            result = evaluate_target(target, self._args(), {})

        self.assertEqual(collected["probe_details"], result["probe_details"])
        self.assertEqual(collected["errors"], result["probe_errors"])

    def test_batch_evaluates_different_accounts_concurrently(self) -> None:
        from sub2api_modeltrace_eval import run_batch

        barrier = threading.Barrier(2, timeout=1)

        def fake_evaluate(target, args, bank):
            barrier.wait()
            return {
                "account": target.name, "tested_model": "model", "error": None,
                "passed": True, "used_outputs": 3,
                "top_model": {"model": "model", "probability": 1.0},
            }

        targets = [
            AccountTarget(name, "https://example.com", "secret", "model")
            for name in ("station-a", "station-b")
        ]
        with patch("sub2api_modeltrace_eval.evaluate_target", side_effect=fake_evaluate):
            results = run_batch(targets, SimpleNamespace(workers=2), {})

        self.assertEqual(["station-a", "station-b"], [item["account"] for item in results])

    def test_sub2api_defaults_to_all_account_workers(self) -> None:
        from sub2api_modeltrace_eval import parse_args

        with patch("sys.argv", ["sub2api_modeltrace_eval.py"]):
            self.assertIsNone(parse_args().workers)

    def test_single_account_mode_rejects_ambiguous_filter(self) -> None:
        from sub2api_modeltrace_eval import require_single_target

        targets = [
            AccountTarget(name, "https://example.com", "secret", "model")
            for name in ("station-a", "station-ab")
        ]
        with self.assertRaisesRegex(ValueError, "匹配到 2 个账号"):
            require_single_target(targets, "station-a")

    def test_report_contains_summary_and_full_results(self) -> None:
        from sub2api_modeltrace_eval import write_report

        args = SimpleNamespace(output=None, model="gpt-5.6-sol", protocol="responses")
        results = [
            {"account": "a", "error": None, "passed": True},
            {"account": "b", "error": None, "passed": False},
            {"account": "c", "error": "failed", "passed": None},
        ]
        with tempfile.TemporaryDirectory() as directory:
            args.output = str(Path(directory) / "report.json")
            output = write_report(args, results)
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual({"accounts": 3, "completed": 2, "passed": 1}, payload["summary"])
        self.assertEqual(results, payload["results"])

    @staticmethod
    def _args() -> SimpleNamespace:
        return SimpleNamespace(
            target_outputs=3, max_attempts=6, reasoning_effort="high",
            timeout=10, protocol="responses", inject_header=False, fast=False,
            request_retries=0,
        )


if __name__ == "__main__":
    unittest.main()
