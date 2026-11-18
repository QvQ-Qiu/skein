"""Outbox：对外写只走这一条路，且同一件事只写一次。

语义：
- 写之前先落 started（占用 key）；
- 写成功落 confirmed，并记下主站返回的对象号；
- 写失败落 failed，不重试；
- 进程在请求发出后死掉，重启时看到 started，先向主站核对再决定补发还是收口。
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass


class EffectState:
    STARTED = "started"
    CONFIRMED = "confirmed"
    FAILED = "failed"


@dataclass(frozen=True)
class Effect:
    key: str
    kind: str
    object: str
    state: str
    response: dict | None


class EmitResult:
    SENT = "sent"
    ALREADY = "already"
    FAILED = "failed"


def get(conn: sqlite3.Connection, key: str) -> Effect | None:
    row = conn.execute(
        "SELECT key, kind, object, state, response FROM effects WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return None
    response = json.loads(row["response"]) if row["response"] else None
    return Effect(row["key"], row["kind"], row["object"], row["state"], response)


def claim(conn: sqlite3.Connection, key: str, kind: str, object_: str) -> bool:
    t = time.time()
    try:
        conn.execute(
            "INSERT INTO effects (key, kind, object, state, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (key, kind, object_, EffectState.STARTED, t, t),
        )
    except sqlite3.IntegrityError:
        return False
    return True


def confirm(conn: sqlite3.Connection, key: str, response: dict) -> None:
    conn.execute(
        "UPDATE effects SET state = ?, response = ?, updated_at = ? WHERE key = ?",
        (EffectState.CONFIRMED, json.dumps(response, ensure_ascii=False), time.time(), key),
    )


def mark_failed(conn: sqlite3.Connection, key: str, reason: str) -> None:
    conn.execute(
        "UPDATE effects SET state = ?, response = ?, updated_at = ? WHERE key = ?",
        (EffectState.FAILED, json.dumps({"error": reason}, ensure_ascii=False), time.time(), key),
    )


def pending(conn: sqlite3.Connection) -> list[Effect]:
    rows = conn.execute(
        "SELECT key, kind, object, state, response FROM effects WHERE state = ? ORDER BY created_at",
        (EffectState.STARTED,),
    ).fetchall()
    return [Effect(r["key"], r["kind"], r["object"], r["state"], None) for r in rows]


Writer = Callable[[str, str, dict], dict]


class Outbox:
    def __init__(self, conn: sqlite3.Connection, writer: Writer) -> None:
        self._conn = conn
        self._writer = writer

    def emit(self, key: str, kind: str, object_: str, payload: dict) -> str:
        existing = get(self._conn, key)
        if existing is not None:
            if existing.state == EffectState.CONFIRMED:
                return EmitResult.ALREADY
            if existing.state == EffectState.FAILED:
                return EmitResult.FAILED
            return EmitResult.ALREADY

        if not claim(self._conn, key, kind, object_):
            return EmitResult.ALREADY

        try:
            response = self._writer(kind, object_, payload)
        except Exception as exc:
            mark_failed(self._conn, key, str(exc))
            raise
        confirm(self._conn, key, response)
        return EmitResult.SENT

    def reconcile(self, key: str, probe: Callable[[str, str], dict | None]) -> str:
        """进程重启后处理 started 状态：主站已有结果就收口，没有就补发。"""
        existing = get(self._conn, key)
        if existing is None or existing.state != EffectState.STARTED:
            return EmitResult.ALREADY
        found = probe(existing.kind, existing.object)
        if found is not None:
            confirm(self._conn, key, found)
            return EmitResult.ALREADY
        mark_failed(self._conn, key, "reconcile_missing")
        return EmitResult.FAILED
