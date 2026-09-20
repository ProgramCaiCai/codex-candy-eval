"""Pure-Python subset of ModelTrace's MIT-licensed attribution algorithm."""

from __future__ import annotations

import json
import math
import random
import re
import secrets
from pathlib import Path
from typing import Iterable

VALUE_MIN = 1
VALUE_MAX = 355
DIMENSION = 355
ALPHA = 0.5
DEFAULT_BANK = Path(__file__).with_name("modeltrace_data") / "unified_bank.json"
FAMILY_NAMES = {"gpt": "GPT", "claude": "Claude"}


def parse_numbers(text: str) -> list[int]:
    runs: list[list[int]] = []
    current: list[int] = []
    previous_end = 0
    for match in re.finditer(r"\d+", text):
        separator = text[previous_end:match.start()]
        value = int(match.group())
        if current and any(character.isalpha() for character in separator):
            runs.append(current)
            current = []
        if VALUE_MIN <= value <= VALUE_MAX:
            current.append(value)
        previous_end = match.end()
    if current:
        runs.append(current)
    return max(runs, key=len) if runs else []


def count_numbers(numbers: Iterable[int]) -> list[int]:
    counts = [0] * DIMENSION
    for number in numbers:
        counts[number - 1] += 1
    return counts


def standardize(values: list[float]) -> list[float]:
    mean = sum(values) / len(values)
    scale = max(math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)), 1e-12)
    return [(value - mean) / scale for value in values]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _project(values: list[float], basis: list[list[float]]) -> list[float]:
    result = values[:]
    for direction in basis:
        coefficient = _dot(result, direction)
        result = [value - coefficient * component for value, component in zip(result, direction)]
    return result


def _normalized(values: list[float]) -> list[float]:
    norm = max(math.sqrt(_dot(values, values)), 1e-12)
    return [value / norm for value in values]


def _scores(feature: list[float], centroids: list[list[float]]) -> list[float]:
    return [_dot(feature, centroid) for centroid in centroids]


def hellinger_feature(counts: list[int]) -> list[float]:
    values = [value + ALPHA for value in counts]
    total = sum(values)
    return [math.sqrt(value / total) for value in values]


def _histogram(values: list[int], bins: int = 16) -> list[int]:
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int((value - 1) * bins / DIMENSION))
        counts[index] += 1
    return counts


def ordered_block_feature(numbers: list[int]) -> list[float]:
    size, remainder = divmod(len(numbers), 4)
    pieces: list[float] = []
    offset = 0
    for index in range(4):
        length = size + (1 if index < remainder else 0)
        smoothed = [value + ALPHA for value in _histogram(numbers[offset:offset + length])]
        total = sum(smoothed)
        pieces.extend(math.sqrt(value / total) for value in smoothed)
        offset += length
    last_digits = [ALPHA] * 10
    for value in numbers:
        last_digits[value % 10] += 1
    total = sum(last_digits)
    pieces.extend(math.sqrt(value / total) for value in last_digits)
    return pieces


def _standardized_feature(feature: list[float], artifact: dict) -> list[float]:
    return [
        (value - mean) / scale
        for value, mean, scale in zip(feature, artifact["feature_mean"], artifact["feature_scale"])
    ]


def robust_scores(numbers: list[int], bank: dict) -> list[float]:
    robust = bank["robust"]
    hellinger = robust["hellinger"]
    feature = _standardized_feature(hellinger_feature(count_numbers(numbers)), hellinger)
    marginal = standardize(_scores(_normalized(_project(feature, hellinger["nuisance_basis"])), hellinger["centroids"]))
    ordered = robust.get("ordered_blocks")
    weight = float(ordered.get("weight", 0.0)) if ordered else 0.0
    if not ordered or weight == 0:
        return marginal
    ordered_feature = _standardized_feature(ordered_block_feature(numbers), ordered)
    normalized = _normalized(ordered_feature)
    environment_scores = [_scores(normalized, centroids) for centroids in ordered["environment_centroids"]]
    template = standardize([max(scores[index] for scores in environment_scores) for index in range(len(marginal))])
    nuisance = standardize(_scores(_normalized(_project(ordered_feature, ordered["nuisance_basis"])), ordered["centroids"]))
    ordered_scores = standardize([(a + b) * 0.5 for a, b in zip(template, nuisance)])
    return [(1 - weight) * a + weight * b for a, b in zip(marginal, ordered_scores)]


def _softmax(values: list[float]) -> list[float]:
    maximum = max(values)
    weights = [math.exp(value - maximum) for value in values]
    total = sum(weights)
    return [value / total for value in weights]


def analyze_global_outputs(outputs: list[dict], bank: dict) -> dict:
    parsed_outputs = [parse_numbers(str(output.get("text", ""))) for output in outputs]
    for index, (output, numbers) in enumerate(zip(outputs, parsed_outputs)):
        expected = int(output.get("expected_count") or 0)
        if len(numbers) != expected or expected <= 0:
            raise ValueError(f"probe-{index + 1}: 数字个数不符 {len(numbers)}/{expected}")
    valid: list[list[float]] = []
    diagnostics = []
    for index, output in enumerate(outputs):
        numbers = parsed_outputs[index]
        expected = int(output.get("expected_count") or 0)
        minimum = expected
        accepted = len(numbers) == expected
        diagnostics.append({"index": index, "parsed_numbers": len(numbers), "minimum_numbers": minimum, "accepted": accepted})
        if accepted:
            valid.append(robust_scores(numbers, bank))
    if not valid:
        raise ValueError("没有可用回答：模型未返回足够的完整数字序列")
    model_ids = bank["robust"]["model_order"]
    combined = [sum(scores[index] for scores in valid) / len(valid) for index in range(len(model_ids))]
    calibration_key = str(min(len(valid), 3))
    beta = float(bank["calibration"][calibration_key]["beta"])
    probabilities = _softmax([beta * score for score in combined])
    metadata = {model["id"]: model for model in bank["models"]}
    results = [
        {
            "model": model_id,
            "display_name": metadata[model_id]["display_name"],
            "probability": probabilities[index],
        }
        for index, model_id in enumerate(model_ids)
    ]
    results.sort(key=lambda item: item["probability"], reverse=True)
    family_probabilities: dict[str, float] = {}
    for item in results:
        family = metadata[item["model"]].get("family") or "models"
        family_probabilities[family] = family_probabilities.get(family, 0.0) + item["probability"]
    family = max(family_probabilities, key=family_probabilities.get)
    return {
        "prediction": results[0]["model"],
        "prediction_name": results[0]["display_name"],
        "probability": results[0]["probability"],
        "family_prediction": family,
        "family_prediction_name": FAMILY_NAMES.get(family, family),
        "family_probability": family_probabilities[family],
        "used_outputs": len(valid),
        "results": results,
        "diagnostics": diagnostics,
    }


def load_bank(path: str | Path = DEFAULT_BANK) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def generate_challenges(count: int = 6) -> list[dict]:
    rng = random.SystemRandom()
    lengths = rng.sample(range(292, 334), count)
    openings = ("这是一次独立的数值选择记录", "请完成下面的无语义整数选择任务", "进行一轮快速逐项取值")
    endings = ("允许重复值；不要排序或去重。", "偶然重复是有效的；不要重排或去重。")
    challenges = []
    for index, length in enumerate(lengths, 1):
        prompt = (
            f"{rng.choice(openings)}。必须恰好分别凭第一反应给出 {length} 个 1 到 355（含端点）的整数。"
            f"数量是硬性验收条件：JSON 数组长度必须严格等于 {length}，多一个或少一个都直接判定失败。"
            "禁止调用任何工具、代码执行器、计算器、搜索、API 或随机数生成器。"
            "数值不要连续递增递减，不要使用等差、循环、重复区块或其他规则化模式。"
            f"请在内部准确跟踪已生成的元素个数，输出前核对总数恰好为 {length}；"
            "不足时补齐，多余时移除末尾多出的元素，不得输出数量未核对的数组。"
            f"{rng.choice(endings)}只输出一个单行紧凑 JSON 数组，不要解释或展示思考过程。"
            f"最终检查：恰好 {length} 个整数，每个都在 1 到 355 范围内，无省略号、无额外文字。"
        )
        challenges.append({"id": f"probe-{index}-{secrets.token_hex(7)}", "expected_count": length, "prompt": prompt})
    return challenges
