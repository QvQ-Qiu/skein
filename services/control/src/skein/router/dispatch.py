"""事件入口：Router 只做分派，工具集被写死在代码里。"""

from __future__ import annotations

from dataclasses import dataclass

from skein.llm import LLMClient

ROUTE_TOOLS = ("route", "attach", "ignore", "stop", "note")

FORBIDDEN_TOOLS = ("bash", "read", "write", "sql", "post", "comment", "edit")


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
