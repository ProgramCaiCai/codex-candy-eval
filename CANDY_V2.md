# 糖果评测 v2

`candy_eval_old.py` 原样保留旧版题目、评分和 CLI。
`candy_eval.py` 默认对 A、B 分别发送独立请求，不共享对话。
A 可主动选形状，金标 21；B 只能盲抽且事前决定总数，金标 29。

```bash
python3 candy_eval.py -u https://example.com -k YOUR_KEY --tests 3 --workers 2 --output reports/candy-v2.json
python3 candy_eval.py -u https://example.com -k YOUR_KEY --case A
python3 candy_eval.py -u https://example.com -k YOUR_KEY --case B
python3 candy_eval_old.py -u https://example.com -k YOUR_KEY
```

`--tests` 是每道题的重复次数，默认双题模式下 3 轮共 6 次请求。
协议、模型、推理等级、超时和系统提示注入沿用原有参数。
`--prompt` 仅允许配合单题选择，覆盖题目后不套用内置金标。
`evaluate_once()` 默认只运行 A，现有 `sub2api_eval.py` 调用仍为单题；
双题入口为 `candy_eval.py` 或 `run_tests()`。

## 评分边界

题面要求完整证明，并要求末行写 `最终答案：N`。
自动评分读取明确的最终答案（也兼容纯数字回答），不会因为正文出现 21 就判对。
未识别或冲突的答案记为未评分，API 错误单独保存。
报告顶层 `correct` 统计同一轮 A=21 且 B=29 的轮数，两题都对才算该轮答对。
`graded` 统计两题均可评分的轮数，`rounds` 保留逐轮判定；缺题或无法评分的轮次为 `null`。
单独运行 A 或 B 只提供单题统计，不计为双题答对。
`results` 和 `by_case` 内的 `correct` 仍表示单题数字命中金标。
`proof_verified: null` 表示证明待人工审核，
不能据此认定整题通过或模型能力下降。

- A：审核可主动选择形状的策略、21 颗充分性、任何策略下 20 颗不够的下界。
- A 答 29：数字判错，并标记 `possible_blind_draw_misread`；不能单独判定模型降智。
- B：审核事先决定总数、28 颗失败构造及失败集合最多 28 颗的证明；答 29 数字正确。

报告含版本、逐题统计、每次实际题面、完整回答、金标、耗时和 token 用量。
退出码 0 表示所有请求均得到可评分答案（不表示答案都正确）；
退出码 1 表示存在请求失败或未评分结果，包括自定义题面。

```bash
python3 -m unittest discover -s tests
```
