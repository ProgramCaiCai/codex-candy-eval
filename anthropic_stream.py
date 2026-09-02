"""Anthropic Messages SSE transport with connection retries."""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Any

from responses_stream import CODEX_USER_AGENT, RequestError, ResponseResult

MAX_RETRIES = 3
RETRY_DELAYS_SECONDS = (1, 2, 4)


class IncompleteStreamError(RuntimeError):
    pass


def stream_anthropic_request(
    url: str, payload: dict[str, Any], token: str, *, timeout: float
) -> ResponseResult:
    last_error: RuntimeError | None = None
    last_cause: BaseException | None = None
    for retry in range(MAX_RETRIES + 1):
        try:
            return replace(
                _request_once(url, payload, token, timeout=timeout), attempts=retry + 1
            )
        except urllib.error.HTTPError as exc:
            last_error = _http_error(exc.code, exc.read(64 * 1024).decode("utf-8", "replace"))
            last_cause = exc
            retryable = exc.code in {408, 425, 429} or 500 <= exc.code <= 599
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException, IncompleteStreamError) as exc:
            last_error = RuntimeError(f"Request failed for {_safe_origin(url)}: {_reason(exc)}")
            last_cause = exc
            retryable = True
        if not retryable or retry == MAX_RETRIES:
            attempts = retry + 1
            suffix = f" (after {attempts} attempts)" if attempts > 1 else ""
            raise RequestError(f"{last_error}{suffix}", attempts) from last_cause
        time.sleep(RETRY_DELAYS_SECONDS[retry])
    raise AssertionError("unreachable")


def _request_once(
    url: str, payload: dict[str, Any], token: str, *, timeout: float
) -> ResponseResult:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": CODEX_USER_AGENT,
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _parse_stream(response)


def _parse_stream(response: Any) -> ResponseResult:
    texts: list[str] = []
    usage: dict[str, Any] = {}
    stopped = False
    for event in _sse_events(response):
        event_type = event.get("type")
        if event_type == "message_start":
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("usage"), dict):
                usage.update(message["usage"])
        elif event_type == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict) and block.get("type") == "text":
                if isinstance(block.get("text"), str):
                    texts.append(block["text"])
        elif event_type == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                if isinstance(delta.get("text"), str):
                    texts.append(delta["text"])
        elif event_type == "message_delta" and isinstance(event.get("usage"), dict):
            usage.update(event["usage"])
        elif event_type == "message_stop":
            stopped = True
        elif event_type == "error":
            raise RuntimeError(f"Anthropic stream error: {_error_message(event)}")
    if not stopped:
        raise IncompleteStreamError("stream ended before message_stop")
    return ResponseResult(
        text="".join(texts),
        input_tokens=_integer(usage.get("input_tokens")),
        output_tokens=_integer(usage.get("output_tokens")),
        reasoning_tokens=_reasoning_tokens(usage),
    )


def _sse_events(response: Any):
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "" and data_lines:
            yield _parse_data(data_lines)
            data_lines.clear()
    if data_lines:
        yield _parse_data(data_lines)


def _parse_data(lines: list[str]) -> dict[str, Any]:
    raw = "\n".join(lines)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid Anthropic SSE event: {raw[:200]!r}") from exc
    return value if isinstance(value, dict) else {}


def _reasoning_tokens(usage: dict[str, Any]) -> int | None:
    direct = _integer(usage.get("reasoning_tokens"))
    details = usage.get("output_tokens_details")
    return direct if direct is not None else (
        _integer(details.get("reasoning_tokens")) if isinstance(details, dict) else None
    )


def _http_error(status: int, raw: str) -> RuntimeError:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return RuntimeError(f"HTTP {status}: {raw.strip()[:500] or 'empty response'}")
    return RuntimeError(f"HTTP {status}: {_error_message(parsed)[:500]}")


def _error_message(value: Any) -> str:
    if isinstance(value, dict):
        error = value.get("error") or value.get("message") or value
        if isinstance(error, dict):
            error = error.get("message") or error.get("type") or error
        return str(error)
    return str(value)


def _safe_origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _reason(exc: BaseException) -> object:
    return exc.reason if isinstance(exc, urllib.error.URLError) else exc


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
