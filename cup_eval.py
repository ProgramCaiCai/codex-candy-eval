#!/usr/bin/env python3
"""独立水杯配对题流式评测 CLI。"""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from candy_eval import add_direct_arguments, test_response

PROMPT = """请严格解答下面的水杯配对游戏问题。不要使用外部工具或联网搜索。必须给出一个明确的最坏情况交换次数 x，并严格证明：为什么 x 可行，以及为什么少于 x 不可行。只输出完整推理和最终结论。

共有 4 种不同颜色的水杯，每种颜色各有两个。将同色的两个水杯分别放在上下两层，因此上下两层各有 4 个水杯。下层 4 个水杯按某个未知顺序排列，挑战者无法看到它们；上层水杯的颜色和位置完全可见。游戏开始后，挑战者可以反复进行以下操作：

1. 向裁判询问当前有多少个位置满足“上下两个水杯颜色相同”。裁判只回答匹配位置的总数，不透露具体是哪些位置；
2. 根据目前获得的所有信息，挑战者可以选择交换上层任意两个相邻位置的水杯，注意只能是相邻，不能是任意两个。

当 4 个位置全部匹配时，游戏结束。问题：挑战者应采用何种策略，才能保证对于下层水杯的任意排列都能完成配对？所有能保证成功的策略中，最坏情况所需的交换次数最少是多少？"""

ANSWER_VALUE = re.compile(
    r"(?:答案|结论|最少(?:需要)?(?:交换|次数)?|最坏情况)[^0-9]{0,100}"
    r"(?:为|是|等于|=|：|:)\s*(?:\\boxed\{)?([0-9]+)",
    re.IGNORECASE,
)


def extract_answer(text: str) -> int | None:
    matches = ANSWER_VALUE.findall(text)
    if matches:
        return int(matches[-1])
    boxed = re.findall(r"\\boxed\{\s*([0-9]+)\s*\}", text)
    return int(boxed[-1]) if boxed else None


def time_bucket(seconds: float) -> str:
    if seconds < 180:
        return "<3分钟"
    if seconds <= 300:
        return "3-5分钟"
    return ">5分钟"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_direct_arguments(
        parser,
        default_effort="medium",
        default_prompt=PROMPT,
        default_output="reports/cup-eval.json",
    )
    return parser.parse_args()


def run_one(run: int, args: argparse.Namespace) -> dict[str, object]:
    started = time.perf_counter()
    try:
        result = test_response(
            args.url, args.key, args.model,
            reasoning_effort=args.reasoning_effort, timeout=args.timeout,
            prompt=args.prompt, protocol=args.protocol,
        )
        elapsed = time.perf_counter() - started
        answer = extract_answer(result.text)
        return {
            "run": run, "answer": answer, "answer_is_8": answer == 8,
            "time_bucket": time_bucket(elapsed), "elapsed_seconds": round(elapsed, 3),
            "text": result.text, "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "attempts": result.attempts, "error": None,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        return {
            "run": run, "answer": None, "answer_is_8": None,
            "time_bucket": time_bucket(elapsed), "elapsed_seconds": round(elapsed, 3),
            "text": None, "input_tokens": None, "output_tokens": None,
            "reasoning_tokens": None, "attempts": getattr(exc, "attempts", 1),
            "error": str(exc),
        }


def main() -> int:
    args = parse_args()
    with ThreadPoolExecutor(max_workers=min(args.workers, args.tests)) as pool:
        futures = [pool.submit(run_one, run, args) for run in range(1, args.tests + 1)]
        results = [future.result() for future in as_completed(futures)]
    results.sort(key=lambda item: int(item["run"]))
    graded = [item for item in results if item["answer_is_8"] is not None]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": args.url, "model": args.model,
        "reasoning_effort": args.reasoning_effort, "protocol": args.protocol,
        "stream": True, "tests": args.tests, "graded": len(graded),
        "answer_8": sum(item["answer_is_8"] is True for item in graded),
        "results": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = [{k: item[k] for k in ("run", "answer", "answer_is_8", "time_bucket", "elapsed_seconds", "reasoning_tokens", "attempts", "error")} for item in results]
    print(json.dumps({"report": str(output.resolve()), "graded": len(graded), "answer_8": payload["answer_8"], "results": summary}, ensure_ascii=False))
    return 0 if len(graded) == args.tests else 1


if __name__ == "__main__":
    raise SystemExit(main())
