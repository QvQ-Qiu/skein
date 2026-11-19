import pytest

from skein import db, store
from skein.admission import Event
from skein.host import RouterHost
from skein.router.runtime import RouterRuntime
from skein.router.session import RouterSession


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if not self.responses:
            raise RuntimeError("no response")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "router.db")
    db.migrate(c)
    yield c
    c.close()


def make_triggers(conn, count: int):
    items = []
    for index in range(count):
        thread_id = store.ensure_thread(conn, "forum_post", f"p{index}")
        items.append(
            (
                Event(
                    source="forum_comment",
                    origin_id=f"c{index}",
                    text=f"问题 {index}",
                    mentioned=True,
                ),
                thread_id,
            )
        )
    return items


def test_batch_uses_one_model_call_for_three_events(conn):
    llm = FakeLLM(
        {
            "decisions": [
                {"index": 0, "action": "route"},
                {"index": 1, "action": "ignore"},
                {"index": 2, "action": "route"},
            ]
        }
    )
    host = RouterHost(conn, llm, "direct", "route")
    results = host.dispatch_batch(make_triggers(conn, 3))

    assert [result.action for result in results] == ["dispatch", "ignore", "dispatch"]
    assert len(llm.requests) == 1
    content = llm.requests[0]["messages"][-1]["content"]
    assert "问题 0" in content and "问题 2" in content
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2


def test_batch_missing_decision_is_ignore(conn):
    llm = FakeLLM({"decisions": [{"index": 0, "action": "route"}]})
    host = RouterHost(conn, llm, "direct", "route")
    results = host.dispatch_batch(make_triggers(conn, 2))
    assert [result.action for result in results] == ["dispatch", "ignore"]


def test_batch_falls_back_to_single_calls_on_model_error(conn):
    llm = FakeLLM(
        RuntimeError("boom"),
        {"action": "ignore"},
        {"action": "ignore"},
    )
    host = RouterHost(conn, llm, "direct", "route")
    results = host.dispatch_batch(make_triggers(conn, 2))
    assert [result.action for result in results] == ["ignore", "ignore"]
    assert len(llm.requests) == 3


def test_runtime_step_drains_batch_and_keeps_remainder(conn):
    llm = FakeLLM(
        {"decisions": [{"index": 0, "action": "route"}, {"index": 1, "action": "ignore"}]},
        {"decisions": [{"index": 0, "action": "route"}]},
    )
    host = RouterHost(conn, llm, "direct", "route")
    session = RouterSession()
    runtime = RouterRuntime(host, session, batch_size=2)
    triggers = make_triggers(conn, 3)
    for event, thread_id in triggers:
        runtime.submit(event, thread_id)

    assert runtime.step() == ["dispatch", "ignore"]
    assert session.pending_count == 1
    assert runtime.step() == ["dispatch"]
    assert session.pending_count == 0
    assert len(llm.requests) == 2
