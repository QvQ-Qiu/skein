"""事件入口：Router 只做分派，工具集被写死在代码里。"""

from __future__ import annotations

import json
from dataclasses import dataclass

ROUTE_TOOLS = ("route", "attach", "ignore", "stop", "note")

ROUTE_SYSTEM_PROMPT = (
    "你是事件入口。只输出 JSON，action 取 route / attach / ignore / stop / note 之一。"
    "需要建单时用 route；已经在办的同一件事用 attach；不值得处理用 ignore。"
    "你不能执行任务，只能做决定。"
)

FORBIDDEN_TOOLS = ("bash", "read", "write", "sql", "post", "comment", "edit")

BATCH_ROUTE_PROMPT = (
    "你会收到一批环境事件，每条带 index。只输出 JSON："
    '{"decisions":[{"index":0,"action":"route","note":"..."}]}。'
    "每条事件必须有且只有一个 decision，action 只能是 route / attach / ignore / stop / note。"
    "route=建单；attach=并入已有；ignore=不处理；stop=叫停；note=只记一笔。"
    "你不能执行任务，只能做决定。"
)


@dataclass(frozen=True)
class RouteDecision:
    action: str
    ticket_id: str | None = None
    thread_id: str | None = None
    note: str | None = None


class RouteRequestError(ValueError):
    pass


def _tool_name(tool: object) -> str | None:
    if isinstance(tool, str):
        return tool
    if isinstance(tool, dict):
        name = tool.get("name")
        if isinstance(name, str):
            return name
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
    return None


def build_route_request(model: str, messages: list[dict], max_tokens: int = 1500) -> dict:
    request = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "tools": list(ROUTE_TOOLS),
    }
    for tool in request["tools"]:
        name = _tool_name(tool)
        if name in FORBIDDEN_TOOLS:
            raise RouteRequestError(f"route tools must not include {name}")
    return request


def build_batch_route_request(
    model: str,
    events: list[dict],
    history: list[dict] | None = None,
    max_tokens: int = 2000,
) -> dict:
    messages = [{"role": "system", "content": BATCH_ROUTE_PROMPT}]
    messages.extend(history or [])
    messages.append(
        {
            "role": "user",
            "content": json.dumps({"events": events}, ensure_ascii=False),
        }
    )
    request = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "tools": list(ROUTE_TOOLS),
    }
    for tool in request["tools"]:
        name = _tool_name(tool)
        if name in FORBIDDEN_TOOLS:
            raise RouteRequestError(f"route tools must not include {name}")
    return request


def parse_batch_route_response(raw: object, count: int) -> list[RouteDecision]:
    decisions: list = []
    if isinstance(raw, dict) and isinstance(raw.get("decisions"), list):
        decisions = raw["decisions"]
    elif isinstance(raw, list):
        decisions = raw

    by_index: dict[int, RouteDecision] = {}
    for item in decisions:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count:
            continue
        action = item.get("action")
        if action not in ROUTE_TOOLS:
            by_index[index] = RouteDecision("ignore", note=f"unknown_action:{action}")
            continue
        by_index[index] = RouteDecision(
            action=action,
            ticket_id=item.get("ticket_id"),
            thread_id=item.get("thread_id"),
            note=item.get("note"),
        )
    return [
        by_index.get(index, RouteDecision("ignore", note="missing_decision"))
        for index in range(count)
    ]


def parse_route_response(raw: object) -> RouteDecision:
    if not isinstance(raw, dict):
        return RouteDecision("ignore", note="invalid_response")
    action = raw.get("action")
    if action not in ROUTE_TOOLS:
        return RouteDecision("ignore", note=f"unknown_action:{action}")
    return RouteDecision(
        action=action,
        ticket_id=raw.get("ticket_id"),
        thread_id=raw.get("thread_id"),
        note=raw.get("note"),
    )
