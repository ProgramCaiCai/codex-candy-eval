from __future__ import annotations

import io
import inspect
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from candy_eval import run_tests, test_response
from responses_stream import stream_response_request
from responses_stream import ResponseResult
from sub2api_eval import run_batch, write_report
from sub2api_info_extract import _target_dict

from response_client import (
    AccountTarget,
    Sub2APIClient,
    build_responses_url,
    resolve_targets,
)


class FakeAPIHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict, dict]] = []

    def do_GET(self) -> None:
        self._record({})
        if self.path.startswith("/api/v1/admin/accounts/data?"):
            self._json({
                "code": 0,
                "data": {
                    "accounts": [
                        {
                            "name": "station-a",
                            "platform": "openai",
                            "type": "apikey",
                            "credentials": {
                                "base_url": f"http://127.0.0.1:{self.server.server_port}/upstream/v1",
                                "api_key": "station-secret",
                                "model_mapping": {"gpt-5.6-sol": "vendor-sol"},
                            },
                        }
                    ]
                },
            })
            return
        self._json({"message": "not found"}, status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self._record(payload)
        if self.path == "/api/v1/auth/login":
            self._json({"code": 0, "data": {"access_token": "admin-token"}})
            return
        if self.path == "/upstream/v1/responses":
            body = (
                'data: {"type":"response.output_text.delta","delta":"答案是 "}\n\n'
                'data: {"type":"response.output_text.delta","delta":"21"}\n\n'
                'data: {"type":"response.completed","response":{"output":[],"usage":{"input_tokens":100,"output_tokens":30,"output_tokens_details":{"reasoning_tokens":20}}}}\n\n'
                "data: [DONE]\n\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/anthropic/v1/messages":
            body = (
                'data: {"type":"message_start","message":{"usage":{"input_tokens":90}}}\n\n'
                'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"答案是 21"}}\n\n'
                'data: {"type":"message_delta","usage":{"output_tokens":40}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"message": "not found"}, status=404)

    def _record(self, payload: dict) -> None:
        self.requests.append((self.command, self.path, dict(self.headers), payload))

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        pass


class ResponseClientTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeAPIHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_build_responses_url_normalizes_common_base_urls(self) -> None:
        cases = {
            "https://example.com": "https://example.com/v1/responses",
            "https://example.com/v1": "https://example.com/v1/responses",
            "https://example.com/v1/": "https://example.com/v1/responses",
            "https://example.com/v1/chat/completions": "https://example.com/v1/responses",
            "https://example.com/responses": "https://example.com/responses",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(expected, build_responses_url(source))

    def test_resolve_targets_honors_exact_and_longest_wildcard_mapping(self) -> None:
        accounts = [
            self._account("exact", {"gpt-5.6-sol": "exact-sol"}),
            self._account("wild", {"gpt-*": "generic", "gpt-5.6-*": "specific"}),
            self._account("excluded", {"gpt-5.5": "old"}),
        ]
        targets, skipped = resolve_targets(accounts, "gpt-5.6-sol")
        self.assertEqual(["exact-sol", "specific"], [target.model for target in targets])
        self.assertEqual([("excluded", "model mapping excludes gpt-5.6-sol")], skipped)

    def test_sub2api_discovery_and_direct_response_end_to_end(self) -> None:
        client = Sub2APIClient(self.base_url, timeout=2)
        client.login("admin@example.com", "password")
        targets, skipped = resolve_targets(client.export_api_key_accounts(), "gpt-5.6-sol")
        self.assertEqual([], skipped)
        self.assertEqual(1, len(targets))

        result = test_response(
            targets[0].responses_url,
            targets[0].api_key,
            targets[0].model,
            reasoning_effort="high",
            timeout=2,
            prompt="candy prompt",
        )
        self.assertEqual("答案是 21", result.text)
        self.assertEqual((100, 30, 20), (result.input_tokens, result.output_tokens, result.reasoning_tokens))
        self.assertEqual(1, result.attempts)
        upstream = FakeAPIHandler.requests[-1]
        self.assertEqual("Bearer station-secret", upstream[2]["Authorization"])
        self.assertIn("codex_cli_rs/", upstream[2]["User-Agent"])
        self.assertEqual("codex_cli_rs", upstream[2]["Originator"])
        self.assertEqual("vendor-sol", upstream[3]["model"])
        self.assertEqual({"effort": "high"}, upstream[3]["reasoning"])
        self.assertFalse(upstream[3]["store"])
        self.assertTrue(upstream[3]["stream"])

    def test_claude_model_uses_anthropic_messages_protocol(self) -> None:
        result = test_response(
            self.base_url + "/anthropic",
            "anthropic-secret",
            "claude-opus-5",
            reasoning_effort="high",
            timeout=2,
            prompt="candy prompt",
            protocol="anthropic",
        )

        self.assertEqual("答案是 21", result.text)
        self.assertEqual((90, 40, None), (
            result.input_tokens, result.output_tokens, result.reasoning_tokens
        ))
        upstream = FakeAPIHandler.requests[-1]
        self.assertEqual("/anthropic/v1/messages", upstream[1])
        self.assertEqual("anthropic-secret", upstream[2]["X-Api-Key"])
        self.assertEqual("2023-06-01", upstream[2]["Anthropic-Version"])
        self.assertEqual({"type": "adaptive"}, upstream[3]["thinking"])
        self.assertEqual({"effort": "high"}, upstream[3]["output_config"])
        self.assertTrue(upstream[3]["stream"])

    def test_account_target_repr_hides_api_key(self) -> None:
        target = AccountTarget("name", "https://example.com/v1/responses", "secret", "model")
        self.assertNotIn("secret", repr(target))
        self.assertNotIn("key", _target_dict(target, include_key=False))
        self.assertEqual("secret", _target_dict(target, include_key=True)["key"])

    def test_candy_eval_only_requires_url_and_key(self) -> None:
        parameters = inspect.signature(test_response).parameters
        required = [
            name
            for name, parameter in parameters.items()
            if parameter.default is inspect.Parameter.empty
        ]
        self.assertEqual(["url", "key"], required)

    def test_standalone_tester_runs_same_endpoint_concurrently(self) -> None:
        barrier = threading.Barrier(2, timeout=1)

        def fake_response(*args, **kwargs):
            barrier.wait()
            return ResponseResult("21", 10, 5, 3)

        with patch("candy_eval.test_response", side_effect=fake_response):
            results = run_tests(
                "https://example.com/v1/responses",
                "secret",
                "model",
                tests=2,
                workers=2,
            )

        self.assertEqual([1, 2], [result["run"] for result in results])
        self.assertTrue(all(result["correct"] for result in results))

    def test_run_batch_allows_same_provider_runs_to_execute_concurrently(self) -> None:
        barrier = threading.Barrier(2, timeout=1)

        def fake_run(target, run, args):
            barrier.wait()
            return [target.name, run], True, {"run": run}

        target = AccountTarget("same-provider", "https://example.com", "secret", "model")
        args = SimpleNamespace(tests=2, workers=2)
        with patch("sub2api_eval.run_one", side_effect=fake_run):
            results = run_batch([target], args)

        self.assertEqual([1, 2], [record["run"] for _, _, record in results])

    def test_report_persists_full_text_and_reasoning_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            args = SimpleNamespace(
                output=str(output), model="gpt-5.6-sol", reasoning_effort="high",
                protocol="responses",
            )
            record = {
                "account": "station-a",
                "text": "完整回答",
                "reasoning_tokens": 20,
            }
            write_report(args, [object()], [([], True, record)])
            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual("high", saved["reasoning_effort"])
        self.assertTrue(saved["stream"])
        self.assertEqual("完整回答", saved["results"][0]["text"])
        self.assertEqual(20, saved["results"][0]["reasoning_tokens"])

    @patch("responses_stream.time.sleep")
    @patch("responses_stream.urllib.request.urlopen")
    def test_stream_request_retries_connection_three_times(self, urlopen, sleep) -> None:
        body = (
            b'data: {"type":"response.output_text.delta","delta":"21"}\n\n'
            b'data: {"type":"response.completed","response":{"usage":{}}}\n\n'
        )
        urlopen.side_effect = [
            URLError("temporary-1"),
            URLError("temporary-2"),
            URLError("temporary-3"),
            io.BytesIO(body),
        ]

        result = stream_response_request(
            "https://example.com/v1/responses", {"stream": True}, "secret", timeout=2
        )

        self.assertEqual("21", result.text)
        self.assertEqual(4, result.attempts)
        self.assertEqual(4, urlopen.call_count)
        self.assertEqual([((1,), {}), ((2,), {}), ((4,), {})], sleep.call_args_list)

    def _account(self, name: str, mapping: dict[str, str]) -> dict:
        return {
            "name": name,
            "credentials": {
                "base_url": "https://example.com/v1",
                "api_key": "secret",
                "model_mapping": mapping,
            },
        }


if __name__ == "__main__":
    unittest.main()
