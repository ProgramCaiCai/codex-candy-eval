#!/usr/bin/env python3
"""从 sub2api 独立提取 provider 的 URL、key 与映射后模型。"""

from __future__ import annotations

import argparse
import json
import os
import sys

from response_client import AccountTarget, Sub2APIClient, load_env_file, resolve_targets

DEFAULT_MODEL = "gpt-5.6-sol"


def fetch_account_targets(
    sub2api_url: str,
    model: str,
    *,
    timeout: float = 300.0,
    admin_token: str | None = None,
    admin_email: str | None = None,
    admin_password: str | None = None,
    totp_code: str | None = None,
    account_filter: str | None = None,
    active_only: bool = False,
) -> tuple[list[AccountTarget], list[tuple[str, str]]]:
    client = Sub2APIClient(sub2api_url, timeout, admin_token)
    if not admin_token:
        if not admin_email or not admin_password:
            raise RuntimeError("需要 SUB2API_ADMIN_TOKEN，或同时提供管理员邮箱和密码")
        client.login(admin_email, admin_password, totp_code)
    accounts = client.export_api_key_accounts(totp_code)
    if active_only:
        # 导出接口通常省略 status；只有字段明确存在且非 active 时才过滤。
        accounts = [account for account in accounts
                    if "status" not in account
                    or str(account.get("status", "")).casefold() == "active"]
    targets, skipped = resolve_targets(accounts, model)
    if account_filter:
        needle = account_filter.casefold()
        targets = [target for target in targets if needle in target.name.casefold()]
    return targets, skipped


def discover_accounts(args: argparse.Namespace) -> tuple[list[AccountTarget], list[tuple[str, str]]]:
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


def parse_args() -> argparse.Namespace:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file", default=os.getenv("SUB2API_ENV_FILE"))
    preliminary, _ = pre.parse_known_args()
    load_env_file(preliminary.env_file)
    parser = argparse.ArgumentParser(description=__doc__, parents=[pre])
    parser.add_argument("--sub2api-url", default=os.getenv("SUB2API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--admin-token", default=os.getenv("SUB2API_ADMIN_TOKEN"))
    parser.add_argument("--admin-email", default=os.getenv("SUB2API_ADMIN_EMAIL") or os.getenv("ADMIN_EMAIL"))
    parser.add_argument("--admin-password", default=os.getenv("SUB2API_ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD"))
    parser.add_argument("--totp-code", default=os.getenv("SUB2API_TOTP_CODE"))
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL)
    parser.add_argument("--account")
    parser.add_argument("--timeout", type=positive_float, default=300.0)
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--include-key", action="store_true", help="JSON 输出中包含 API key")
    return parser.parse_args()


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def main() -> int:
    args = parse_args()
    try:
        targets, skipped = discover_accounts(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        payload = {
            "accounts": [_target_dict(target, args.include_key) for target in targets],
            "skipped": [{"account": name, "reason": reason} for name, reason in skipped],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for target in targets:
            print(f"{target.name}\t{target.responses_url}\t{target.model}")
        print(f"Matched accounts: {len(targets)}  skipped: {len(skipped)}", file=sys.stderr)
    return 0 if targets else 1


def _target_dict(target: AccountTarget, include_key: bool) -> dict[str, str]:
    result = {
        "account": target.name,
        "url": target.responses_url,
        "model": target.model,
    }
    if include_key:
        result["key"] = target.api_key
    return result


if __name__ == "__main__":
    sys.exit(main())
