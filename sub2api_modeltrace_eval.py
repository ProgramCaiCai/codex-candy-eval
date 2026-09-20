#!/usr/bin/env python3
"""批量检测 sub2api 各账号实际提供的模型。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from candy_eval import DEFAULT_MODEL, positive_int
from modeltrace_core import load_bank
from modeltrace_eval import add_probe_arguments, evaluate_target, print_result
from response_client import AccountTarget, load_env_file
from sub2api_info_extract import fetch_account_targets


def parse_args() -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=os.getenv("SUB2API_ENV_FILE"))
    preliminary, _ = pre_parser.parse_known_args()
    load_env_file(preliminary.env_file)
    parser = argparse.ArgumentParser(description=__doc__, parents=[pre_parser])
    parser.add_argument("--sub2api-url", default=os.getenv("SUB2API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--admin-token", default=os.getenv("SUB2API_ADMIN_TOKEN"))
    parser.add_argument("--admin-email", default=os.getenv("SUB2API_ADMIN_EMAIL") or os.getenv("ADMIN_EMAIL"))
    parser.add_argument("--admin-password", default=os.getenv("SUB2API_ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD"))
    parser.add_argument("--totp-code", default=os.getenv("SUB2API_TOTP_CODE"))
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL)
    parser.add_argument("-j", "--workers", type=positive_int,
                        help="账号并发数；默认同时运行全部匹配账号")
    parser.add_argument("--account", help="按名称包含关系筛选账号（不区分大小写）")
    parser.add_argument("--single-account", action="store_true",
                        help="要求筛选结果恰好只有一个账号")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--output", help="JSON 报告路径")
    add_probe_arguments(parser)
    return parser.parse_args()


def discover_targets(args: argparse.Namespace) -> tuple[list[AccountTarget], list[tuple[str, str]]]:
    return fetch_account_targets(
        args.sub2api_url, args.model, timeout=args.timeout,
        admin_token=args.admin_token, admin_email=args.admin_email,
        admin_password=args.admin_password, totp_code=args.totp_code,
        account_filter=args.account,
    )


def require_single_target(targets: list[AccountTarget], account: str | None) -> AccountTarget:
    if len(targets) != 1:
        detail = f"筛选条件 {account!r} " if account else ""
        raise ValueError(f"{detail}匹配到 {len(targets)} 个账号，单账号模式要求恰好 1 个")
    return targets[0]


def run_batch(targets: list[AccountTarget], args: argparse.Namespace, bank: dict) -> list[dict[str, Any]]:
    indexed_results: list[tuple[int, dict[str, Any]]] = []
    worker_count = min(args.workers, len(targets)) if args.workers else len(targets)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(evaluate_target, target, args, bank): index
            for index, target in enumerate(targets)
        }
        for future in as_completed(futures):
            result = future.result()
            indexed_results.append((futures[future], result))
            print_result(result)
    return [result for _, result in sorted(indexed_results)]


def write_report(args: argparse.Namespace, results: list[dict[str, Any]]) -> Path:
    timestamp = datetime.now(timezone.utc)
    output = Path(args.output) if args.output else Path("reports") / timestamp.strftime(
        "sub2api-modeltrace-%Y%m%dT%H%M%SZ.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = [result for result in results if result["error"] is None]
    payload = {
        "created_at": timestamp.isoformat(),
        "requested_model": args.model,
        "protocol": args.protocol,
        "fast_mode": getattr(args, "fast", False),
        "summary": {
            "accounts": len(results),
            "completed": len(completed),
            "passed": sum(result["passed"] is True for result in completed),
        },
        "results": results,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> int:
    args = parse_args()
    try:
        targets, skipped = discover_targets(args)
        if args.single_account:
            targets = [require_single_target(targets, args.account)]
        if not targets:
            raise ValueError("没有匹配的可测试账号")
        if args.list_only:
            for target in targets:
                print(f"{target.name}\t{target.model}\t{target.responses_url}")
            return 0
        bank = load_bank(args.bank)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    results = run_batch(targets, args, bank)
    report = write_report(args, results)
    passed = sum(result["passed"] is True for result in results)
    print(f"\nPassed: {passed}/{len(results)}  skipped: {len(skipped)}")
    print(f"Report: {report.resolve()}")
    return 1 if any(result["error"] for result in results) or passed != len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
