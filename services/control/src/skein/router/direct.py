"""空工具直答：聊天的第一句由 Router 写，这次调用里没有任何工具。

约束不是提示词，是结构：
- 请求里不允许出现 tools 等字段，构造时校验；
- 响应只接受 say / dispatch 两个动作；
- 出现工具调用、非法 JSON、say 但正文为空，一律按 dispatch 处理并丢弃正文。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from skein.llm import LLMClient

FORBIDDEN_REQUEST_KEYS = ("tools", "tool_choice", "functions", "function_call")
FORBIDDEN_RESPONSE_KEYS = ("tool_use", "tool_calls", "function_call")

DISPATCH_RESULT_TEXT = "收到，我转给负责的同事处理。"


@dataclass(frozen=True)
class Reply:
    action: Literal["say", "dispatch"]
    text: str | None = None


DISPATCH = Reply("dispatch", None)


class DirectRequestError(ValueError):
    pass


def ensure_no_tools(request: dict) -> dict:
    for key in FORBIDDEN_REQUEST_KEYS:
        if key in request:
            raise DirectRequestError(f"direct reply must not carry {key}")
    return request


def build_direct_request(model: str, messages: list[dict], max_tokens: int = 800) -> dict:
    return ensure_no_tools({"model": model, "messages": messages, "max_tokens": max_tokens})


def parse_direct_response(raw: object) -> Reply:
    if not isinstance(raw, dict):
        return DISPATCH

    for key in FORBIDDEN_RESPONSE_KEYS:
        if key in raw:
            return DISPATCH

    action = raw.get("action")
    if action == "dispatch":
        return DISPATCH
    if action == "say":
        text = raw.get("text")
        if isinstance(text, str) and text.strip():
            return Reply("say", text.strip())
    return DISPATCH


class DirectRouter:
    def __init__(self, llm: LLMClient, model: str) -> None:
        self._llm = llm
        self._model = model

    def reply(self, messages: list[dict]) -> Reply:
        request = build_direct_request(self._model, messages)
        raw = self._llm.complete(request)
        return parse_direct_response(raw)
