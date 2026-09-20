from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

import candy_eval
from candy_cases import case_prompt
from responses_stream import ResponseResult


class CandyV2Test(unittest.TestCase):
    def test_extracts_conclusion_instead_of_incidental_gold(self):
        cases = {
            "讨论21颗，但最终不同。\n最终答案：29": 29,
            "**最终答案：21**": 21,
            "最终答案：29颗。": 29,
            "21": 21,
            "121": 121,
            "21不够，需要更多": None,
            "最终答案：21\n最终答案：29": None,
            "": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, candy_eval.extract_answer(text))

    @patch("candy_eval.test_response")
    def test_same_29_answer_has_different_case_grades(self, request):
        request.return_value = ResponseResult("最终答案：29", 1, 2, 1)
        results = candy_eval.run_tests("https://example.com", "secret")
        self.assertEqual([False, True], [r["correct"] for r in results])
        self.assertEqual("possible_blind_draw_misread", results[0]["diagnostic"])
        self.assertIsNone(results[1]["diagnostic"])
        self.assertTrue(all(r["proof_verified"] is None for r in results))

    @patch("candy_eval.test_response")
    def test_cases_are_separate_requests_and_repeated_independently(self, request):
        def respond(*args, **kwargs):
            answer = 21 if kwargs["prompt"] == case_prompt("A") else 29
            return ResponseResult(f"最终答案：{answer}", 1, 2, 1)

        request.side_effect = respond
        results = candy_eval.run_tests(
            "https://example.com", "secret", tests=2, workers=4,
            protocol="anthropic", system_prompt="instructions",
        )
        self.assertEqual(4, request.call_count)
        self.assertEqual([(1, "A"), (1, "B"), (2, "A"), (2, "B")],
                         [(r["run"], r["case"]) for r in results])
        self.assertTrue(all(r["correct"] for r in results))
        for call in request.call_args_list:
            self.assertIn(call.kwargs["prompt"], (case_prompt("A"), case_prompt("B")))
            self.assertEqual("anthropic", call.kwargs["protocol"])
            self.assertEqual("instructions", call.kwargs["system_prompt"])

    @patch("candy_eval.test_response")
    def test_error_does_not_discard_other_case(self, request):
        request.side_effect = [TimeoutError("timed out"), ResponseResult("29", 1, 2, 1)]
        results = candy_eval.run_tests("https://example.com", "secret")
        self.assertIsNone(results[0]["correct"])
        self.assertEqual("timed out", results[0]["error"])
        self.assertEqual("A", results[0]["case"])
        self.assertTrue(results[1]["correct"])

    @patch("candy_eval.test_response")
    def test_custom_prompt_is_not_graded_against_builtin_gold(self, request):
        request.return_value = ResponseResult("21", 1, 2, 1)
        with self.assertRaisesRegex(ValueError, "requires --case"):
            candy_eval.run_tests("https://example.com", "secret", prompt="custom")
        request.assert_not_called()
        result = candy_eval.run_tests(
            "https://example.com", "secret", prompt="custom", case="A",
        )[0]
        self.assertIsNone(result["correct"])
        self.assertIsNone(result["expected_answer"])
        self.assertEqual("custom", request.call_args.kwargs["prompt"])

    @patch("candy_eval.test_response")
    def test_main_report_identifies_version_and_case_totals(self, request):
        request.side_effect = [ResponseResult("21", 1, 2, 1), ResponseResult("29", 1, 2, 1)]
        argv = ["candy_eval.py", "-u", "https://example.com", "-k", "secret"]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            self.assertEqual(0, candy_eval.main())
        report = json.loads(output.getvalue())
        self.assertEqual(2, report["version"])
        self.assertEqual(2, report["requests"])
        self.assertEqual(1, report["correct"])
        self.assertEqual(1, report["graded"])
        self.assertEqual([{"run": 1, "correct": True}], report["rounds"])
        self.assertEqual(1, report["by_case"]["A"]["correct"])
        self.assertEqual(1, report["by_case"]["B"]["correct"])
        self.assertNotIn("secret", output.getvalue())

    def test_round_requires_both_correct_in_the_same_run(self):
        for a, b, expected in (
            (True, True, True), (False, True, False),
            (True, False, False), (False, False, False),
            (None, True, None), (True, None, None),
        ):
            with self.subTest(a=a, b=b):
                results = [{"run": 1, "case": "A", "correct": a},
                           {"run": 1, "case": "B", "correct": b}]
                self.assertEqual([{"run": 1, "correct": expected}],
                                 candy_eval.grade_rounds(results, tests=1))
        split = [{"run": 1, "case": "A", "correct": True},
                 {"run": 2, "case": "B", "correct": True}]
        self.assertEqual([{"run": 1, "correct": None}, {"run": 2, "correct": None}],
                         candy_eval.grade_rounds(split, tests=2))


if __name__ == "__main__":
    unittest.main()
