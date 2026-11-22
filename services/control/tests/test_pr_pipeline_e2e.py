"""PR 链路端到端验证（SK-62AC28E5）。

模拟一次 worker result，走真实控制面通道与 forge，验证整条链路：

1. 节点注册 → 派单 → worker 回 result（ok + pushed）；
2. forge 调 GitHub API 建 **draft** PR（head/base/仓库/标题/正文正确）；
3. PR URL 写回 ``dispatches.pr_url``，并记一条 ``pr`` 审计；
4. 验证完成后关闭 PR，并清理测试工单（ticket/dispatch/assignment）。

GitHub 用进程内假传输，不访问外网、不产生真实 PR；真实环境里的
建/关 PR 由控制面持有令牌执行，令牌不进节点。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from skein import db
from skein.api import create_app
from skein.forge import GitHubClient, PullRequestService

TOKEN = "pr-e2e-token"
REPO = "o/r"
BRANCH = "fix/yz-sk-62ac28e5"


class FakeGitHub:
    """最小 GitHub pulls API：POST 建 PR，PATCH 关 PR。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.pulls: dict[int, dict] = {}

    def __call__(
        self, method: str, url: str, payload: dict | None
    ) -> tuple[int, dict]:
        self.calls.append((method, url, payload))
        if method == "POST" and url == f"https://api.github.com/repos/{REPO}/pulls":
            number = 101
            self.pulls[number] = {
                "number": number,
                "html_url": f"https://github.com/{REPO}/pull/{number}",
                "state": "open",
            }
            return 201, dict(self.pulls[number])
        if method == "PATCH" and url.startswith(
            f"https://api.github.com/repos/{REPO}/pulls/"
        ):
            number = int(url.rsplit("/", 1)[1])
            if number in self.pulls:
                self.pulls[number]["state"] = "closed"
                return 200, dict(self.pulls[number])
        return 404, {"message": "not found"}


@pytest.fixture()
def app(tmp_path):
    conn = db.connect(tmp_path / "pr-e2e.db")
    db.migrate(conn)
    github = FakeGitHub()
    client = GitHubClient("test-token", http=github)
    service = PullRequestService(client, repos={"skein": REPO}, base="main", draft=True)
    application = create_app(
        conn,
        str(tmp_path / "blobs"),
        TOKEN,
        platform_repos={"skein": "/srv/skein"},
        pr_service=service,
    )
    with TestClient(application) as api:
        api.conn = conn
        api.github = github
        api.pr = client
        yield api
    conn.close()


def make_ticket(conn, code: str = "SK-E2E") -> str:
    now = time.time()
    conn.execute(
        "INSERT INTO tickets (id, code, title, summary, status, created_at, updated_at)"
        " VALUES (?, ?, 'PR 链路端到端验证', '模拟 worker result 验证 forge 出 draft PR',"
        " 'staged', ?, ?)",
        (code, code, now, now),
    )
    return code


def register(ws, platforms=("skein",), node_id: str = "n-prverify") -> dict:
    ws.send_json(
        {
            "t": "node.register",
            "node_id": node_id,
            "token": TOKEN,
            "name": "pr-verify",
            "platforms": list(platforms),
            "max_workers": 2,
            "peer_url": "ws://127.0.0.1:9101/peer",
        }
    )
    return ws.receive_json()


def wait_for(getter, timeout: float = 3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = getter()
        if value:
            return value
        time.sleep(0.02)
    return getter()


def cleanup(conn, ticket_id: str) -> None:
    """验证完清理测试工单及其派工/回执（审计保留）。"""
    conn.execute("DELETE FROM assignments WHERE ticket_id = ?", (ticket_id,))
    conn.execute("DELETE FROM dispatches WHERE ticket_id = ?", (ticket_id,))
    conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    conn.commit()


def test_worker_result_creates_draft_pr_then_closes_and_cleans_up(app):
    ticket = make_ticket(app.conn)

    with app.websocket_connect("/api/agent/channel") as ws:
        assert register(ws)["t"] == "node.registered"
        body = app.post(
            f"/internal/tickets/{ticket}/dispatch?platform=skein",
            headers={"X-Skein-Token": TOKEN},
        ).json()
        assert body["ok"] is True

        create = ws.receive_json()
        assert create["t"] == "worker.create"
        assert create["platform"] == "skein"
        ws.send_json(
            {
                "t": "worker.created",
                "worker_id": body["worker_id"],
                "session_id": "sess-e2e",
            }
        )
        assert ws.receive_json()["t"] == "dispatch"

        ws.send_json(
            {
                "t": "result",
                "ticket_id": ticket,
                "worker_id": body["worker_id"],
                "result": {
                    "ok": True,
                    "pushed": True,
                    "branch": BRANCH,
                    "base_commit": "abc1234",
                    "commits": "abc1234 e2e: PR 链路验证",
                    "diffstat": "1 file changed",
                    "remote": f"git@github.com:{REPO}.git",
                    "platform": "skein",
                },
            }
        )

        pr_url = wait_for(
            lambda: app.conn.execute(
                "SELECT pr_url FROM dispatches WHERE worker_id = ?",
                (body["worker_id"],),
            ).fetchone()[0]
        )
        status = wait_for(
            lambda: "review"
            if app.conn.execute(
                "SELECT status FROM tickets WHERE id = ?", (ticket,)
            ).fetchone()[0]
            == "review"
            else ""
        )

    assert pr_url == f"https://github.com/{REPO}/pull/101"
    assert status == "review"

    # forge 确实按 draft 建 PR，head/base/title/body 都来自工单与 result。
    method, url, payload = app.github.calls[0]
    assert (method, url) == ("POST", f"https://api.github.com/repos/{REPO}/pulls")
    assert payload["draft"] is True
    assert payload["head"] == BRANCH
    assert payload["base"] == "main"
    assert payload["title"].startswith("SK-E2E:")
    assert "SK-E2E" in payload["body"]
    assert "不会自动合入" in payload["body"]

    # URL 落库并留审计。
    audit = app.conn.execute(
        "SELECT detail FROM audit WHERE action = 'pr' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit is not None
    assert json.loads(audit[0])["url"] == pr_url

    # 验证完关闭 PR。
    closed = app.pr.close(REPO, 101)
    assert closed.state == "closed"
    assert app.github.pulls[101]["state"] == "closed"

    # 清理工单：测试单及其派工/回执都不再留在库中。
    cleanup(app.conn, ticket)
    for table in ("tickets", "dispatches", "assignments"):
        count = app.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE "
            + ("id = ?" if table == "tickets" else "ticket_id = ?"),
            (ticket,),
        ).fetchone()[0]
        assert count == 0