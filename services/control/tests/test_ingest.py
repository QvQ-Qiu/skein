import pytest

from skein import db
from skein.ingest import Ingestor, RawItem


class FakeSource:
    name = "forum"

    def __init__(self, items):
        self.items = items
        self.calls = []

    def fetch(self, since, limit):
        self.calls.append(since)
        return list(self.items)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "ingest.db")
    db.migrate(c)
    yield c
    c.close()


def items():
    return [
        RawItem(source="forum_post", origin_id="p1", text="求助：切换会话白屏", created_at=100.0),
        RawItem(
            source="forum_comment",
            origin_id="c1",
            parent_id="p1",
            text="同问",
            created_at=101.0,
        ),
        RawItem(
            source="forum_comment",
            origin_id="c2",
            parent_id="p1",
            text="@小秋 帮忙看下",
            created_at=102.0,
            mentioned=True,
        ),
    ]


def test_poll_writes_events_and_threads(conn):
    written = Ingestor(conn, FakeSource(items())).poll()
    assert written == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 3
    threads = conn.execute("SELECT origin_id FROM threads ORDER BY origin_id").fetchall()
    assert [t["origin_id"] for t in threads] == ["p1"]


def test_second_poll_is_idempotent(conn):
    source = FakeSource(items())
    ingestor = Ingestor(conn, source)
    ingestor.poll()
    assert ingestor.poll() == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 3
    assert source.calls[0] is None
    assert source.calls[1] == 102.0


def test_comment_attaches_to_post_thread(conn):
    Ingestor(conn, FakeSource(items())).poll()
    rows = conn.execute(
        "SELECT e.origin_id, t.origin_id AS thread FROM events e JOIN threads t ON t.id = e.thread_id"
        " ORDER BY e.origin_id"
    ).fetchall()
    assert [(r["origin_id"], r["thread"]) for r in rows] == [
        ("c1", "p1"),
        ("c2", "p1"),
        ("p1", "p1"),
    ]


def test_mentions_are_marked_for_processing(conn):
    Ingestor(conn, FakeSource(items())).poll()
    verdicts = {
        r["origin_id"]: r["verdict"]
        for r in conn.execute("SELECT origin_id, verdict FROM events")
    }
    assert verdicts == {"p1": "ignore", "c1": "ignore", "c2": "process"}


def test_watermark_advances(conn):
    Ingestor(conn, FakeSource(items())).poll()
    row = conn.execute("SELECT value FROM kv WHERE key = 'wm:forum'").fetchone()
    assert row["value"] == "102.0"


def test_handler_called_only_for_process_events(conn):
    seen = []
    Ingestor(conn, FakeSource(items()), on_process=lambda e: seen.append(e.origin_id)).poll()
    assert seen == ["c2"]


def test_handler_error_does_not_stop_ingestion(conn):
    def boom(event):
        raise RuntimeError("model down")

    written = Ingestor(conn, FakeSource(items()), on_process=boom).poll()
    assert written == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == 3
    row = conn.execute("SELECT action FROM audit WHERE action = 'handler_error'").fetchone()
    assert row is not None
