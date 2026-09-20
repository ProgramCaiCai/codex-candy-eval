import unittest
from types import SimpleNamespace
from unittest.mock import patch

from modeltrace_core import analyze_global_outputs, load_bank
from modeltrace_eval import evaluate_target
from response_client import AccountTarget
from responses_stream import ResponseResult


class ModelTraceCountsTest(unittest.TestCase):
    def test_wrong_count_fails_account_before_attribution(self):
        for count in (0, 99, 101):
            with self.subTest(count=count):
                args = SimpleNamespace(
                    fast=False, target_outputs=1, max_attempts=3,
                    reasoning_effort="low", timeout=10, protocol="responses",
                    inject_header=False, request_retries=0,
                )
                challenge = {"id": "probe-1", "expected_count": 100, "prompt": "test"}
                response = ResponseResult(",".join(["1"] * count), 10, 20, 5)
                target = AccountTarget("direct", "https://example.com", "secret", "model")
                with patch("modeltrace_eval.generate_challenges", return_value=[challenge]), patch(
                    "modeltrace_eval.test_response", return_value=response,
                ) as request, patch("modeltrace_eval.analyze_global_outputs") as analyze:
                    result = evaluate_target(target, args, {})
                self.assertEqual(1, request.call_count)
                analyze.assert_not_called()
                self.assertFalse(result["passed"])
                self.assertIn("数字个数不符", result["error"])
                self.assertIn(f"{count}/100", result["error"])
                self.assertEqual(count, result["probe_details"][0]["parsed_count"])

    def test_analysis_rejects_mismatch_even_with_valid_output(self):
        valid = {"text": ",".join(["1"] * 100), "expected_count": 100}
        for count in (0, 99, 101):
            with self.subTest(count=count), patch("modeltrace_core.robust_scores") as scores:
                invalid = {"text": ",".join(["1"] * count), "expected_count": 100}
                with self.assertRaisesRegex(ValueError, "数字个数不符"):
                    analyze_global_outputs([valid, invalid], {})
                scores.assert_not_called()

    def test_analysis_accepts_exact_count(self):
        output = {"text": ",".join(["1"] * 100), "expected_count": 100}
        result = analyze_global_outputs([output], load_bank())
        self.assertEqual(1, result["used_outputs"])
