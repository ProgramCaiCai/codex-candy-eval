#!/usr/bin/env python3
"""从 sub2api 提取 provider，并复用 candy_eval 批量评测糖果题。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from candy_eval import (
    DEFAULT_MODEL,
    evaluate_once,
    parse_protocol,
    positive_float,
    positive_int,
)
from sub2api_info_extract import AccountTarget, fetch_account_targets, load_env_file

EFFORTS = ["none", "low", "medium", "high", "xhigh", "max", "ultra"]
RunResult = tuple[list[object], bool | None, dict[str, object]]


def parse_args() -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=os.getenv("SUB2API_ENV_FILE"))
    preliminary, _ = pre_parser.parse_known_args()
    load_env_file(preliminary.env_file)

    parser = argparse.ArgumentParser(description=__doc__, parents=[pre_parser])
    parser.add_argument("--sub2api-url", default=os.getenv("SUB2API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--admin-token", default=os.getenv("SUB2API_ADMIN_TOKEN"))
    parser.add_argument("--admin-email", default=_first_env("SUB2API_ADMIN_EMAIL", "ADMIN_EMAIL"))
    parser.add_argument("--admin-password", default=_first_env("SUB2API_ADMIN_PASSWORD", "ADMIN_PASSWORD"))
    parser.add_argument("--totp-code", default=os.getenv("SUB2API_TOTP_CODE"))
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL)
    parser.add_argument("-p", "--protocol", type=parse_protocol, default="responses",
                        metavar="{responses,anthropic}")
    parser.add_argument("-r", "--reasoning-effort", choices=EFFORTS, default="high")
    parser.add_argument("-n", "--tests", type=positive_int, default=1, help="每个账号的测试次数")
    parser.add_argument("-j", "--workers", type=positive_int, default=4)
    parser.add_argument("--timeout", type=positive_float, default=600.0)
    parser.add_argument("--account", help="只测试名称包含该文本的账号（不区分大小写）")
    parser.add_argument("--list-only", action="store_true", help="只列出匹配账号，不发送模型请求")
    parser.add_argument("--output", help="JSON 报告路径；默认写入 reports/ 下的时间戳文件")
    return parser.parse_args()


def _first_env(*names: str) -> str | None:
    return next((os.environ[name] for name in names if os.environ.get(name)), None)


def discover_targets(args: argparse.Namespace) -> tuple[list[AccountTarget], list[tuple[str, str]]]:
    return fetch_account_targets(
        args.sub2api_url,
        args.model,
        timeout=args.timeout,
        admin_token=args.admin_token,
        admin_email=args.admin_email,
        admin_password=args.admin_password,
        totp_code=args.totp_code,
        account_filter=args.account,
    )


def run_one(
    target: AccountTarget, run: int, args: argparse.Namespace
) -> RunResult:
    result = evaluate_once(
        run,
        target.responses_url,
        target.api_key,
        model=target.model,
        reasoning_effort=args.reasoning_effort,
        timeout=args.timeout,
        protocol=args.protocol,
    )
    correct = result["correct"] if isinstance(result["correct"], bool) else None
    elapsed = float(result["elapsed_seconds"])
    output_tokens = result["output_tokens"]
    tps = output_tokens / elapsed if isinstance(output_tokens, int) and elapsed > 0 else None
    answer = result["text"] if isinstance(result["text"], str) else f"ERROR: {result['error']}"
    row = [
        target.name, run, target.model, preview(answer),
        value_or_dash(result["input_tokens"]), value_or_dash(output_tokens),
        value_or_dash(result["reasoning_tokens"]), f"{elapsed:.1f}",
        f"{tps:.1f}" if tps else "-", "✓" if correct else "✗" if correct is False else "-",
    ]
    record = {
        "account": target.name,
        "model": target.model,
        **result,
        "tokens_per_second": round(tps, 3) if tps else None,
    }
    return row, correct, record


def run_batch(targets: list[AccountTarget], args: argparse.Namespace) -> list[RunResult]:
    jobs = [(target, run) for target in targets for run in range(1, args.tests + 1)]
    results: list[tuple[int, RunResult]] = []
    with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs))) as executor:
        future_indexes = {
            executor.submit(run_one, target, run, args): index
            for index, (target, run) in enumerate(jobs)
        }
        for future in as_completed(future_indexes):
            results.append((future_indexes[future], future.result()))
    return [result for _, result in sorted(results)]


def write_report(
    args: argparse.Namespace,
    targets: list[AccountTarget],
    results: list[RunResult],
) -> Path:
    timestamp = datetime.now(timezone.utc)
    output = Path(args.output) if args.output else Path("reports") / timestamp.strftime(
        "sub2api-candy-eval-%Y%m%dT%H%M%SZ.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    graded = [correct for _, correct, _ in results if correct is not None]
    payload = {
        "created_at": timestamp.isoformat(),
        "requested_model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "protocol": args.protocol,
        "stream": True,
        "accounts": len(targets),
        "graded": len(graded),
        "correct": sum(graded),
        "results": [record for _, _, record in results],
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def print_targets(targets: list[AccountTarget], skipped: list[tuple[str, str]]) -> None:
    rows = [[target.name, target.model, target.responses_url] for target in targets]
    print(render_table(["Account", "Upstream Model", "Provider URL"], rows, ["left"] * 3))
    print(f"\nMatched accounts: {len(targets)}  skipped by config: {len(skipped)}")
    for name, reason in skipped:
        print(f"  skip {name}: {reason}")


def main() -> int:
    setup_console()
    args = parse_args()
    try:
        targets, skipped = discover_targets(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not targets:
        print_targets(targets, skipped)
        return 1
    if args.list_only:
        print_targets(targets, skipped)
        return 0

    results = run_batch(targets, args)
    rows = [row for row, _, _ in results]
    headers = ["Account", "Run", "Model", "Answer", "In", "Out", "Reason", "Time(s)", "TPS", "OK"]
    aligns = ["left", "right", "left", "left", "right", "right", "right", "right", "right", "center"]
    print(render_table(headers, rows, aligns))
    report_path = write_report(args, targets, results)
    graded = [correct for _, correct, _ in results if correct is not None]
    correct = sum(graded)
    total = len(results)
    if graded:
        accuracy = correct / len(graded) * 100
        print(f"\nAccounts={len(targets)}  graded={len(graded)}/{total}  correct={correct}  accuracy={accuracy:.1f}%")
    else:
        print(f"\nAccounts={len(targets)}  graded=0/{total}")
    print(f"Report: {report_path.resolve()}")
    return 0 if len(graded) == total else 1


def value_or_dash(value: int | None) -> int | str:
    return value if value is not None else "-"


def char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1


def display_width(text: str) -> int:
    return sum(char_width(char) for char in text)


def pad(text: str, width: int, align: str) -> str:
    gap = width - display_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def render_table(headers: list[str], rows: list[list[object]], aligns: list[str]) -> str:
    text_rows = [[str(cell) for cell in row] for row in rows]
    widths = [
        max([display_width(headers[index]), *[display_width(row[index]) for row in text_rows]])
        for index in range(len(headers))
    ]

    def format_row(cells: list[str]) -> str:
        return "  ".join(pad(cell, widths[index], aligns[index]) for index, cell in enumerate(cells))

    lines = [format_row(headers), "  ".join("-" * width for width in widths)]
    lines.extend(format_row(row) for row in text_rows)
    return "\n".join(lines)


def preview(text: str, limit: int = 52) -> str:
    flattened = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\n")
    if display_width(flattened) <= limit:
        return flattened
    result: list[str] = []
    width = 0
    for char in flattened:
        if width + char_width(char) > limit - 3:
            break
        result.append(char)
        width += char_width(char)
    return "".join(result) + "..."


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
