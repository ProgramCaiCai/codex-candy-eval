# ModelTrace 评测

本仓库集成了 [xqy2006/ModelTrace](https://github.com/xqy2006/ModelTrace) 的统一模型指纹库与归因算法。
默认同时运行全部匹配账号，每个账号恰好并发发送 3 个长整数探针，不串行补测。
等待该账号的 3 个探针全部返回后，使用其中有效回答输出一行综合归因。终端与 JSON 报告只保留 Top 1 模型及概率；Top 1 与实际请求的映射后模型名完全一致时记为 `PASS`。
每个探针的模型输出上限固定为 4096 tokens。
ModelTrace 默认单探针超时为 180 秒且不做 transport 自动重试，避免单个慢请求拖住整批；可用
`--timeout 300 --request-retries 1` 显式放宽。

每个探针首先校验解析出的数字个数必须与要求完全一致；少返回或多返回均直接判定该账号评测失败，不补测、不继续归因。同批已发出的请求仍会收集结果。
如需针对请求错误补测，可显式传入 `--max-attempts 6`，整体耗时也会相应增加。
报告中的 `probe_details` 会记录每个探针的耗时、parsed count、token 和底层请求次数。

## 直连测试

```bash
python3 modeltrace_eval.py \
  --url https://api.example.com \
  --key "$API_KEY" \
  --model gpt-5.6-sol
```

可用 `--protocol anthropic` 切换 Anthropic Messages 协议，用 `--output report.json`
保存报告。推理等级默认为 `low`，可通过 `-r high` 等参数覆盖。

## sub2api 批量版

默认从仓库根目录 `.env` 加载 sub2api 管理凭据，并并发检测所有匹配账号：

```bash
./run_all_sub2api_modeltrace.sh --model gpt-5.6-sol
```

默认账号并发数等于匹配账号总数；可用 `--workers 4` 限流。常用参数还包括 `--account NAME`、`--protocol anthropic`、
`--inject-header` 和 `--output reports/result.json`。

快速模式只发送第一个探针，不补测，适合快速筛查：

```bash
./run_all_sub2api_modeltrace.sh --fast --model gpt-5.6-sol
./run_one_sub2api_modeltrace.sh ACCOUNT --fast --model gpt-5.6-sol
```

单探针置信度通常低于标准三探针模式，正式验收建议使用默认模式。

## sub2api 单账号版

```bash
./run_one_sub2api_modeltrace.sh ACCOUNT --model gpt-5.6-sol
```

`ACCOUNT` 使用不区分大小写的包含匹配，但匹配结果必须恰好为一个账号；存在多个候选时会拒绝运行，
避免误测其他账号。

## 输出与退出码

```text
PASS  station-a  probes=3  tested=gpt-5.6-sol  top1=gpt-5.6-sol  probability=82.10%
```

全部账号均为 `PASS` 时退出码为 `0`；出现归因不一致或请求错误时退出码为 `1`；配置、账号发现或
指纹库加载失败时退出码为 `2`。默认报告写入 `reports/sub2api-modeltrace-*.json`。

`modeltrace_data/unified_bank.json` 与归因方法来自上游 ModelTrace，按其 MIT License 使用；许可证位于
`modeltrace_data/LICENSE`。
