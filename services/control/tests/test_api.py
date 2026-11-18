import base64

import pytest
from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app
from skein.outbox import EmitResult

TOKEN = "test-token"


@pytest.fixture()
def client(tmp_path):
    conn = db.connect(tmp_path / "api.db")
    db.migrate(conn)
    calls = []

    def writer(kind, object_, payload):
        calls.append((kind, object_))
        return {"ok": True}

    app = create_app(conn, str(tmp_path / "blobs"), TOKEN, writer)
    with TestClient(app) as c:
        c.calls = calls
        yield c
    conn.close()


def auth(token=TOKEN):
    return {"X-Skein-Token": token}


def test_health_needs_no_auth(client):
    assert client.get("/health/live").json() == {"status": "ok"}


def test_turn_requires_token(client):
    response = client.post("/internal/worker/turn", json={"session_id": "s", "turn_index": 0, "data": ""})
    assert response.status_code == 401


def test_turn_upload_and_conflict(client):
    body = {"session_id": "s1", "turn_index": 0, "data": base64.b64encode(b"one").decode()}
    first = client.post("/internal/worker/turn", json=body, headers=auth())
    assert first.status_code == 200
    again = client.post("/internal/worker/turn", json=body, headers=auth())
    assert again.status_code == 200
    other = dict(body, data=base64.b64encode(b"two").decode())
    assert client.post("/internal/worker/turn", json=other, headers=auth()).status_code == 409


def test_turn_rejects_bad_base64(client):
    body = {"session_id": "s2", "turn_index": 0, "data": "***"}
    assert client.post("/internal/worker/turn", json=body, headers=auth()).status_code == 400


def test_emit_is_single_write(client):
    body = {"key": "k1", "kind": "post", "object": "day:1", "payload": {}}
    first = client.post("/internal/outbox/emit", json=body, headers=auth())
    assert first.status_code == 200
    assert first.json()["result"] == EmitResult.SENT
    second = client.post("/internal/outbox/emit", json=body, headers=auth())
    assert second.json()["result"] == EmitResult.ALREADY
    assert len(client.calls) == 1
