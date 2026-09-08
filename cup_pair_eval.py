#!/usr/bin/env python3
"""水杯配对游戏的 gpt-5.6-sol medium 流式评测。"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from candy_eval import test_response
from sub2api_info_extract import fetch_account_targets, load_env_file

PROMPT = """请严格解答下面的水杯配对游戏问题。不要使用外部工具或联网搜索。必须给出一个明确的最坏情况交换次数 x，并严格证明：为什么 x 可行，以及为什么少于 x 不可行。只输出你的完整推理和最终结论。

共有 4 种不同颜色的水杯，每种颜色各有两个。将同色的两个水杯分别放在上下两层，因此上下两层各有 4 个水杯。下层 4 个水杯按某个未知顺序排列，挑战者无法看到它们；上层水杯的颜色和位置完全可见。游戏开始后，挑战者可以反复进行以下操作：

1. 向裁判询问当前有多少个位置满足“上下两个水杯颜色相同”。裁判只回答匹配位置的总数，不透露具体是哪些位置；
2. 根据目前获得的所有信息，挑战者可以选择交换上层任意两个相邻位置的水杯，注意只能是相邻，不能是任意两个。

当 4 个位置全部匹配时，游戏结束。问题：挑战者应采用何种策略，才能保证对于下层水杯的任意排列都能完成配对？所有能保证成功的策略中，最坏情况所需的交换次数最少是多少？"""

ANSWER_8 = re.compile(r"(?:答案|结论|最少(?:需要)?(?:交换|次数)?)[^0-9]{0,80}(?:为|是|等于|=|：|:)\s*(?:\\boxed\{)?8(?!\d)", re.IGNORECASE)

def time_bucket(seconds: float) -> str:
    if seconds < 180:
        return "<3分钟"
    if seconds <= 300:
        return "3-5分钟"
    return ">5分钟"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/home/ubuntu/sub2api-deploy/.env")
    parser.add_argument("--sub2api-url", default="http://127.0.0.1:8080")
    parser.add_argument("--admin-token")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output", default="reports/cup-pair-sol-medium-active-20260901.json")
    return parser.parse_args()

def run_one(target, args):
    started = time.perf_counter()
    try:
        result = test_response(target.responses_url, target.api_key, target.model, reasoning_effort=args.reasoning_effort, timeout=args.timeout, prompt=PROMPT)
        elapsed = time.perf_counter() - started
        answer_is_8 = bool(ANSWER_8.search(result.text))
        return {"account": target.name, "model": target.model, "answer_is_8": answer_is_8, "time_bucket": time_bucket(elapsed), "elapsed_seconds": round(elapsed, 3), "text": result.text, "input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "reasoning_tokens": result.reasoning_tokens, "attempts": result.attempts, "error": None}
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {"account": target.name, "model": target.model, "answer_is_8": None, "time_bucket": time_bucket(elapsed), "elapsed_seconds": round(elapsed, 3), "text": None, "input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "attempts": getattr(exc, "attempts", 1), "error": str(exc)}

def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    admin_email = os.getenv("SUB2API_ADMIN_EMAIL") or os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("SUB2API_ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD")
    targets, skipped = fetch_account_targets(args.sub2api_url, args.model, timeout=args.timeout, admin_token=args.admin_token or os.getenv("SUB2API_ADMIN_TOKEN"), admin_email=admin_email, admin_password=admin_password, totp_code=os.getenv("SUB2API_TOTP_CODE"), active_only=True)
    if not targets:
        raise SystemExit("没有找到 active 且映射目标模型的渠道")
    with ThreadPoolExecutor(max_workers=min(args.workers, len(targets))) as pool:
        futures = [pool.submit(run_one, target, args) for target in targets]
        results = [future.result() for future in as_completed(futures)]
    results.sort(key=lambda item: item["account"])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"created_at": datetime.now(timezone.utc).isoformat(), "requested_model": args.model, "reasoning_effort": args.reasoning_effort, "stream": True, "tests_per_account": 1, "accounts": len(targets), "graded": sum(item["answer_is_8"] is not None for item in results), "answer_8": sum(item["answer_is_8"] is True for item in results), "skipped": [{"account": n, "reason": reason} for n, reason in skipped], "results": results}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = [{k: item[k] for k in ("account", "answer_is_8", "time_bucket", "elapsed_seconds", "reasoning_tokens", "attempts", "error")} for item in results]
    print(json.dumps({"report": str(output.resolve()), "accounts": len(targets), "graded": payload["graded"], "answer_8": payload["answer_8"], "results": summary}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
