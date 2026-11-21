"""节点注册与派单选择。

节点通过反向拨入注册；派单只看「在线、有心跳、平台匹配、未满员」。
"""

from __future__ import annotations

import json
import sqlite3
import time

from skein import db

ONLINE_WINDOW_S = 60.0
ACTIVE_STATES = ("pending", "created", "dispatched")


def upsert_node(
    conn: sqlite3.Connection,
    node_id: str,
    name: str = "",
    host: str = "",
    platforms: list[str] | None = None,
    peer_url: str = "",
    max_workers: int = 3,
) -> None:
    conn.execute(
        "INSERT INTO nodes (id, name, host, platforms, peer_url, status, max_workers, last_seen)"
        " VALUES (?, ?, ?, ?, ?, 'online', ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET name=excluded.name, host=excluded.host,"
        " platforms=excluded.platforms, peer_url=excluded.peer_url, status='online',"
        " max_workers=excluded.max_workers, last_seen=excluded.last_seen",
        (node_id, name, host, json.dumps(platforms or []), peer_url, max_workers, time.time()),
    )


def touch(conn: sqlite3.Connection, node_id: str) -> None:
    conn.execute(
        "UPDATE nodes SET last_seen = ?, status = 'online' WHERE id = ?", (time.time(), node_id)
    )


def set_status(conn: sqlite3.Connection, node_id: str, status: str) -> None:
    conn.execute("UPDATE nodes SET status = ? WHERE id = ?", (status, node_id))


def online(conn: sqlite3.Connection, now: float | None = None) -> list[sqlite3.Row]:
    cutoff = (now if now is not None else time.time()) - ONLINE_WINDOW_S
    return list(
        conn.execute(
            "SELECT id, name, host, platforms, peer_url, max_workers, last_seen"
            " FROM nodes WHERE status = 'online' AND last_seen >= ?",
            (cutoff,),
        )
    )


def active_count(conn: sqlite3.Connection, node_id: str) -> int:
    marks = ",".join("?" for _ in ACTIVE_STATES)
    row = conn.execute(
        f"SELECT COUNT(*) FROM dispatches WHERE node_id = ? AND state IN ({marks})",
        (node_id, *ACTIVE_STATES),
    ).fetchone()
    return int(row[0])


def _accepts(platforms_json: str, platform: str | None) -> bool:
    if platform is None:
        return True
    try:
        platforms = json.loads(platforms_json)
    except json.JSONDecodeError:
        return False
    return not platforms or "*" in platforms or platform in platforms


def pick_node(
    conn: sqlite3.Connection, platform: str | None = None, now: float | None = None
) -> str | None:
    candidates = [
        row
        for row in online(conn, now)
        if _accepts(row["platforms"], platform)
        and active_count(conn, row["id"]) < row["max_workers"]
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (active_count(conn, row["id"]), row["id"]))
    return candidates[0]["id"]


def open_dispatch(conn: sqlite3.Connection, ticket_id: str, node_id: str) -> str:
    dispatch_id = db.new_id()
    conn.execute(
        "INSERT INTO dispatches (id, ticket_id, node_id, state, updated_at)"
        " VALUES (?, ?, ?, 'pending', ?)",
        (dispatch_id, ticket_id, node_id, time.time()),
    )
    return dispatch_id


def update_dispatch(
    conn: sqlite3.Connection,
    dispatch_id: str,
    state: str | None = None,
    worker_id: str | None = None,
    detail: str | None = None,
) -> None:
    sets = ["updated_at = ?"]
    params: list[object] = [time.time()]
    if state is not None:
        sets.append("state = ?")
        params.append(state)
    if worker_id is not None:
        sets.append("worker_id = ?")
        params.append(worker_id)
    if detail is not None:
        sets.append("detail = ?")
        params.append(detail)
    params.append(dispatch_id)
    conn.execute(f"UPDATE dispatches SET {', '.join(sets)} WHERE id = ?", tuple(params))


def fail_dispatch(
    conn: sqlite3.Connection,
    dispatch_id: str,
    detail: str = "",
    requeue: bool = True,
) -> str | None:
    """把派单收尾为 failed；默认把工单放回 staged，等下一次派单。

    没有这条路径时，worker 侧 nack / 下发失败会让 dispatch 永远停在活动态，
    既占节点容量，又只能等 Watchdog 报「派单超时」。
    """
    row = conn.execute(
        "SELECT ticket_id FROM dispatches WHERE id = ?", (dispatch_id,)
    ).fetchone()
    now = time.time()
    conn.execute(
        "UPDATE dispatches SET state = 'failed', detail = ?, updated_at = ? WHERE id = ?",
        (detail, now, dispatch_id),
    )
    if requeue and row is not None:
        conn.execute(
            "UPDATE tickets SET status = 'staged', updated_at = ?"
            " WHERE id = ? AND status IN ('assigned', 'running')",
            (now, row["ticket_id"]),
        )
    return row["ticket_id"] if row is not None else None


def dispatch(conn: sqlite3.Connection, ticket_id: str, platform: str | None = None) -> str | None:
    """给工单挑一个节点并开派单；没有可用节点时返回 None，工单保持原状。"""
    node_id = pick_node(conn, platform)
    if node_id is None:
        return None
    dispatch_id = open_dispatch(conn, ticket_id, node_id)
    conn.execute(
        "UPDATE tickets SET status = 'assigned', updated_at = ? WHERE id = ? AND status = 'staged'",
        (time.time(), ticket_id),
    )
    return dispatch_id
