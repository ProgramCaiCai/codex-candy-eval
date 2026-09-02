# 模型推理题评测

项目提供两个直连评测入口和两个 sub2api 工具：

- `candy_eval.py`：糖果题评测，正确答案为 `21`
- `cup_eval.py`：水杯配对题评测，正确答案为 `8`
- `sub2api_info_extract.py`：只从 sub2api 提取 provider 信息
- `sub2api_eval.py`：提取 provider 后批量运行糖果题评测

`candy_eval.py` 和 `cup_eval.py` 使用相同的连接参数。`sub2api_eval.py` 复用
`candy_eval.evaluate_once()`，不会重复实现请求、协议选择或糖果题判分逻辑。

所有请求都直连 provider，不经过 sub2api 网关，也不依赖 Codex CLI。

## 要求

- Python 3.10+
- 使用 sub2api 工具时，需要管理员 token，或管理员邮箱和密码
- sub2api 开启 step-up 2FA 时，还需要当前 TOTP 验证码

脚本只有 Python 标准库依赖。

## 直连评测

糖果题只要求 `url` 和 `key`：

```bash
python3 candy_eval.py \
  --url https://provider.example/v1 \
  --key "$PROVIDER_KEY"
```

默认模型为 `gpt-5.6-sol`，默认协议为 `responses`。指定 Anthropic Messages 协议：

```bash
python3 candy_eval.py \
  --url https://provider.example \
  --key "$PROVIDER_KEY" \
  --model claude-opus-5 \
  --protocol anthropic
```

水杯题入口格式完全相同：

```bash
python3 cup_eval.py \
  --url https://provider.example/v1/messages \
  --key "$PROVIDER_KEY" \
  --model claude-opus-5 \
  --protocol anthropic
```

两个直连入口的公共参数：

- `-u, --url`：provider 根地址、`/v1` 地址或完整协议端点，必填
- `-k, --key`：provider API key，必填
- `-m, --model`：模型名，默认 `gpt-5.6-sol`
- `-p, --protocol`：`responses` 或 `anthropic`，默认 `responses`
- `-r, --reasoning-effort`：推理等级
- `--tests`：测试次数，默认 `1`
- `--workers`：最大并发数，默认 `1`
- `--timeout`：单次请求超时秒数，默认 `600`
- `--output`：完整 JSON 报告路径

协议不会根据模型名自动切换。即使模型名以 `claude` 开头，不传 `--protocol` 仍使用
Responses；使用 Anthropic Messages 必须显式传 `--protocol anthropic`。CLI 也接受
`response` 和 `anthropics` 拼写，并分别归一化为上述标准值。

`--url` 支持根地址、`/v1`、`/v1/responses`、`/v1/messages` 和
`/v1/chat/completions`；脚本会根据协议生成最终端点。

## sub2api 信息提取

默认只输出账号名、URL 和映射后的模型，不输出 key：

```bash
python3 sub2api_info_extract.py --model gpt-5.6-sol --json
```

确实需要导出 key 时显式添加 `--include-key`：

```bash
python3 sub2api_info_extract.py --json --include-key
```

JSON 中的字段名与直连入口一致：`url`、`key`、`model`。
管理接口请求的 `--timeout` 默认也是 `600` 秒，可按需覆盖。

## sub2api 批量评测

先只查看会参与测试的账号，不产生模型费用：

```bash
export SUB2API_ADMIN_TOKEN='your-admin-jwt'
python3 sub2api_eval.py --list-only
```

对全部匹配账号各测试一次 `gpt-5.6-sol`：

```bash
python3 sub2api_eval.py -m gpt-5.6-sol -r high -n 1 -j 4
```

通过 Anthropic Messages 协议测试所有匹配 provider：

```bash
python3 sub2api_eval.py --model claude-opus-5 --protocol anthropic
```

也可以通过管理员账号登录：

```bash
export SUB2API_ADMIN_EMAIL='admin@example.com'
export SUB2API_ADMIN_PASSWORD='your-password'
python3 sub2api_eval.py -n 1
```

本机 sub2api 部署的 `.env` 已配置 `ADMIN_EMAIL` 和 `ADMIN_PASSWORD` 时，可直接读取：

```bash
python3 sub2api_eval.py \
  --env-file /path/to/sub2api-deploy/.env \
  --list-only
```

如果 sub2api 开启 TOTP 登录或敏感操作 step-up：

```bash
export SUB2API_TOTP_CODE='123456'
python3 sub2api_eval.py --list-only
```

常用参数：

- `--sub2api-url`：sub2api 地址，默认 `http://127.0.0.1:8080`
- `-m, --model`：请求模型，默认 `gpt-5.6-sol`
- `-p, --protocol`：`responses` 或 `anthropic`，默认 `responses`
- `-r, --reasoning-effort`：`none/low/medium/high/xhigh/max/ultra`，默认 `high`
- `-n, --tests`：每个账号测试次数，默认 `1`
- `-j, --workers`：最大并发请求数，默认 `4`；同一 provider 的多次测试也可并发
- `--account TEXT`：只测试名称包含 `TEXT` 的账号
- `--timeout`：每次请求超时秒数，默认 `600`
- `--list-only`：只做账号发现与模型筛选，不发送模型请求
- `--output`：JSON 报告路径；默认写入 `reports/` 下的 UTC 时间戳文件

账号配置存在 `model_mapping` 时，只选择能精确或通配符匹配目标模型的账号，并向上游
发送映射后的真实模型名。未配置 `model_mapping` 时按透传账号处理。

Responses 请求使用 Bearer token，并带上 Codex 形态的 `User-Agent` 与
`originator: codex_cli_rs`。Anthropic 请求使用 `x-api-key` 和
`anthropic-version: 2023-06-01`。两种协议都使用 SSE 流式响应。

每次批量测试都会保存完整回答、错误、判分结果以及 input/output/reasoning token 数；
报告不会包含账号 API key。连接异常、流提前结束以及 HTTP 408/425/429/5xx 会在首次
失败后最多重试 3 次，并在报告中记录实际请求次数。

默认 User-Agent 与本机 Codex CLI 版本一致；可通过 `CODEX_USER_AGENT` 环境变量覆盖，
脚本本身不会执行或依赖 Codex CLI。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
