import sys
import unittest
from unittest.mock import patch

from response_client import AccountTarget
from responses_stream import ResponseResult
from sub2api_eval import parse_args, run_batch


class Sub2APIInjectHeaderTest(unittest.TestCase):
    def test_flag_defaults_to_disabled(self):
        with patch.object(sys, "argv", ["sub2api_eval.py"]):
            self.assertFalse(parse_args().inject_header)

    def test_batch_uses_each_accounts_mapped_model(self):
        for protocol in ("responses", "anthropic"):
            for enabled in (False, True):
                with self.subTest(protocol=protocol, enabled=enabled):
                    self.check_batch(protocol, enabled)

    def check_batch(self, protocol, enabled):
        argv = ["sub2api_eval.py", "-m", "requested-model", "-p", protocol, "-n", "2"]
        if enabled:
            argv.append("--inject-header")
        with patch.object(sys, "argv", argv):
            args = parse_args()
        targets = [AccountTarget(name, "https://example.com", "secret", name)
                   for name in ("upstream-a", "upstream-b")]
        transport = "stream_response_request" if protocol == "responses" else "stream_anthropic_request"
        with patch(f"candy_eval.{transport}", return_value=ResponseResult("21", 1, 1, 1)) as send:
            results = run_batch(targets, args)
        self.assertEqual(4, len(results))
        self.assertTrue(all(correct for _, correct, _ in results))
        self.assertEqual(4, send.call_count)
        models = []
        for call in send.call_args_list:
            model = call.args[1]["model"]
            models.append(model)
            expected = {"x-codex-routing-hint": f"model={model};tier=default"} if enabled else None
            self.assertEqual(expected, call.kwargs.get("headers"))
        self.assertCountEqual(["upstream-a", "upstream-a", "upstream-b", "upstream-b"], models)


if __name__ == "__main__":
    unittest.main()
