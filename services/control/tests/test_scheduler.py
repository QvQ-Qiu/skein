import time

import pytest

from skein import db, scheduler


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "sched.db")
    db.migrate(c)
    yield c
    c.close()


def make_ticket(conn, code="SK-1", platform=None):
    t = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES (?, ?, 'x', 'staged', ?, ?)",
        (code, code, t, t),
    )
    return code


def test_upsert_and_list_online(conn):
    scheduler.upsert_node(conn, "n1", name="node-1", platforms=["forum"], max_workers=2)
    rows = scheduler.online(conn)
    assert [r["id"] for r in rows] == ["n1"]
    assert rows[0]["max_workers"] == 2


def test_stale_node_is_excluded(conn):
    scheduler.upsert_node(conn, "n1")
    conn.execute("UPDATE nodes SET last_seen = ? WHERE id = 'n1'", (time.time() - 120,))
    assert scheduler.online(conn) == []


def test_heartbeat_brings_node_back(conn):
    scheduler.upsert_node(conn, "n1")
    conn.execute("UPDATE nodes SET last_seen = ?, status = 'offline' WHERE id = 'n1'", (0,))
    scheduler.touch(conn, "n1")
    assert [r["id"] for r in scheduler.online(conn)] == ["n1"]


def test_capacity_is_respected(conn):
    scheduler.upsert_node(conn, "n1", max_workers=1)
    ticket = make_ticket(conn)
    assert scheduler.dispatch(conn, ticket) is not None
    assert scheduler.pick_node(conn) is None


def test_platform_filter(conn):
    scheduler.upsert_node(conn, "n1", platforms=["iwiki"])
    assert scheduler.pick_node(conn, platform="forum") is None
    scheduler.upsert_node(conn, "n2", platforms=["forum"])
    assert scheduler.pick_node(conn, platform="forum") == "n2"


def test_wildcard_node_accepts_any_platform(conn):
    scheduler.upsert_node(conn, "n1", platforms=["*"])
    assert scheduler.pick_node(conn, platform="forum") == "n1"


def test_dispatch_sets_ticket_assigned(conn):
    scheduler.upsert_node(conn, "n1")
    ticket = make_ticket(conn)
    dispatch_id = scheduler.dispatch(conn, ticket)
    assert dispatch_id is not None
    row = conn.execute("SELECT ticket_id, node_id, state FROM dispatches").fetchone()
    assert tuple(row) == (ticket, "n1", "pending")
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
    assert status == "assigned"


def test_dispatch_without_node_leaves_ticket_staged(conn):
    ticket = make_ticket(conn)
    assert scheduler.dispatch(conn, ticket) is None
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
    assert status == "staged"


def test_update_dispatch(conn):
    scheduler.upsert_node(conn, "n1")
    ticket = make_ticket(conn)
    dispatch_id = scheduler.dispatch(conn, ticket)
    scheduler.update_dispatch(conn, dispatch_id, state="created", worker_id="w1", detail="ok")
    row = conn.execute("SELECT state, worker_id, detail FROM dispatches").fetchone()
    assert tuple(row) == ("created", "w1", "ok")


def test_fail_dispatch_requeues_ticket(conn):
    scheduler.upsert_node(conn, "n1")
    ticket = make_ticket(conn)
    dispatch_id = scheduler.dispatch(conn, ticket)
    scheduler.update_dispatch(conn, dispatch_id, state="dispatched", worker_id="w1")
    assert scheduler.fail_dispatch(conn, dispatch_id, "boom") == ticket
    row = conn.execute(
        "SELECT state, detail FROM dispatches WHERE id = ?", (dispatch_id,)
    ).fetchone()
    assert tuple(row) == ("failed", "boom")
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
    assert status == "staged"


def test_fail_dispatch_keeps_terminal_state(conn):
    scheduler.upsert_node(conn, "n1")
    ticket = make_ticket(conn)
    dispatch_id = scheduler.dispatch(conn, ticket)
    scheduler.update_dispatch(conn, dispatch_id, state="done", worker_id="w1")
    scheduler.fail_dispatch(conn, dispatch_id, "late")
    state = conn.execute("SELECT state FROM dispatches WHERE id = ?", (dispatch_id,)).fetchone()[0]
    assert state == "done"


def test_reconcile_node_fails_lost_receipt(conn):
    scheduler.upsert_node(conn, "n1")
    ticket = make_ticket(conn)
    dispatch_id = scheduler.dispatch(conn, ticket)
    scheduler.update_dispatch(conn, dispatch_id, state="dispatched", worker_id="w1")
    assert scheduler.reconcile_node(conn, "n1", set()) == [dispatch_id]
    state = conn.execute("SELECT state FROM dispatches WHERE id = ?", (dispatch_id,)).fetchone()[0]
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
    assert (state, status) == ("failed", "staged")


def test_reconcile_node_keeps_busy_worker(conn):
    scheduler.upsert_node(conn, "n1")
    ticket = make_ticket(conn)
    dispatch_id = scheduler.dispatch(conn, ticket)
    scheduler.update_dispatch(conn, dispatch_id, state="dispatched", worker_id="w1")
    assert scheduler.reconcile_node(conn, "n1", {"w1"}) == []
    state = conn.execute("SELECT state FROM dispatches WHERE id = ?", (dispatch_id,)).fetchone()[0]
    assert state == "dispatched"


def test_expire_stale_dispatches_only_old(conn):
    scheduler.upsert_node(conn, "n1")
    old_ticket = make_ticket(conn, code="SK-OLD")
    fresh_ticket = make_ticket(conn, code="SK-FRESH")
    old = scheduler.dispatch(conn, old_ticket)
    fresh = scheduler.dispatch(conn, fresh_ticket)
    conn.execute(
        "UPDATE dispatches SET updated_at = ? WHERE id = ?", (time.time() - 7200, old)
    )
    assert scheduler.expire_stale_dispatches(conn, 3600) == [old]
    states = dict(
        conn.execute("SELECT id, state FROM dispatches").fetchall()
    )
    assert states[old] == "failed"
    assert states[fresh] == "pending"
