"""sub2api account discovery and provider configuration parsing."""

from __future__ import annotations

import json
import os
import shlex
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any



@dataclass(frozen=True)
class AccountTarget:
    name: str
    responses_url: str
    api_key: str = field(repr=False)
    model: str


class APIError(RuntimeError):
    def __init__(self, status: int, message: str, code: str = "") -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.code = code


def load_env_file(path: str | None) -> None:
    """Load a small, shell-compatible .env subset without overwriting env vars."""
    if not path:
        return
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "a").isalnum():
            continue
        try:
            parsed = shlex.split(value, comments=True, posix=True)
        except ValueError as exc:
            raise ValueError(f"Invalid .env value for {key}: {exc}") from exc
        os.environ.setdefault(key, parsed[0] if parsed else "")


def _json_request(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 30,
    unwrap: bool = False,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read(64 * 1024).decode("utf-8", "replace")
        raise _http_error(exc.code, raw) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {_safe_origin(url)}: {exc.reason}") from exc
    return _unwrap_sub2api(parsed) if unwrap else parsed


def _http_error(status: int, raw: str) -> APIError:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return APIError(status, raw.strip()[:500] or "empty response")
    if not isinstance(parsed, dict):
        return APIError(status, str(parsed)[:500])
    message = parsed.get("message") or parsed.get("error") or parsed
    if isinstance(message, dict):
        message = message.get("message") or message.get("code") or message
    return APIError(status, str(message)[:500], str(parsed.get("code", "")))


def _unwrap_sub2api(parsed: Any) -> Any:
    if not isinstance(parsed, dict) or "code" not in parsed:
        return parsed
    if parsed.get("code") not in (0, "0"):
        raise APIError(200, str(parsed.get("message", "sub2api API error")), str(parsed["code"]))
    return parsed.get("data")


def _safe_origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _validated_url(value: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid HTTP base URL: {value!r}")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in base URLs are not supported")
    return parsed


def _join_sub2api_url(base_url: str, endpoint: str) -> str:
    parsed = _validated_url(base_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/api/v1"):
        path += "/api/v1"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path + endpoint, "", ""))


def build_responses_url(base_url: str) -> str:
    parsed = _validated_url(base_url)
    path = parsed.path.rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if path.endswith("/v1/responses") or path.endswith("/responses"):
        response_path = path
    elif path.endswith("/v1"):
        response_path = path + "/responses"
    else:
        response_path = path + "/v1/responses"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, response_path, "", ""))


class Sub2APIClient:
    def __init__(self, base_url: str, timeout: float, token: str | None = None) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.token = token

    def login(self, email: str, password: str, totp_code: str | None = None) -> None:
        data = self._call("/auth/login", {"email": email, "password": password}, auth=False)
        if isinstance(data, dict) and data.get("requires_2fa"):
            if not totp_code:
                raise RuntimeError("sub2api login requires SUB2API_TOTP_CODE")
            data = self._call(
                "/auth/login/2fa",
                {"temp_token": data.get("temp_token"), "totp_code": totp_code},
                auth=False,
            )
        if not isinstance(data, dict) or not data.get("access_token"):
            raise RuntimeError("sub2api login did not return an access token")
        self.token = str(data["access_token"])

    def export_api_key_accounts(self, totp_code: str | None = None) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({
            "platform": "openai",
            "type": "apikey",
            "include_proxies": "false",
        })
        try:
            data = self._call(f"/admin/accounts/data?{query}")
        except APIError as exc:
            if not totp_code or "STEP_UP" not in exc.code.upper():
                raise
            self._call("/user/totp/step-up", {"code": totp_code})
            data = self._call(f"/admin/accounts/data?{query}")
        accounts = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(accounts, list):
            raise RuntimeError("sub2api account export returned an unexpected response")
        return [item for item in accounts if isinstance(item, dict)]

    def _call(self, endpoint: str, payload: dict[str, Any] | None = None, auth: bool = True) -> Any:
        if auth and not self.token:
            raise RuntimeError("sub2api admin authentication is required")
        return _json_request(
            _join_sub2api_url(self.base_url, endpoint),
            payload=payload,
            token=self.token if auth else None,
            timeout=self.timeout,
            unwrap=True,
        )


def resolve_targets(
    accounts: list[dict[str, Any]], requested_model: str
) -> tuple[list[AccountTarget], list[tuple[str, str]]]:
    targets: list[AccountTarget] = []
    skipped: list[tuple[str, str]] = []
    for index, account in enumerate(accounts, 1):
        name = str(account.get("name") or f"account-{index}")
        credentials = account.get("credentials")
        if not isinstance(credentials, dict):
            skipped.append((name, "missing credentials"))
            continue
        mapped_model = _mapped_model(credentials.get("model_mapping"), requested_model)
        if mapped_model is None:
            skipped.append((name, f"model mapping excludes {requested_model}"))
            continue
        api_key = credentials.get("api_key")
        base_url = credentials.get("base_url") or "https://api.openai.com"
        if not isinstance(api_key, str) or not api_key.strip():
            skipped.append((name, "missing api_key"))
            continue
        try:
            responses_url = build_responses_url(str(base_url))
        except ValueError as exc:
            skipped.append((name, str(exc)))
            continue
        targets.append(AccountTarget(name, responses_url, api_key.strip(), mapped_model))
    return targets, skipped


def _mapped_model(mapping: Any, requested_model: str) -> str | None:
    if not isinstance(mapping, dict) or not mapping:
        return requested_model
    pairs = {str(key): str(value).strip() for key, value in mapping.items()}
    if requested_model in pairs:
        return pairs[requested_model] or None
    matches = [key for key in pairs if key.endswith("*") and requested_model.startswith(key[:-1])]
    if not matches:
        return None
    pattern = sorted(matches, key=lambda key: (-len(key), key))[0]
    return pairs[pattern] or None
