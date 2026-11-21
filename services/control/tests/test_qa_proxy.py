
from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app
from skein.qa_proxy import verify_user_header
from skein.sso import Principal, SsoConfig, SsoRejected

TOKEN = "qa-token"


class FakeResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "application/json"):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._body = body

    async def aiter_raw(self):
        yield self._body

    async def aclose(self):
        return None


class FakeClient:
    """记录上游请求，返回固定响应；替代 httpx.MockTransport（它不支持流式）。"""

    def __init__(self):
        self.requests = []

    def build_request(self, method, url, headers=None, content=None):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": (content or b"").decode(),
            }
        )
        return self.requests[-1]

    async def send(self, request, stream=True):
        if request["url"].endswith("/ops/metrics"):
            return FakeResponse(200, b'{"ops": true}')
        return FakeResponse(200, b'{"ok": true}')


class FakeSso:
    def __init__(self, internal=True, admin=True):
        self.internal = internal
        self.admin = admin

    def password_verify(self, email, password):  # pragma: no cover - 未用
        raise SsoRejected("invalid_credentials", 401)

    def session_verify(self, session):
        if session != "sess-ok":
            raise SsoRejected("invalid_sso", 401)
        return Principal(
            sub="u1", name="张三", internal=self.internal, admin=self.admin
        )

    def session_revoke(self, session):  # pragma: no cover
        return None


def make_app(tmp_path, sso=False, internal=True, admin=True, user_secret=""):
    conn = db.connect(tmp_path / "qa.db")
    db.migrate(conn)
    client = FakeClient()
    kwargs = {}
    if sso:
        kwargs["sso_config"] = SsoConfig(client_id="c", client_secret="s")
        kwargs["sso_client"] = FakeSso(internal=internal, admin=admin)
    app = create_app(
        conn,
        str(tmp_path / "blobs"),
        TOKEN,
        lambda *a: {},
        qa_chat_upstream="http://chat.test",
        qa_ops_upstream="http://ops.test",
        qa_client=client,
        qa_panel_secret="panel-secret-123456",
        qa_panel_sub="sub-1",
        qa_panel_email="ops@example.com",
        qa_user_secret=user_secret,
        **kwargs,
    )
    return app, conn, client


def test_chat_proxies_jwt_and_strips_cookie(tmp_path):
    app, conn, upstream = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/agent/suggestions",
            headers={"Authorization": "Bearer user-token", "Cookie": "unisso_sid=secret"},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    sent = upstream.requests[-1]
    assert sent["url"] == "http://chat.test/api/v1/agent/suggestions"
    assert sent["headers"].get("authorization") == "Bearer user-token"
    assert "cookie" not in {k.lower() for k in sent["headers"]}
    conn.close()


def test_chat_attaches_sso_identity_header(tmp_path):
    app, conn, upstream = make_app(
        tmp_path, sso=True, internal=True, admin=False, user_secret="user-secret-123456"
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/agent/x", headers={"Cookie": "unisso_sid=sess-ok"}
        )
    assert response.status_code == 200
    sent = upstream.requests[-1]["headers"]
    assert "cookie" not in {k.lower() for k in sent}
    raw = sent.get("x-skein-user")
    assert raw
    data = verify_user_header(raw, "user-secret-123456")
    assert data is not None
    assert data["sub"] == "u1"
    assert data["internal"] is True
    assert data["admin"] is False
    conn.close()


def test_chat_strips_client_supplied_identity_header(tmp_path):
    app, conn, upstream = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/agent/x",
            headers={"X-Skein-User": "forged", "Authorization": "Bearer user-token"},
        )
    assert response.status_code == 200
    assert "x-skein-user" not in {
        k.lower() for k in upstream.requests[-1]["headers"]
    }
    conn.close()


def test_chat_forwards_method_body_and_query(tmp_path):
    app, conn, upstream = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/task/sse?topic=x", json={"question": "在吗"})
    assert response.status_code == 200
    sent = upstream.requests[-1]
    assert sent["method"] == "POST"
    assert sent["url"] == "http://chat.test/task/sse?topic=x"
    assert "在吗" in sent["body"]
    conn.close()


def test_ops_requires_token_or_internal(tmp_path):
    app, conn, upstream = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/ops/metrics").status_code == 401
        ok = client.get("/ops/metrics", headers={"X-Skein-Token": TOKEN})
    assert ok.status_code == 200
    sent = upstream.requests[-1]
    assert sent["url"] == "http://ops.test/ops/metrics"
    assert sent["headers"]["cookie"].startswith("ops_session=sub-1||ops@example.com|")
    conn.close()


def test_ops_rejects_public_sso_user(tmp_path):
    app, conn, _ = make_app(tmp_path, sso=True, internal=False, admin=False)
    with TestClient(app) as client:
        response = client.get("/ops/metrics", headers={"Cookie": "unisso_sid=sess-ok"})
    assert response.status_code == 403
    assert response.json()["detail"] == "not_internal"
    conn.close()


def test_ops_rejects_market_user(tmp_path):
    app, conn, _ = make_app(tmp_path, sso=True, internal=True, admin=False)
    with TestClient(app) as client:
        response = client.get("/ops/metrics", headers={"Cookie": "unisso_sid=sess-ok"})
    assert response.status_code == 403
    assert response.json()["detail"] == "not_admin"
    conn.close()


def test_ops_accepts_internal_sso_user(tmp_path):
    app, conn, upstream = make_app(tmp_path, sso=True, internal=True)
    with TestClient(app) as client:
        response = client.get("/ops/metrics", headers={"Cookie": "unisso_sid=sess-ok"})
    assert response.status_code == 200
    assert upstream.requests[-1]["url"] == "http://ops.test/ops/metrics"
    conn.close()


def test_disabled_upstream_returns_503(tmp_path):
    conn = db.connect(tmp_path / "none.db")
    db.migrate(conn)
    app = create_app(conn, str(tmp_path / "b"), TOKEN, lambda *a: {})
    with TestClient(app) as client:
        assert client.get("/api/v1/agent/x").status_code == 503
        assert client.get("/ops/x", headers={"X-Skein-Token": TOKEN}).status_code == 503
    conn.close()
