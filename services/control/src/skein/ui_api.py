"""控制台查询：工单、事件、会话、拓扑、审计，以及准入策略。

payload 与 detail 能解析成 JSON 就解析，失败则保留原字符串。时间仍是 Unix 秒。
读接口不写库。准入策略的 PUT 写 kv 和审计。不引用 api，避免和 create_app 循环依赖。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from skein import logbuf
from skein.admission import load_policies, save_policies
from skein.session_store import blob_path

_TICKET_FIELDS = (
    "id",
    "code",
    "thread_id",
    "title",
    "status",
    "dedup_key",
    "created_at",
    "updated_at",
)
_THREAD_FIELDS = ("id", "origin", "origin_id", "created_at")
_ASSIGNMENT_FIELDS = (
    "ticket_id",
    "worker_id",
    "state",
    "lease_until",
    "result",
    "updated_at",
)
_DISPATCH_FIELDS = (
    "id",
    "ticket_id",
    "node_id",
    "worker_id",
    "state",
    "attempt",
    "detail",
    "updated_at",
)
_SESSION_FIELDS = ("id", "ticket_id", "node_id", "pinned", "created_at", "updated_at")
_EVENT_FIELDS = (
    "id",
    "origin",
    "origin_id",
    "thread_id",
    "verdict",
    "created_at",
    "payload",
)
_TURN_FIELDS = (
    "session_id",
    "turn_index",
    "sha256",
    "bytes",
    "head_sha",
    "branch",
    "created_at",
)
_NODE_FIELDS = ("id", "name", "host", "platforms", "status", "max_workers", "last_seen")
_SUB_FIELDS = ("thread_id", "ticket_id", "state", "created_at")
_AUDIT_FIELDS = ("id", "actor", "action", "detail", "created_at")

_JSON_PAYLOAD = frozenset({"payload"})
_JSON_DETAIL = frozenset({"detail"})

_EVENT_CAP = 500
_TOPOLOGY_TICKETS = 200
_TOPOLOGY_DISPATCHES = 500
_TOPOLOGY_SESSIONS = 500


def _parse_json(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _pick(
    row: sqlite3.Row,
    fields: tuple[str, ...],
    json_fields: frozenset[str] | None = None,
) -> dict:
    parsed = json_fields or frozenset()
    out: dict = {}
    for name in fields:
        value = row[name]
        if name in parsed:
            value = _parse_json(value)
        out[name] = value
    return out


def _rows(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple,
    fields: tuple[str, ...],
    json_fields: frozenset[str] | None = None,
) -> list[dict]:
    return [_pick(row, fields, json_fields) for row in conn.execute(sql, params)]


def _first(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple,
    fields: tuple[str, ...],
    json_fields: frozenset[str] | None = None,
) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return _pick(row, fields, json_fields)


def _nonneg(limit: int) -> int:
    # SQLite 把 LIMIT -1 当成「不限制」。
    return max(0, limit)


def _cap(limit: int, cap: int) -> int:
    return min(_nonneg(limit), cap)


def _events(
    conn: sqlite3.Connection,
    *,
    since: float | None = None,
    verdict: str | None = None,
    origin: str | None = None,
    thread_id: str | None = None,
    limit: int | None = None,
    newest_first: bool = True,
) -> list[dict]:
    sql = (
        "SELECT id, origin, origin_id, thread_id, verdict, created_at, payload"
        " FROM events"
    )
    clauses: list[str] = []
    params: list[object] = []
    if since is not None:
        clauses.append("created_at > ?")
        params.append(since)
    if verdict:
        clauses.append("verdict = ?")
        params.append(verdict)
    if origin:
        clauses.append("origin = ?")
        params.append(origin)
    if thread_id is not None:
        clauses.append("thread_id = ?")
        params.append(thread_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    direction = "DESC" if newest_first else "ASC"
    sql += f" ORDER BY created_at {direction}, id {direction}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return _rows(conn, sql, tuple(params), _EVENT_FIELDS, _JSON_PAYLOAD)


def _sessions(
    conn: sqlite3.Connection,
    *,
    session_id: str | None = None,
    node_id: str | None = None,
    ticket_id: str | None = None,
    limit: int | None = None,
    with_last: bool = True,
) -> list[dict]:
    # COUNT(t.turn_index) 在没有轮次时是 0；COUNT(*) 会把左连接的空行算成 1。
    sql = (
        "SELECT s.id, s.ticket_id, s.node_id, s.pinned, s.created_at, s.updated_at,"
        " COUNT(t.turn_index) AS turn_count, MAX(t.created_at) AS last_turn_at"
        " FROM sessions AS s"
        " LEFT JOIN session_turns AS t ON t.session_id = s.id"
    )
    clauses: list[str] = []
    params: list[object] = []
    if session_id:
        clauses.append("s.id = ?")
        params.append(session_id)
    if node_id:
        clauses.append("s.node_id = ?")
        params.append(node_id)
    if ticket_id:
        clauses.append("s.ticket_id = ?")
        params.append(ticket_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " GROUP BY s.id ORDER BY s.updated_at DESC, s.id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    items = []
    for row in conn.execute(sql, tuple(params)):
        item = _pick(row, _SESSION_FIELDS)
        item["turn_count"] = int(row["turn_count"])
        if with_last:
            item["last_turn_at"] = row["last_turn_at"]
        items.append(item)
    return items


def _turn_text(root: Path, sha256: str) -> str | None:
    try:
        data = blob_path(root, sha256).read_bytes()
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


def create_ui_router(conn: sqlite3.Connection, blob_root: str | Path, auth) -> APIRouter:
    """挂上与控制面相同的 auth 依赖。调用方负责 include_router。"""
    router = APIRouter(dependencies=[Depends(auth)])
    root = Path(blob_root)

    @router.get("/internal/tickets/{ticket_id}")
    def get_ticket(ticket_id: str) -> dict:
        ticket = _first(
            conn,
            "SELECT id, code, thread_id, title, status, dedup_key, created_at, updated_at"
            " FROM tickets WHERE id = ?",
            (ticket_id,),
            _TICKET_FIELDS,
        )
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        thread = None
        if ticket["thread_id"]:
            thread = _first(
                conn,
                "SELECT id, origin, origin_id, created_at FROM threads WHERE id = ?",
                (ticket["thread_id"],),
                _THREAD_FIELDS,
            )
        assignments = _rows(
            conn,
            "SELECT ticket_id, worker_id, state, lease_until, result, updated_at"
            " FROM assignments WHERE ticket_id = ?"
            " ORDER BY updated_at DESC, worker_id",
            (ticket_id,),
            _ASSIGNMENT_FIELDS,
        )
        dispatches = _rows(
            conn,
            "SELECT id, ticket_id, node_id, worker_id, state, attempt, detail, updated_at"
            " FROM dispatches WHERE ticket_id = ? ORDER BY updated_at DESC, id",
            (ticket_id,),
            _DISPATCH_FIELDS,
            _JSON_DETAIL,
        )
        return {
            "ticket": ticket,
            "thread": thread,
            "assignments": assignments,
            "dispatches": dispatches,
            "sessions": _sessions(conn, ticket_id=ticket_id, with_last=False),
        }

    @router.get("/internal/events")
    def list_events(
        since: float | None = None,
        limit: int = 100,
        verdict: str = "",
        origin: str = "",
    ) -> dict:
        return {
            "events": _events(
                conn,
                since=since,
                verdict=verdict or None,
                origin=origin or None,
                limit=_cap(limit, _EVENT_CAP),
            )
        }

    @router.get("/internal/threads/{thread_id}")
    def get_thread(thread_id: str) -> dict:
        thread = _first(
            conn,
            "SELECT id, origin, origin_id, created_at FROM threads WHERE id = ?",
            (thread_id,),
            _THREAD_FIELDS,
        )
        if thread is None:
            raise HTTPException(status_code=404, detail="thread not found")
        tickets = _rows(
            conn,
            "SELECT id, code, thread_id, title, status, dedup_key, created_at, updated_at"
            " FROM tickets WHERE thread_id = ? ORDER BY created_at ASC, id",
            (thread_id,),
            _TICKET_FIELDS,
        )
        return {
            "thread": thread,
            "events": _events(conn, thread_id=thread_id, newest_first=False),
            "tickets": tickets,
        }

    @router.get("/internal/sessions")
    def list_sessions(node_id: str = "", ticket_id: str = "", limit: int = 100) -> dict:
        return {
            "sessions": _sessions(
                conn,
                node_id=node_id or None,
                ticket_id=ticket_id or None,
                limit=_nonneg(limit),
            )
        }

    @router.get("/internal/sessions/{session_id}/turns")
    def list_turns(session_id: str, include_text: bool = False) -> dict:
        found = _sessions(conn, session_id=session_id)
        if not found:
            raise HTTPException(status_code=404, detail="session not found")
        turns = []
        rows = conn.execute(
            "SELECT session_id, turn_index, sha256, bytes, head_sha, branch, created_at"
            " FROM session_turns WHERE session_id = ? ORDER BY turn_index",
            (session_id,),
        )
        for row in rows:
            item = _pick(row, _TURN_FIELDS)
            if include_text:
                text = _turn_text(root, row["sha256"])
                if text is not None:
                    item["text"] = text
            turns.append(item)
        return {"session": found[0], "turns": turns}

    @router.get("/internal/topology")
    def topology() -> dict:
        nodes = _rows(
            conn,
            "SELECT id, name, host, platforms, status, max_workers, last_seen"
            " FROM nodes ORDER BY id",
            (),
            _NODE_FIELDS,
        )
        tickets = _rows(
            conn,
            "SELECT id, code, thread_id, title, status, dedup_key, created_at, updated_at"
            " FROM tickets ORDER BY updated_at DESC, id DESC LIMIT ?",
            (_TOPOLOGY_TICKETS,),
            _TICKET_FIELDS,
        )
        dispatches = _rows(
            conn,
            "SELECT id, ticket_id, node_id, worker_id, state, attempt, detail, updated_at"
            " FROM dispatches ORDER BY updated_at DESC, id DESC LIMIT ?",
            (_TOPOLOGY_DISPATCHES,),
            _DISPATCH_FIELDS,
            _JSON_DETAIL,
        )
        subscriptions = _rows(
            conn,
            "SELECT thread_id, ticket_id, state, created_at FROM subscriptions"
            " ORDER BY created_at, thread_id, ticket_id",
            (),
            _SUB_FIELDS,
        )
        return {
            "nodes": nodes,
            "tickets": tickets,
            "dispatches": dispatches,
            "sessions": _sessions(conn, limit=_TOPOLOGY_SESSIONS),
            "subscriptions": subscriptions,
        }

    @router.get("/internal/audit")
    def list_audit(limit: int = 100) -> dict:
        return {
            "audit": _rows(
                conn,
                "SELECT id, actor, action, detail, created_at FROM audit"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (_nonneg(limit),),
                _AUDIT_FIELDS,
                _JSON_DETAIL,
            )
        }

    @router.get("/internal/logs")
    def list_logs(limit: int = 300, component: str = "", level: str = "") -> dict:
        return {
            "logs": logbuf.recent(
                limit=limit, component=component or None, level=level or None
            )
        }

    @router.get("/internal/admission")
    def get_admission() -> dict:
        return load_policies(conn)

    @router.put("/internal/admission")
    def put_admission(body: dict) -> dict:
        try:
            return save_policies(conn, body)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
