#!/usr/bin/env python3
"""通过三个长整数探针检测 API 实际提供的模型。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from candy_eval import DEFAULT_MODEL, parse_protocol, positive_float, positive_int, test_response
from modeltrace_core import analyze_global_outputs, generate_challenges, load_bank, parse_numbers
from response_client import AccountTarget


def add_probe_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-p", "--protocol", type=parse_protocol, default="responses",
                        metavar="{responses,anthropic}")
    parser.add_argument("-r", "--reasoning-effort", default="low")
    parser.add_argument("--timeout", type=positive_float, default=180.0)
    parser.add_argument("--target-outputs", type=positive_int, default=3,
                        help="完成归因所需的有效回答数")
    parser.add_argument("--max-attempts", type=positive_int, default=3,
                        help="每个账号最多发送的探针数")
    parser.add_argument("--fast", action="store_true",
                        help="快速模式：只发送第一个探针，不补测")
    parser.add_argument("--request-retries", type=_nonnegative_int, default=0,
                        help="单探针底层重试次数；默认 0")
    parser.add_argument("--bank", default=str(Path(__file__).with_name("modeltrace_data") / "unified_bank.json"))
    parser.add_argument("--inject-header", action="store_true")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-u", "--url", required=True)
    parser.add_argument("-k", "--key", required=True)
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", help="JSON 报告路径")
    add_probe_arguments(parser)
    return parser.parse_args()


def collect_outputs(target: AccountTarget, args: argparse.Namespace) -> dict[str, Any]:
    outputs: list[dict[str, Any]] = []
    errors: list[str] = []
    probe_details: list[dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    fast_mode = getattr(args, "fast", False)
    target_outputs = 1 if fast_mode else args.target_outputs
    max_attempts = 1 if fast_mode else args.max_attempts
    challenges = iter(generate_challenges(max_attempts))
    attempted = 0
    while len(outputs) < target_outputs and attempted < max_attempts:
        missing = target_outputs - len(outputs)
        batch_size = min(missing, max_attempts - attempted)
        batch = [next(challenges) for _ in range(batch_size)]
        attempted += batch_size
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            futures = [executor.submit(_run_probe, target, args, item) for item in batch]
            for future in as_completed(futures):
                output, error, response, detail = future.result()
                probe_details.append(detail)
                if output:
                    outputs.append(output)
                if error:
                    errors.append(error)
                if response:
                    _add_usage(usage, response)
        if any("数字个数不符" in error for error in errors):
            break
    return {
        "outputs": outputs, "errors": errors, "attempted": attempted,
        "usage": usage, "probe_details": probe_details,
    }


def _run_probe(
    target: AccountTarget, args: argparse.Namespace, challenge: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None, Any | None, dict[str, Any]]:
    started = time.perf_counter()
    try:
        response = test_response(
            target.responses_url, target.api_key, target.model,
            reasoning_effort=args.reasoning_effort, timeout=args.timeout,
            prompt=challenge["prompt"], protocol=args.protocol,
            inject_header=args.inject_header, max_tokens=4096,
            max_retries=args.request_retries, total_timeout=args.timeout,
        )
        count = len(parse_numbers(response.text))
        detail = _probe_detail(challenge, response, count, time.perf_counter() - started)
        if count != challenge["expected_count"]:
            error = f"{challenge['id']}: 数字个数不符 {count}/{challenge['expected_count']}"
            detail["error"] = error
            return None, error, response, detail
        return {
            "text": response.text,
            "expected_count": challenge["expected_count"],
            "parsed_count": count,
        }, None, response, detail
    except Exception as exc:
        error = f"{challenge['id']}: {exc}"
        detail = _probe_detail(challenge, exc, 0, time.perf_counter() - started)
        detail["error"] = error
        return None, error, None, detail


def _probe_detail(challenge: dict[str, Any], result: Any, count: int, elapsed: float) -> dict[str, Any]:
    return {
        "challenge_id": challenge["id"],
        "expected_count": challenge["expected_count"],
        "parsed_count": count,
        "elapsed_seconds": round(elapsed, 3),
        "attempts": getattr(result, "attempts", 1),
        "output_tokens": getattr(result, "output_tokens", None),
        "reasoning_tokens": getattr(result, "reasoning_tokens", None),
        "error": None,
    }


def _add_usage(usage: dict[str, int], response: Any) -> None:
    for key in usage:
        value = getattr(response, key, None)
        if isinstance(value, int) and not isinstance(value, bool):
            usage[key] += value


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def evaluate_target(
    target: AccountTarget,
    args: argparse.Namespace,
    bank: dict,
) -> dict[str, Any]:
    collected: dict[str, Any] | None = None
    try:
        collected = collect_outputs(target, args)
        for detail in collected["probe_details"]:
            if detail.get("error") and "数字个数不符" in detail["error"]:
                raise ValueError(detail["error"])
        analysis = analyze_global_outputs(collected["outputs"], bank)
        return {
            **_attribution_result(target, args, analysis),
            "used_outputs": analysis["used_outputs"],
            "attempted": collected["attempted"],
            "usage": collected["usage"],
            "probe_details": collected["probe_details"],
            "probe_errors": collected["errors"],
            "error": None,
        }
    except Exception as exc:
        result = {
            "account": target.name, "tested_model": target.model,
            "fast_mode": getattr(args, "fast", False),
            "passed": False if "数字个数不符" in str(exc) else None,
            "top_model": None, "error": str(exc),
        }
        if collected is not None:
            result.update({
                "attempted": collected["attempted"],
                "usage": collected["usage"],
                "probe_details": collected["probe_details"],
                "probe_errors": collected["errors"],
            })
        return result


def _attribution_result(target: AccountTarget, args: argparse.Namespace, analysis: dict) -> dict[str, Any]:
    first = analysis["results"][0]
    return {
        "account": target.name,
        "tested_model": target.model,
        "fast_mode": getattr(args, "fast", False),
        "passed": first["model"] == target.model,
        "top_model": {
            "model": first["model"],
            "display_name": first.get("display_name", first["model"]),
            "probability": first["probability"],
        },
        "used_outputs": analysis["used_outputs"],
        "error": None,
    }


def print_result(result: dict[str, Any]) -> None:
    if result["error"]:
        print(f"ERROR  {result['account']}  {result['error']}")
        return
    status = "PASS" if result["passed"] else "FAIL"
    top = result["top_model"]
    print(
        f"{status}  {result['account']}  probes={result['used_outputs']}  "
        f"tested={result['tested_model']}  top1={top['model']}  probability={top['probability']:.2%}",
        flush=True,
    )


def main() -> int:
    args = parse_args()
    target = AccountTarget("direct", args.url, args.key, args.model)
    try:
        bank = load_bank(args.bank)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    result = evaluate_target(target, args, bank)
    print_result(result)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Report: {output.resolve()}")
    return 1 if result["error"] or not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
