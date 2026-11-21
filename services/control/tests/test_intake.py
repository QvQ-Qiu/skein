from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app

TOKEN = "intake-token"


def make_client(tmp_path):
    conn = db.connect(tmp_path / "intake.db")
    db.migrate(conn)
    app = create_app(conn, str(tmp_path / "b"), TOKEN, lambda *a: {})
    return TestClient(app), conn


def test_intake_creates_and_dedups(tmp_path):
    client, conn = make_client(tmp_path)
    body = {
        "source": "forum_post",
        "thread_id": "42",
        "message_id": "42",
        "requester": "forum:278",
        "title": "登录坏了",
        "summary": "点登录没反应",
        "category": "bug",
        "severity": "high",
    }
    with client:
        first = client.post(
            "/internal/intake", json=body, headers={"X-Skein-Token": TOKEN}
        )
        second = client.post(
            "/internal/intake", json=body, headers={"X-Skein-Token": TOKEN}
        )
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["public_status"] == "received"
    assert second.json()["created"] is False
    assert second.json()["ticket_id"] == first.json()["ticket_id"]
    row = conn.execute(
        "SELECT origin, origin_id, requester, public_status, status FROM tickets"
    ).fetchone()
    assert row["origin"] == "forum_post"
    assert row["origin_id"] == "42"
    assert row["requester"] == "forum:278"
    assert row["public_status"] == "received"
    assert row["status"] == "staged"
    conn.close()


def test_review_moves_public_status(tmp_path):
    client, conn = make_client(tmp_path)
    with client:
        created = client.post(
            "/internal/intake",
            json={
                "source": "forum_comment",
                "thread_id": "42",
                "message_id": "7",
                "requester": "forum:278",
                "title": "图片打不开",
                "summary": "上传后裂图",
            },
            headers={"X-Skein-Token": TOKEN},
        ).json()
        review = client.post(
            f"/internal/tickets/{created['ticket_id']}/review",
            json={"decision": "approve"},
            headers={"X-Skein-Token": TOKEN},
        )
    assert review.status_code == 200
    row = conn.execute(
        "SELECT status, public_status FROM tickets WHERE id = ?",
        (created["ticket_id"],),
    ).fetchone()
    assert row["status"] == "done"
    assert row["public_status"] == "fixed"
    conn.close()
