import time

import pytest
from fastapi.testclient import TestClient

from skein import db
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
