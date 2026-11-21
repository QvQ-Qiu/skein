import time

import pytest
from fastapi.testclient import TestClient

from skein import db, store
from skein.api import create_app

TOKEN = "chan-token"


@pytest.fixture()
def app_client(tmp_path):
    conn = db.connect(tmp_path / "chan.db")
    db.migrate(conn)
    app = create_app(
        conn,
        str(tmp_path / "blobs"),
        TOKEN,
        lambda *a: {},
        platform_repos={"forum": "/srv/repo"},
    )
    with TestClient(app) as client:
        client.conn = conn
        yield client
    conn.close()


def make_ticket(conn, code="SK-1"):
    t = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES (?, ?, '求助', 'staged', ?, ?)",
        (code, code, t, t),
    )
    return code


def register(ws, token=TOKEN, node_id="n1"):
    ws.send_json(
        {
            "t": "node.register",
            "node_id": node_id,
            "token": token,
            "name": "node-1",
            "platforms": ["forum"],
            "max_workers": 2,
            "peer_url": "ws://127.0.0.1:9101/peer",
        }
    )
    return ws.receive_json()


def test_register_rejects_bad_token(app_client):
    with app_client.websocket_connect("/api/agent/channel") as ws:
        reply = register(ws, token="wrong")
        assert reply["t"] == "error"


def test_register_marks_node_online(app_client):
    with app_client.websocket_connect("/api/agent/channel") as ws:
        reply = register(ws)
        assert reply == {"t": "node.registered", "node_id": "n1"}
        row = app_client.conn.execute("SELECT status, max_workers FROM nodes").fetchone()
        assert tuple(row) == ("online", 2)


def test_dispatch_roundtrip(app_client):
    ticket = make_ticket(app_client.conn)
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        response = app_client.post(
            f"/internal/tickets/{ticket}/dispatch?platform=forum",
            headers={"X-Skein-Token": TOKEN},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True

        create = ws.receive_json()
        assert create["t"] == "worker.create"
        assert create["repo"] == "/srv/repo"

        ws.send_json(
            {"t": "worker.created", "worker_id": body["worker_id"], "session_id": "sess-1"}
        )
        dispatch_msg = ws.receive_json()
        assert dispatch_msg["t"] == "dispatch"
        assert dispatch_msg["ticket"]["code"] == ticket

        ws.send_json(
            {"t": "result", "worker_id": body["worker_id"], "ticket_id": ticket, "result": {"ok": True}}
        )
        for _ in range(50):
            status = app_client.conn.execute(
                "SELECT status FROM tickets WHERE id = ?", (ticket,)
            ).fetchone()[0]
            if status == "review":
                break
            time.sleep(0.02)
        assert status == "review"
        state = app_client.conn.execute("SELECT state FROM dispatches").fetchone()[0]
        assert state == "done"


def wait_for(check, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    return check()


def dispatch_post(app_client, ticket):
    return app_client.post(
        f"/internal/tickets/{ticket}/dispatch?platform=forum",
        headers={"X-Skein-Token": TOKEN},
    )


def test_dispatch_marks_ticket_assigned_and_rejects_duplicate(app_client):
    ticket = make_ticket(app_client.conn, code="SK-D1")
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        body = dispatch_post(app_client, ticket).json()
        assert body["ok"] is True
        ws.receive_json()  # worker.create
        status = app_client.conn.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket,)
        ).fetchone()[0]
        assert status == "assigned"
        worker_id = app_client.conn.execute(
            "SELECT worker_id FROM dispatches WHERE id = ?", (body["dispatch_id"],)
        ).fetchone()[0]
        assert worker_id == body["worker_id"]

        again = dispatch_post(app_client, ticket).json()
        assert again["reason"] == "already_active"
        assert again["dispatch_id"] == body["dispatch_id"]


def test_dispatch_unknown_ticket(app_client):
    response = dispatch_post(app_client, "no-such-ticket")
    assert response.json() == {"ok": False, "reason": "ticket_not_found"}


def test_worker_create_failed_requeues_ticket(app_client):
    conn = app_client.conn
    ticket = make_ticket(conn, code="SK-D2")
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        body = dispatch_post(app_client, ticket).json()
        ws.receive_json()  # worker.create
        ws.send_json(
            {"t": "worker.create_failed", "worker_id": body["worker_id"], "reason": "仓库不存在"}
        )
        assert wait_for(
            lambda: conn.execute(
                "SELECT state FROM dispatches WHERE id = ?", (body["dispatch_id"],)
            ).fetchone()[0]
            == "failed"
        )
        status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
        assert status == "staged"


def test_nack_marks_dispatch_failed_and_requeues(app_client):
    conn = app_client.conn
    ticket = make_ticket(conn, code="SK-D3")
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        body = dispatch_post(app_client, ticket).json()
        ws.receive_json()  # worker.create
        ws.send_json({"t": "worker.created", "worker_id": body["worker_id"], "session_id": "s"})
        assert ws.receive_json()["t"] == "dispatch"
        ws.send_json(
            {"t": "nack", "ticket_id": ticket, "worker_id": body["worker_id"], "reason": "busy"}
        )
        state = wait_for(
            lambda: conn.execute(
                "SELECT state FROM dispatches WHERE id = ?", (body["dispatch_id"],)
            ).fetchone()[0]
            == "failed"
        )
        assert state
        assert conn.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket,)
        ).fetchone()[0] == "staged"


def test_worker_created_recovers_dispatch_after_control_plane_restart(app_client):
    """_pending 只在内存里；重启后按 worker_id 从库里恢复并补发 dispatch。"""
    conn = app_client.conn
    ticket = make_ticket(conn, code="SK-D4")
    conn.execute("UPDATE tickets SET status = 'assigned' WHERE id = ?", (ticket,))
    conn.execute(
        "INSERT INTO dispatches (id, ticket_id, node_id, worker_id, state, updated_at)"
        " VALUES ('deadbeef00000000000000000000000', ?, 'n1', 'w-deadbeef', 'pending', ?)",
        (ticket, time.time()),
    )
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        ws.send_json({"t": "worker.created", "worker_id": "w-deadbeef", "session_id": "s"})
        message = ws.receive_json()
        assert message["t"] == "dispatch"
        assert message["worker_id"] == "w-deadbeef"
        assert message["ticket"]["code"] == ticket
        state = wait_for(
            lambda: conn.execute(
                "SELECT state FROM dispatches WHERE worker_id = 'w-deadbeef'"
            ).fetchone()[0]
        )
        assert state == "dispatched"


def test_dispatch_without_node(app_client):
    ticket = make_ticket(app_client.conn, code="SK-2")
    response = app_client.post(
        f"/internal/tickets/{ticket}/dispatch", headers={"X-Skein-Token": TOKEN}
    )
    assert response.json() == {"ok": False, "reason": "no_node"}


def test_review_approve_and_reject(app_client):
    ticket = make_ticket(app_client.conn, code="SK-3")
    ok = app_client.post(
        f"/internal/tickets/{ticket}/review",
        json={"decision": "approve"},
        headers={"X-Skein-Token": TOKEN},
    )
    assert ok.json()["status"] == "done"
    bad = app_client.post(
        f"/internal/tickets/{ticket}/review",
        json={"decision": "maybe"},
        headers={"X-Skein-Token": TOKEN},
    )
    assert bad.status_code == 400


def test_followup_pushes_to_subscribed_worker(app_client):
    conn = app_client.conn
    ticket = make_ticket(conn, code="SK-F1")
    conn.execute(
        "INSERT INTO threads (id, origin, origin_id, created_at) VALUES ('th-f1', 'forum_post', 'p1', 0)"
    )
    store.subscribe(conn, "th-f1", ticket)
    conn.execute(
        "INSERT INTO dispatches"
        " (id, ticket_id, node_id, worker_id, state, attempt, updated_at)"
        " VALUES ('d-f1', ?, 'n1', 'w-f1', 'dispatched', 1, 1)",
        (ticket,),
    )
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        response = app_client.post(
            "/internal/events/followup",
            json={"thread_id": "th-f1", "text": "补充一下截图"},
            headers={"X-Skein-Token": TOKEN},
        )
        assert response.json() == {"forwarded": True}
        message = ws.receive_json()
        assert message["t"] == "dispatch"
        assert message["followup"] is True
        assert message["worker_id"] == "w-f1"
        assert message["ticket"]["title"] == "补充一下截图"


def test_cancel_sends_to_node_and_requeues_ticket(app_client):
    conn = app_client.conn
    ticket = make_ticket(conn, code="SK-C1")
    conn.execute("UPDATE tickets SET status = 'assigned' WHERE id = ?", (ticket,))
    conn.execute(
        "INSERT INTO dispatches"
        " (id, ticket_id, node_id, worker_id, state, attempt, updated_at)"
        " VALUES ('d-c1', ?, 'n1', 'w-c1', 'dispatched', 1, 1)",
        (ticket,),
    )
    with app_client.websocket_connect("/api/agent/channel") as ws:
        register(ws)
        response = app_client.post(
            f"/internal/tickets/{ticket}/cancel",
            headers={"X-Skein-Token": TOKEN},
        )
        assert response.json() == {"cancelled": True}
        message = ws.receive_json()
        assert message == {"t": "cancel", "ticket_id": ticket, "worker_id": "w-c1"}
    state = conn.execute("SELECT state FROM dispatches WHERE id = 'd-c1'").fetchone()[0]
    status = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket,)).fetchone()[0]
    assert state == "failed"
    assert status == "staged"


def test_cancel_without_live_dispatch_conflicts(app_client):
    ticket = make_ticket(app_client.conn, code="SK-C2")
    response = app_client.post(
        f"/internal/tickets/{ticket}/cancel",
        headers={"X-Skein-Token": TOKEN},
    )
    assert response.status_code == 409
