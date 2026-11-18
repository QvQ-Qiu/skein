from skein.router.direct import (
    DISPATCH,
    DirectRequestError,
    DirectRouter,
    Reply,
    build_direct_request,
    ensure_no_tools,
    parse_direct_response,
)


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return self.response


def test_request_never_carries_tools():
    request = build_direct_request("m", [{"role": "user", "content": "hi"}])
    assert "tools" not in request
    assert "tool_choice" not in request
    assert "functions" not in request


def test_request_rejects_tools_when_forced():
    try:
        ensure_no_tools({"model": "m", "messages": [], "tools": []})
    except DirectRequestError:
        return
    raise AssertionError("guard should reject tools")


def test_router_never_sends_tools():
    llm = FakeLLM({"action": "say", "text": "你好"})
    DirectRouter(llm, "m").reply([{"role": "user", "content": "hi"}])
    assert "tools" not in llm.requests[0]


def test_say_is_returned():
    assert parse_direct_response({"action": "say", "text": " 你好 "}) == Reply("say", "你好")


def test_dispatch_is_returned():
    assert parse_direct_response({"action": "dispatch"}) == DISPATCH


def test_tool_call_forces_dispatch():
    assert parse_direct_response({"action": "say", "text": "好", "tool_calls": []}) == DISPATCH
    assert parse_direct_response({"tool_use": {"name": "bash"}}) == DISPATCH


def test_malformed_forces_dispatch():
    assert parse_direct_response("not a dict") == DISPATCH
    assert parse_direct_response({}) == DISPATCH
    assert parse_direct_response({"action": "run"}) == DISPATCH


def test_empty_say_forces_dispatch():
    assert parse_direct_response({"action": "say", "text": "   "}) == DISPATCH
    assert parse_direct_response({"action": "say"}) == DISPATCH


def test_router_end_to_end_dispatch():
    llm = FakeLLM({"action": "say", "text": "我来", "tool_calls": [{"name": "bash"}]})
    assert DirectRouter(llm, "m").reply([{"role": "user", "content": "修个 bug"}]) == DISPATCH
