import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app

TOKEN = "ctl-token"
INTERNAL = "internal-token"


class Forum(BaseHTTPRequestHandler):
    calls: ClassVar[list] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).calls.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if self.path == "/internal/featured":
            self.wfile.write(json.dumps({"code": 200, "data": {"marked": len(body["post_ids"])}}).encode())
        else:
            self.wfile.write(json.dumps({"code": 200, "data": {"postID": 900}}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture()
def forum_base():
    Forum.calls = []
    server = HTTPServer(("127.0.0.1", 0), Forum)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture()
def client(tmp_path, forum_base):
    conn = db.connect(tmp_path / "compat.db")
    db.migrate(conn)
    app = create_app(
        conn,
        str(tmp_path / "blobs"),
        TOKEN,
        lambda *a: {},
        forum_base=forum_base,
        internal_token=INTERNAL,
    )
    with TestClient(app) as c:
        c.conn = conn
        yield c
    conn.close()


def test_featured_forwards_and_is_idempotent(client):
    body = {"post_ids": [11, 12]}
    first = client.post("/internal/featured", json=body, headers={"X-Internal-Token": INTERNAL})
    assert first.json() == {"code": 200, "data": {"marked": 2}}
    assert len(Forum.calls) == 2
    assert Forum.calls[0][0] == "/internal/featured"
    assert Forum.calls[0][1].get("X-Internal-Token") == INTERNAL

    second = client.post("/internal/featured", json=body, headers={"X-Internal-Token": INTERNAL})
    assert second.json() == {"code": 200, "data": {"marked": 2}}
    assert len(Forum.calls) == 2


def test_featured_rejects_bad_token(client):
    response = client.post("/internal/featured", json={"post_ids": [1]}, headers={"X-Internal-Token": "no"})
    assert response.status_code == 403
    assert Forum.calls == []


def test_post_forwards_authorization_and_dedups(client):
    body = {"title": "日报", "content": "正文", "partition": "tech", "isPrivate": False, "userTelephone": "138"}
    headers = {"Authorization": "Bearer user-token"}
    first = client.post("/api/auth/post", json=body, headers=headers)
    assert first.json()["code"] == 200
    assert len(Forum.calls) == 1
    assert Forum.calls[0][1].get("Authorization") == "Bearer user-token"

    second = client.post("/api/auth/post", json=body, headers=headers)
    assert second.json()["code"] == 200
    assert len(Forum.calls) == 1
