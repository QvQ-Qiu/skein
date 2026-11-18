import pytest

from skein import db
from skein.admission import Event
from skein.host import RouterHost
from skein.router.direct import DISPATCH_RESULT_TEXT


class FakeLLM:
    def __init__(self, direct=None, route=None):
        self.direct = direct if direct is not None else {"action": "say", "text": "你好"}
        self.route = route if route is not None else {"action": "route"}
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return self.route if "tools" in request else self.direct


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "host.db")
    db.migrate(c)
    yield c
    c.close()


def host(conn, llm):
    return RouterHost(conn, llm, direct_model="direct", route_model="route")


def ticket_count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"]


def test_chat_say_writes_no_ticket(conn):
    llm = FakeLLM(direct={"action": "say", "text": "你好"})
    result = host(conn, llm).chat([{"role": "user", "content": "在吗"}])
    assert result.action == "say"
    assert result.text == "你好"
    assert result.ticket_id is None
    assert ticket_count(conn) == 0
    assert "tools" not in llm.requests[0]


def test_chat_dispatch_creates_ticket(conn):
    llm = FakeLLM(direct={"action": "dispatch"})
    result = host(conn, llm).chat([{"role": "user", "content": "帮我修个 bug"}])
    assert result.action == "dispatch"
    assert result.text == DISPATCH_RESULT_TEXT
    assert result.ticket_id is not None
    assert ticket_count(conn) == 1
    row = conn.execute("SELECT status, title FROM tickets").fetchone()
    assert row["status"] == "staged"
    assert row["title"] == "帮我修个 bug"


def test_tool_call_in_chat_becomes_dispatch(conn):
    llm = FakeLLM(direct={"action": "say", "text": "我来", "tool_calls": [{"name": "bash"}]})
    result = host(conn, llm).chat([{"role": "user", "content": "看看这个"}])
    assert result.action == "dispatch"
    assert result.text == DISPATCH_RESULT_TEXT


def test_event_mention_creates_ticket_and_subscription(conn):
    llm = FakeLLM(route={"action": "route"})
    event = Event(
        source="forum_comment", origin_id="c1", text="帮忙定位一下", mentioned=True, actor="u1"
    )
    result = host(conn, llm).on_event(event)
    assert result.action == "dispatch"
    assert result.ticket_id is not None
    assert ticket_count(conn) == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM subscriptions").fetchone()["n"] == 1
    verdict = conn.execute("SELECT verdict FROM events").fetchone()["verdict"]
    assert verdict == "process"
    assert "tools" in llm.requests[0]


def test_event_is_idempotent(conn):
    llm = FakeLLM(route={"action": "route"})
    h = host(conn, llm)
    event = Event(source="forum_comment", origin_id="c2", text="帮忙看看", mentioned=True)
    first = h.on_event(event)
    second = h.on_event(event)
    assert first.action == "dispatch"
    assert second.action == "duplicate"
    assert ticket_count(conn) == 1


def test_event_ignored_writes_no_ticket(conn):
    llm = FakeLLM()
    event = Event(source="forum_comment", origin_id="c3", text="路过看看")
    result = host(conn, llm).on_event(event)
    assert result.action == "ignore"
    assert ticket_count(conn) == 0
    assert llm.requests == []


def test_route_ignore_creates_no_ticket(conn):
    llm = FakeLLM(route={"action": "ignore"})
    event = Event(source="forum_comment", origin_id="c4", text="帮忙看看", mentioned=True)
    result = host(conn, llm).on_event(event)
    assert result.action == "ignore"
    assert ticket_count(conn) == 0


def test_comment_thread_uses_parent_post(conn):
    event = Event(
        source="forum_comment", origin_id="c9", parent_id="p7", text="帮忙", mentioned=True
    )
    host(conn, FakeLLM(route={"action": "route"})).on_event(event)
    row = conn.execute("SELECT origin, origin_id FROM threads").fetchone()
    assert tuple(row) == ("forum_post", "p7")


def test_dedup_key_attaches_instead_of_second_ticket(conn):
    llm = FakeLLM(route={"action": "route"})
    h = host(conn, llm)
    h._conn.execute("DELETE FROM events")  # 模拟事件去重被绕过，只剩工单去重
    h.on_event(Event(source="forum_comment", origin_id="c5", text="同一个问题", mentioned=True))
    h._conn.execute("DELETE FROM events")
    second = h.on_event(
        Event(source="forum_comment", origin_id="c5", text="同一个问题", mentioned=True)
    )
    assert second.action == "attached"
    assert ticket_count(conn) == 1


def test_handle_admitted_does_not_re_record_event(conn):
    from skein import store
    from skein.host import DispatchHandler

    h = host(conn, FakeLLM(route={"action": "route"}))
    thread_id = store.ensure_thread(conn, "forum_post", "p9")
    event = Event(source="forum_comment", origin_id="c9", text="帮忙", mentioned=True, parent_id="p9")
    result = DispatchHandler(h)(event, thread_id)
    assert result.action == "dispatch"
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 1
