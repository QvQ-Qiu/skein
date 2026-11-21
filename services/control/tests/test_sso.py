import time

import pytest
from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app
from skein.sso import Principal, SsoConfig, SsoRejected, SsoUnavailable

TOKEN = "dev-token"


class FakeSsoClient:
    """unisso 的替身：同样的成功/拒绝/不可用三态。"""

    def __init__(self) -> None:
        self.down = False
        self.revoked: list[str] = []
        self.users = {"a@ssemarket.cn": ("sess-a", True)}
        self.sessions = {
            "sess-a": Principal(
                sub="u1", name="张三", email="a@ssemarket.cn", internal=True
            )
        }

    def password_verify(self, email: str, password: str) -> dict:
        if self.down:
            raise SsoUnavailable("down")
        if email not in self.users or password != "right":
            raise SsoRejected("invalid_credentials", 401)
        session, internal = self.users[email]
        return {
            "user": {"sub": "u1", "name": "张三", "email": email, "internal": internal},
            "sso_session": session,
            "sso_cookie_name": "unisso_sid",
            "sso_cookie_domain": ".ssemarket.cn",
            "sso_max_age": 604800,
        }

    def session_verify(self, session: str) -> Principal | None:
        if self.down:
            raise SsoUnavailable("down")
        return self.sessions.get(session)

    def session_revoke(self, session: str) -> None:
        if self.down:
            raise SsoUnavailable("down")
        self.revoked.append(session)
        self.sessions.pop(session, None)


@pytest.fixture()
def sso_env(tmp_path):
    conn = db.connect(tmp_path / "sso.db")
    db.migrate(conn)
    client = FakeSsoClient()
    app = create_app(
        conn,
        str(tmp_path / "blobs"),
        TOKEN,
        lambda *a: {},
        sso_config=SsoConfig(client_id="c", client_secret="s"),
        sso_client=client,
    )
    with TestClient(app) as test:
        test.conn = conn
        test.sso = client
        yield test
    conn.close()


def auth() -> dict[str, str]:
    return {"X-Skein-Token": TOKEN}


def test_token_mode_when_sso_absent(tmp_path):
    conn = db.connect(tmp_path / "none.db")
    db.migrate(conn)
    app = create_app(conn, str(tmp_path / "b"), TOKEN, lambda *a: {})
    with TestClient(app) as client:
        assert client.get("/api/session").json() == {"mode": "token"}
    conn.close()


def test_session_without_cookie_is_no_sso(sso_env):
    response = sso_env.get("/api/session")
    assert response.status_code == 401
    assert response.json()["detail"] == "no_sso"


def sso_cookie() -> dict[str, str]:
    # httpx 不会替 testserver 域保存 .ssemarket.cn 的 Cookie，测试里手动带上。
    return {"Cookie": "unisso_sid=sess-a"}


def test_login_sets_sso_cookie_and_apis_accept_it(sso_env):
    response = sso_env.post(
        "/api/session/login", json={"email": "a@ssemarket.cn", "password": "right"}
    )
    assert response.status_code == 200
    assert response.json()["user"]["name"] == "张三"
    cookie = response.headers["set-cookie"]
    assert "unisso_sid=sess-a" in cookie
    assert "Domain=.ssemarket.cn" in cookie
    assert "HttpOnly" in cookie
    assert sso_env.get("/internal/summary", headers=sso_cookie()).status_code == 200


def test_login_wrong_password_maps_401(sso_env):
    response = sso_env.post(
        "/api/session/login", json={"email": "a@ssemarket.cn", "password": "nope"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_credentials"


def test_login_non_internal_rejected(sso_env):
    sso_env.sso.users["b@ssemarket.cn"] = ("sess-b", False)
    sso_env.sso.sessions["sess-b"] = Principal(
        sub="u2", email="b@ssemarket.cn", internal=False
    )
    response = sso_env.post(
        "/api/session/login", json={"email": "b@ssemarket.cn", "password": "right"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "not_internal"


def test_market_surface_allows_non_internal(sso_env):
    sso_env.sso.users["b@ssemarket.cn"] = ("sess-b", False)
    sso_env.sso.sessions["sess-b"] = Principal(
        sub="u2", email="b@ssemarket.cn", internal=False
    )
    response = sso_env.get(
        "/api/session?surface=market", headers={"Cookie": "unisso_sid=sess-b"}
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "market"
    assert response.json()["user"]["internal"] is False


def test_console_still_rejects_non_internal_after_market_probe(sso_env):
    # 会话缓存的是主体，不是授权结论；先走 market 再走控制台，必须重新判 internal。
    sso_env.sso.sessions["sess-b"] = Principal(
        sub="u2", email="b@ssemarket.cn", internal=False
    )
    cookies = {"Cookie": "unisso_sid=sess-b"}
    assert sso_env.get("/api/session?surface=market", headers=cookies).status_code == 200
    assert sso_env.get("/api/session", headers=cookies).status_code == 403
    assert sso_env.get("/internal/summary", headers=cookies).status_code == 403


def test_market_login_allows_non_internal(sso_env):
    sso_env.sso.users["b@ssemarket.cn"] = ("sess-b", False)
    sso_env.sso.sessions["sess-b"] = Principal(
        sub="u2", email="b@ssemarket.cn", internal=False
    )
    ok = sso_env.post(
        "/api/session/login",
        json={
            "email": "b@ssemarket.cn",
            "password": "right",
            "surface": "market",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["kind"] == "market"
    denied = sso_env.post(
        "/api/session/login",
        json={"email": "b@ssemarket.cn", "password": "right"},
    )
    assert denied.status_code == 403


def test_logout_revokes_and_clears(sso_env):
    sso_env.post("/api/session/login", json={"email": "a@ssemarket.cn", "password": "right"})
    response = sso_env.post("/api/session/logout", headers=sso_cookie())
    assert response.json() == {"ok": True}
    assert sso_env.sso.revoked == ["sess-a"]
    assert sso_env.get("/internal/summary", headers=sso_cookie()).status_code == 401


def test_review_records_sso_actor(sso_env):
    now = time.time()
    sso_env.conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES ('t9', 'SK-9', 'x', 'review', ?, ?)",
        (now, now),
    )
    sso_env.post("/api/session/login", json={"email": "a@ssemarket.cn", "password": "right"})
    response = sso_env.post(
        "/internal/tickets/t9/review", json={"decision": "approve"}, headers=sso_cookie()
    )
    assert response.status_code == 200
    actor = sso_env.conn.execute(
        "SELECT actor FROM audit ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert actor == "张三"


def test_unavailable_maps_503(sso_env):
    sso_env.sso.sessions["sess-x"] = Principal(sub="u3", internal=True)
    sso_env.sso.down = True
    response = sso_env.get(
        "/internal/summary", headers={"Cookie": "unisso_sid=sess-x"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "sso_unavailable"


def test_token_still_works_alongside_sso(sso_env):
    response = sso_env.get("/internal/summary", headers=auth())
    assert response.status_code == 200
