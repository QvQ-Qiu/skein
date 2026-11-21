"""OpenAI 兼容网关客户端。只发 JSON 补全，不持有业务语义。"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

log = logging.getLogger("skein.llm")


class LLMError(RuntimeError):
    pass


class HttpLLM:
    def __init__(self, base: str, key: str, timeout: float = 60.0) -> None:
        self._base = base.rstrip("/")
        self._key = key
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.on_call: Callable[[dict[str, Any]], None] | None = None

    def _emit(self, payload: dict[str, Any]) -> None:
        if self.on_call is None:
            return
        try:
            self.on_call(payload)
        except Exception:
            log.debug("llm trace failed", exc_info=True)

    @staticmethod
    def _tokens(usage: Any, *names: str) -> int:
        if not isinstance(usage, dict):
            return 0
        for name in names:
            try:
                value = int(usage.get(name) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0

    def complete(self, request: dict) -> dict:
        started = time.monotonic()
        body = dict(request)
        body.setdefault("response_format", {"type": "json_object"})
        data = json.dumps(body, ensure_ascii=False).encode()
        prompt = body.get("messages", body)
        prompt_text = json.dumps(prompt, ensure_ascii=False, default=str)
        model = str(body.get("model") or "")
        http_request = urllib.request.Request(
            f"{self._base}/chat/completions",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
        )

        def emit(**extra: Any) -> None:
            payload: dict[str, Any] = {
                "session_id": "",
                "actor": "",
                "source": "skein",
                "model": model,
                "prompt": prompt_text,
                "response": "",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "status": "ok",
                "error": "",
            }
            payload.update(extra)
            self._emit(payload)

        try:
            with self._opener.open(http_request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            emit(status="error", error=f"gateway returned {exc.code}")
            raise LLMError(f"gateway returned {exc.code}") from exc
        except urllib.error.URLError as exc:
            emit(status="error", error=str(exc.reason))
            raise LLMError(str(exc.reason)) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            emit(
                status="error",
                response=raw[:2000].decode(errors="replace"),
                error="gateway returned a non-json body",
            )
            raise LLMError("gateway returned a non-json body") from exc

        usage = payload.get("usage") or {}
        content_text = ""
        try:
            content = payload["choices"][0]["message"]["content"]
            content_text = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, default=str)
            )
            result = json.loads(content_text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            emit(
                status="error",
                response=content_text,
                error="gateway returned an unparsable body",
            )
            raise LLMError("gateway returned an unparsable body") from exc

        emit(
            response=content_text,
            prompt_tokens=self._tokens(usage, "prompt_tokens", "input_tokens"),
            completion_tokens=self._tokens(usage, "completion_tokens", "output_tokens"),
        )
        return result
