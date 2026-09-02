#!/usr/bin/env python3
"""直连模型 API 的糖果题流式评测 CLI。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from anthropic_stream import stream_anthropic_request
from responses_stream import ResponseResult, stream_response_request


CANDY_PROMPT = """不使用任何外部工具回答以下问题：

在一个黑色的袋子里放有三种口味的糖果，每种糖果有两种不同的形状（圆形和五角星形，不同的形状靠手感可以分辨）。现已知不同口味的糖和不同形状的数量统计如下表。参赛者需要在活动前决定摸出的糖果数目，那么，最少取出多少个糖果才能保证手中同时拥有不同形状的苹果味和桃子味的糖？（同时手中有圆形苹果味匹配五角星桃子味糖果，或者有圆形桃子味匹配五角星苹果味糖果都满足要求）

        苹果味  桃子味  西瓜味
圆形       7      9      8
五角星形   7      6      4
"""
ANSWER_PATTERN = re.compile(r"(?<!\d)21(?!\d)")
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_PROTOCOL = "responses"
PROTOCOL_ALIASES = {
    "response": "responses",
    "responses": "responses",
    "anthropic": "anthropic",
    "anthropics": "anthropic",
}


def test_response(
    url: str,
    key: str,
    model: str = DEFAULT_MODEL,
    *,
    reasoning_effort: str = "high",
    timeout: float = 600.0,
    prompt: str = CANDY_PROMPT,
    protocol: str = DEFAULT_PROTOCOL,
) -> ResponseResult:
    selected = resolve_protocol(protocol)
    endpoint = normalize_endpoint(url, selected)
    if selected == "anthropic":
        payload = {
            "model": model,
            "max_tokens": 16384,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": reasoning_effort},
            "stream": True,
        }
        return stream_anthropic_request(endpoint, payload, key, timeout=timeout)
    payload = {
        "model": model,
        "input": prompt,
        "reasoning": {"effort": reasoning_effort},
        "store": False,
        "stream": True,
    }
    return stream_response_request(endpoint, payload, key, timeout=timeout)


def evaluate_once(
    run: int,
    url: str,
    key: str,
    *,
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = "high",
    timeout: float = 600.0,
    prompt: str = CANDY_PROMPT,
    protocol: str = DEFAULT_PROTOCOL,
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        result = test_response(
            url,
            key,
            model,
            reasoning_effort=reasoning_effort,
            timeout=timeout,
            prompt=prompt,
            protocol=protocol,
        )
        return _result_dict(run, result, time.perf_counter() - started)
    except Exception as exc:
        return _error_dict(run, exc, time.perf_counter() - started)


def run_tests(
    url: str,
    key: str,
    model: str = DEFAULT_MODEL,
    *,
    tests: int = 1,
    workers: int = 1,
    reasoning_effort: str = "high",
    timeout: float = 600.0,
    prompt: str = CANDY_PROMPT,
    protocol: str = DEFAULT_PROTOCOL,
) -> list[dict[str, object]]:
    arguments = (url, key)
    options = {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "timeout": timeout,
        "prompt": prompt,
        "protocol": protocol,
    }

    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=min(workers, tests)) as executor:
        futures = [
            executor.submit(evaluate_once, run, *arguments, **options)
            for run in range(1, tests + 1)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: int(item["run"]))


def add_direct_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_effort: str = "high",
    default_timeout: float = 600.0,
    default_prompt: str = CANDY_PROMPT,
    default_output: str | None = None,
) -> None:
    parser.add_argument("-u", "--url", required=True)
    parser.add_argument("-k", "--key", required=True)
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL)
    parser.add_argument("-p", "--protocol", type=parse_protocol, default=DEFAULT_PROTOCOL,
                        metavar="{responses,anthropic}")
    parser.add_argument("-r", "--reasoning-effort", default=default_effort)
    parser.add_argument("--timeout", type=positive_float, default=default_timeout)
    parser.add_argument("--prompt", default=default_prompt)
    parser.add_argument("--tests", type=positive_int, default=1)
    parser.add_argument("--workers", type=positive_int, default=1)
    parser.add_argument("--output", default=default_output, help="完整 JSON 报告路径")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_direct_arguments(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        results = run_tests(
            args.url,
            args.key,
            args.model,
            tests=args.tests,
            workers=args.workers,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
            prompt=args.prompt,
            protocol=args.protocol,
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    graded = [item["correct"] for item in results if item["correct"] is not None]
    payload = {
        "url": args.url,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "protocol": args.protocol,
        "stream": True,
        "tests": args.tests,
        "graded": len(graded),
        "correct": sum(value is True for value in graded),
        "results": results,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(json.dumps({
            "report": str(output.resolve()),
            "graded": len(graded),
            "correct": payload["correct"],
        }, ensure_ascii=False))
    else:
        print(rendered, end="")
    return 0 if len(graded) == args.tests else 1


def _result_dict(run: int, result: ResponseResult, elapsed: float) -> dict[str, object]:
    return {
        "run": run,
        "text": result.text,
        "correct": bool(ANSWER_PATTERN.search(result.text)),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "attempts": result.attempts,
        "elapsed_seconds": round(elapsed, 3),
        "error": None,
    }


def _error_dict(run: int, exc: Exception, elapsed: float) -> dict[str, object]:
    return {
        "run": run,
        "text": None,
        "correct": None,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "attempts": getattr(exc, "attempts", 1),
        "elapsed_seconds": round(elapsed, 3),
        "error": str(exc),
    }


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_protocol(value: str) -> str:
    try:
        return PROTOCOL_ALIASES[value.casefold()]
    except KeyError as exc:
        raise argparse.ArgumentTypeError("protocol must be responses or anthropic") from exc


def resolve_protocol(protocol: str) -> str:
    return parse_protocol(protocol)


def normalize_endpoint(url: str, protocol: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("credentials in URLs are not supported")
    endpoint = "messages" if resolve_protocol(protocol) == "anthropic" else "responses"
    path = _normalized_endpoint_path(parsed.path, endpoint)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _normalized_endpoint_path(raw_path: str, endpoint: str) -> str:
    path = raw_path.rstrip("/")
    for suffix in ("/responses", "/messages"):
        if path.endswith(suffix):
            return path[: -len(suffix)] + f"/{endpoint}"
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if path.endswith("/v1"):
        return path + f"/{endpoint}"
    return path + f"/v1/{endpoint}"


if __name__ == "__main__":
    sys.exit(main())
