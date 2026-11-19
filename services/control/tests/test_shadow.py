import pytest

from skein import db
from skein.admission import Event
from skein.shadow import ShadowDispatcher


class FakeLLM:
    def __init__(self, response=None, error=None):
        self.response = response or {"action": "route"}
        self.error = error
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "shadow.db")
    db.migrate(c)
    yield c
    c.close()


def event(origin_id="c1"):
    return Event(source="forum_comment", origin_id=origin_id, text="帮忙定位", mentioned=True)


def test_shadow_records_decision_without_side_effects(conn):
    llm = FakeLLM({"action": "route"})
    action = ShadowDispatcher(conn, llm, "route")(event())
    assert action == "route"
    rows = conn.execute("SELECT source, origin_id, action FROM shadow_decisions").fetchall()
    assert [tuple(r) for r in rows] == [("forum_comment", "c1", "route")]
    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert "tools" in llm.requests[0]


def test_shadow_records_ignore(conn):
    action = ShadowDispatcher(conn, FakeLLM({"action": "ignore"}), "route")(event("c2"))
    assert action == "ignore"
    assert conn.execute("SELECT action FROM shadow_decisions").fetchone()[0] == "ignore"


def test_shadow_propagates_model_error(conn):
    with pytest.raises(RuntimeError):
        ShadowDispatcher(conn, FakeLLM(error=RuntimeError("boom")), "route")(event("c3"))
    assert conn.execute("SELECT COUNT(*) FROM shadow_decisions").fetchone()[0] == 0


def test_shadow_accepts_ingestor_thread_argument(conn):
    action = ShadowDispatcher(conn, FakeLLM({"action": "route"}), "route")(
        event("c4"), "thread-1"
    )
    assert action == "route"
    assert conn.execute(
        "SELECT action FROM shadow_decisions WHERE origin_id = 'c4'"
    ).fetchone()[0] == "route"
