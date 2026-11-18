"""空工具直答：聊天的第一句由 Router 写，这次调用里没有任何工具。

约束不是提示词，是结构：
- 请求里不允许出现 tools 等字段，构造时校验；
- 响应只接受 say / dispatch 两个动作；
- 出现工具调用、非法 JSON、say 但正文为空，一律按 dispatch 处理并丢弃正文。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from skein.llm import LLMClient

FORBIDDEN_REQUEST_KEYS = (
    "tools",
    "tool_choice",
    "functions",
    "function_call",
    "tool_calls",
    "tool_use",
)
FORBIDDEN_RESPONSE_KEYS = ("tool_use", "tool_calls", "function_call")
TOOL_BLOCK_TYPES = ("tool_use", "tool_call", "tool_result")

DISPATCH_RESULT_TEXT = "收到，我转给负责的同事处理。"

DIRECT_SYSTEM_PROMPT = (
    "你是 Router 的对话入口。只输出一个 JSON 对象，二选一："
    '{"action":"say","text":"..."} 或 {"action":"dispatch"}。'
    "你没有工具，也不能要求使用工具；需要动手时只能返回 dispatch。"
)


@dataclass(frozen=True)
class Reply:
    action: Literal["say", "dispatch"]
    text: str | None = None


DISPATCH = Reply("dispatch", None)


class DirectRequestError(ValueError):
    pass


def _find_key(obj: object, keys: tuple[str, ...]) -> str | None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys:
                return key
            found = _find_key(value, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key(item, keys)
            if found:
                return found
    return None


def ensure_no_tools(request: dict) -> dict:
    bad = _find_key(request, FORBIDDEN_REQUEST_KEYS)
    if bad:
        raise DirectRequestError(f"direct reply must not carry {bad}")
    return request


def freeze_request(request: dict) -> dict:
    """构造后冻结：先深度查禁用键，再序列化复核，最后用序列化结果重建。

    返回的是一个新对象，调用方之后改动原 dict 不会影响已冻结的请求。
    """
    ensure_no_tools(request)
    text = json.dumps(request, ensure_ascii=False, sort_keys=True)
    for key in FORBIDDEN_REQUEST_KEYS:
        if f'"{key}"' in text:
            raise DirectRequestError(f"direct reply must not carry {key}")
    return json.loads(text)


def build_direct_request(model: str, messages: list[dict], max_tokens: int = 800) -> dict:
    return freeze_request({"model": model, "messages": messages, "max_tokens": max_tokens})


def _has_tool_marker(obj: object) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in FORBIDDEN_RESPONSE_KEYS:
                return True
            if key == "content" and isinstance(value, list):
                for block in value:
                    if isinstance(block, dict) and block.get("type") in TOOL_BLOCK_TYPES:
                        return True
            if _has_tool_marker(value):
                return True
    elif isinstance(obj, list):
        return any(_has_tool_marker(item) for item in obj)
    return False


def parse_direct_response(raw: object) -> Reply:
    if not isinstance(raw, dict):
        return DISPATCH

    if raw.get("stop_reason") in ("tool_use", "tool_calls"):
        return DISPATCH
    if _has_tool_marker(raw):
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
        payload = [{"role": "system", "content": DIRECT_SYSTEM_PROMPT}, *messages]
        request = build_direct_request(self._model, payload)
        raw = self._llm.complete(request)
        return parse_direct_response(raw)
