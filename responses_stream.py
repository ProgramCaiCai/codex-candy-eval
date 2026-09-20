"""Streaming transport for direct OpenAI Responses API requests."""

from __future__ import annotations

import json
import os
import http.client
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any


CODEX_USER_AGENT = os.getenv(
    "CODEX_USER_AGENT",
    "codex_cli_rs/0.151.0 (Ubuntu 24.04.0; x86_64) xterm-256color",
)
CODEX_ORIGINATOR = "codex_cli_rs"
MAX_RETRIES = 3
RETRY_DELAYS_SECONDS = (1, 2, 4)


@dataclass(frozen=True)
class ResponseResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    attempts: int = 1


class RequestError(RuntimeError):
    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


class IncompleteStreamError(RuntimeError):
    pass


def stream_response_request(
    url: str, payload: dict[str, Any], token: str, *, timeout: float,
    headers: dict[str, str] | None = None,
    max_retries: int = MAX_RETRIES,
    total_timeout: float | None = None,
) -> ResponseResult:
    last_error: RuntimeError | None = None
    last_cause: BaseException | None = None
    deadline = time.monotonic() + total_timeout if total_timeout is not None else None
    for retry in range(max_retries + 1):
        try:
            result = _request_once(
                url, payload, token, timeout=timeout, headers=headers, deadline=deadline,
            )
            return replace(result, attempts=retry + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(64 * 1024).decode("utf-8", "replace")
            last_error = _http_error(exc.code, raw)
            last_cause = exc
            retryable = _retryable_http_status(exc.code)
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException, IncompleteStreamError) as exc:
            last_error = RuntimeError(f"Request failed for {_safe_origin(url)}: {_error_reason(exc)}")
            last_cause = exc
            retryable = True
        if not retryable or retry == max_retries:
            attempts = retry + 1
            suffix = f" (after {attempts} attempts)" if attempts > 1 else ""
            raise RequestError(f"{last_error}{suffix}", attempts) from last_cause
        time.sleep(RETRY_DELAYS_SECONDS[retry])
    raise AssertionError("unreachable")


def _request_once(
    url: str, payload: dict[str, Any], token: str, *, timeout: float,
    headers: dict[str, str] | None = None,
    deadline: float | None = None,
) -> ResponseResult:
    body = json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "User-Agent": CODEX_USER_AGENT,
            "originator": CODEX_ORIGINATOR,
            "Authorization": f"Bearer {token}",
            **(headers or {}),
        },
    )
    read_timeout = min(timeout, 30.0) if deadline is not None else timeout
    with urllib.request.urlopen(request, timeout=read_timeout) as response:
        return _parse_sse_response(response, deadline)


def _parse_sse_response(response: Any, deadline: float | None = None) -> ResponseResult:
    deltas: list[str] = []
    completed: dict[str, Any] = {}
    data_lines: list[str] = []
    for raw_line in response:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("total request timeout exceeded")
        line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line == "" and data_lines:
            _consume_sse_event(_parse_sse_data(data_lines), deltas, completed)
            data_lines.clear()
    if data_lines:
        _consume_sse_event(_parse_sse_data(data_lines), deltas, completed)
    if not completed:
        raise IncompleteStreamError("stream ended before response.completed")
    return _build_result(deltas, completed)


def _parse_sse_data(lines: list[str]) -> dict[str, Any]:
    raw = "\n".join(lines)
    if raw == "[DONE]":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid Responses SSE event: {raw[:200]!r}") from exc
    return parsed if isinstance(parsed, dict) else {}


def _consume_sse_event(
    event: dict[str, Any], deltas: list[str], completed: dict[str, Any]
) -> None:
    event_type = event.get("type")
    if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
        deltas.append(event["delta"])
    elif event_type == "response.completed" and isinstance(event.get("response"), dict):
        completed.update(event["response"])


def _build_result(deltas: list[str], completed: dict[str, Any]) -> ResponseResult:
    usage = completed.get("usage") if isinstance(completed.get("usage"), dict) else {}
    details = usage.get("output_tokens_details")
    details = details if isinstance(details, dict) else {}
    return ResponseResult(
        text="".join(deltas) or _extract_output_text(completed),
        input_tokens=_integer_or_none(usage.get("input_tokens")),
        output_tokens=_integer_or_none(usage.get("output_tokens")),
        reasoning_tokens=_integer_or_none(details.get("reasoning_tokens")),
    )


def _extract_output_text(response: dict[str, Any]) -> str:
    texts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                if isinstance(content.get("text"), str):
                    texts.append(content["text"])
    return "\n".join(texts)


def _http_error(status: int, raw: str) -> RuntimeError:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return RuntimeError(f"HTTP {status}: {raw.strip()[:500] or 'empty response'}")
    message: Any = parsed.get("message") or parsed.get("error") or parsed
    if isinstance(message, dict):
        message = message.get("message") or message.get("code") or message
    return RuntimeError(f"HTTP {status}: {str(message)[:500]}")


def _safe_origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _retryable_http_status(status: int) -> bool:
    return status in {408, 425, 429} or 500 <= status <= 599


def _error_reason(exc: BaseException) -> object:
    return exc.reason if isinstance(exc, urllib.error.URLError) else exc


def _integer_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
