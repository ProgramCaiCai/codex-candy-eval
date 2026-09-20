import io
import json
import sys
import unittest
import urllib.error
from unittest.mock import patch

import candy_eval


class InjectHeaderTest(unittest.TestCase):
    def test_cli_flag(self):
        argv = ["candy_eval.py", "-u", "https://example.com", "-k", "secret"]
        with patch.object(sys, "argv", argv):
            self.assertFalse(candy_eval.parse_args().inject_header)
        with patch.object(sys, "argv", argv + ["--inject-header"]):
            self.assertTrue(candy_eval.parse_args().inject_header)

    def test_header_reaches_every_request_and_retry(self):
        for protocol in ("responses", "anthropic"):
            for enabled in (False, True):
                with self.subTest(protocol=protocol, enabled=enabled):
                    self.check_requests(protocol, enabled)

    def test_main_passes_flag_and_selected_model(self):
        argv = ["candy_eval.py", "-u", "https://example.com", "-k", "secret",
                "-m", "selected-model", "--inject-header"]
        with patch.object(sys, "argv", argv), patch("builtins.print"), patch(
            "candy_eval.run_tests", return_value=[{"correct": True}]
        ) as run:
            self.assertEqual(0, candy_eval.main())
        self.assertEqual("selected-model", run.call_args.args[2])
        self.assertTrue(run.call_args.kwargs["inject_header"])

    def check_requests(self, protocol, enabled):
        events = ({"type": "response.completed", "response": {"output_text": "21"}}
                  if protocol == "responses" else {"type": "message_stop"})
        stream = io.BytesIO(("data: " + json.dumps(events) + "\n\n").encode())
        module = "responses_stream" if protocol == "responses" else "anthropic_stream"
        with patch(f"{module}.urllib.request.urlopen", side_effect=[
            urllib.error.URLError("temporary"), stream,
        ]) as send, patch(f"{module}.time.sleep"):
            results = candy_eval.run_tests(
                "https://example.com", "secret", "actual-model",
                protocol=protocol, inject_header=enabled,
            )
        self.assertIsNone(results[0]["error"])
        self.assertEqual(2, send.call_count)
        for call in send.call_args_list:
            request = call.args[0]
            headers = {name.lower(): value for name, value in request.header_items()}
            expected = "model=actual-model;tier=default" if enabled else None
            self.assertEqual(expected, headers.get("x-codex-routing-hint"))
            self.assertEqual("actual-model", json.loads(request.data)["model"])


if __name__ == "__main__":
    unittest.main()
