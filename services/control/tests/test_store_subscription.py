import time

import pytest

from skein import db, store


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "store.db")
    db.migrate(c)
    yield c
    c.close()


def add_ticket(conn, ticket_id="tk1", thread_id="th1"):
    now = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, thread_id, title, status, created_at, updated_at)"
        " VALUES (?, ?, ?, '求助', 'review', ?, ?)",
        (ticket_id, ticket_id, thread_id, now, now),
    )


def test_active_subscription_returns_live_worker(conn):
    conn.execute(
        "INSERT INTO threads (id, origin, origin_id, created_at) VALUES ('th1', 'forum_post', 'p1', 0)"
    )
    add_ticket(conn)
    store.subscribe(conn, "th1", "tk1")
    conn.execute(
        "INSERT INTO dispatches"
        " (id, ticket_id, node_id, worker_id, state, attempt, updated_at)"
        " VALUES ('d1', 'tk1', 'n1', 'w1', 'dispatched', 1, 1)"
    )
    row = store.active_subscription(conn, "th1")
    assert row == {"ticket_id": "tk1", "code": "tk1", "worker_id": "w1", "node_id": "n1"}


def test_active_subscription_none_without_dispatch(conn):
    conn.execute(
        "INSERT INTO threads (id, origin, origin_id, created_at) VALUES ('th1', 'forum_post', 'p1', 0)"
    )
    add_ticket(conn)
    store.subscribe(conn, "th1", "tk1")
    assert store.active_subscription(conn, "th1") is None
