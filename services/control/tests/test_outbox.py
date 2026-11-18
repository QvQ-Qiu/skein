import pytest

from skein import db
from skein.outbox import EffectState, EmitResult, Outbox, get, pending


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "outbox.db")
    db.migrate(c)
    yield c
    c.close()


class Writer:
    def __init__(self, response=None, fail=False):
        self.calls = []
        self.response = response if response is not None else {"post_id": 1}
        self.fail = fail

    def __call__(self, kind, object_, payload):
        self.calls.append((kind, object_, payload))
        if self.fail:
            raise RuntimeError("upstream down")
        return self.response


def test_emit_is_single_use(conn):
    writer = Writer()
    ob = Outbox(conn, writer)
    assert ob.emit("k1", "post", "day:2026-11-18", {"t": "x"}) == EmitResult.SENT
    assert ob.emit("k1", "post", "day:2026-11-18", {"t": "x"}) == EmitResult.ALREADY
    assert len(writer.calls) == 1
    assert get(conn, "k1").state == EffectState.CONFIRMED


def test_failure_is_recorded_and_not_retried(conn):
    writer = Writer(fail=True)
    ob = Outbox(conn, writer)
    with pytest.raises(RuntimeError):
        ob.emit("k2", "comment", "c:1", {})
    assert get(conn, "k2").state == EffectState.FAILED
    assert ob.emit("k2", "comment", "c:1", {}) == EmitResult.FAILED
    assert len(writer.calls) == 1


def test_pending_lists_started(conn):
    ob = Outbox(conn, Writer())
    ob.emit("k3", "post", "o3", {})
    conn.execute("UPDATE effects SET state = 'started' WHERE key = 'k3'")
    assert [e.key for e in pending(conn)] == ["k3"]


def test_reconcile_confirms_when_object_exists(conn):
    ob = Outbox(conn, Writer())
    ob.emit("k4", "post", "day:2026-11-19", {})
    conn.execute("UPDATE effects SET state = 'started' WHERE key = 'k4'")
    result = ob.reconcile("k4", lambda kind, obj: {"post_id": 99})
    assert result == EmitResult.ALREADY
    assert get(conn, "k4").state == EffectState.CONFIRMED


def test_reconcile_fails_when_object_missing(conn):
    ob = Outbox(conn, Writer())
    ob.emit("k5", "post", "day:2026-11-20", {})
    conn.execute("UPDATE effects SET state = 'started' WHERE key = 'k5'")
    result = ob.reconcile("k5", lambda kind, obj: None)
    assert result == EmitResult.FAILED
    assert get(conn, "k5").state == EffectState.FAILED
