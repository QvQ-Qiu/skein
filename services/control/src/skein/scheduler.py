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
    """把在途派单收尾为 failed；默认把工单放回 staged，等下一次派单。

    没有这条路径时，回执丢失（断线、控制面重启、worker 崩溃）会让 dispatch
    永远停在活动态，既占节点容量，又只能等 Watchdog 报「派单超时」。
    """
    row = conn.execute(
        "SELECT ticket_id, state FROM dispatches WHERE id = ?", (dispatch_id,)
    ).fetchone()
    if row is None:
        return None
    if row["state"] not in ACTIVE_STATES:
        return row["ticket_id"]
    now = time.time()
    conn.execute(
        "UPDATE dispatches SET state = 'failed', detail = ?, updated_at = ?"
        " WHERE id = ? AND state IN ('pending', 'created', 'dispatched')",
        (detail, now, dispatch_id),
    )
    if requeue:
        conn.execute(
            "UPDATE tickets SET status = 'staged', updated_at = ?"
            " WHERE id = ? AND status IN ('assigned', 'running')",
            (now, row["ticket_id"]),
        )
    return row["ticket_id"]


def reconcile_node(
    conn: sqlite3.Connection,
    node_id: str,
    busy_worker_ids: set[str],
) -> list[str]:
    """节点重连时对账：库里有在途派单，但节点已不在跑对应 worker。

    节点注册消息带 `workers` 与 `busy`；重连后仍 busy 的保留，其余说明
    ack/result 在断线窗口丢了，收尾为 failed，避免永久占容量。
    """
    failed: list[str] = []
    for row in conn.execute(
        "SELECT id, worker_id FROM dispatches"
        " WHERE node_id = ? AND state = 'dispatched'",
        (node_id,),
    ):
        worker_id = str(row["worker_id"] or "")
        if worker_id and worker_id in busy_worker_ids:
            continue
        fail_dispatch(
            conn,
            row["id"],
            detail="节点重连时 worker 已不在运行（回执丢失）",
        )
        db.record_audit(
            conn,
            "control",
            "dispatch_reconcile",
            {"dispatch_id": row["id"], "node_id": node_id, "worker_id": worker_id},
        )
        failed.append(row["id"])
    return failed


def expire_stale_dispatches(
    conn: sqlite3.Connection,
    older_than_s: float,
    now: float | None = None,
) -> list[str]:
    """收尾长时间没有进展的派单，避免永久占用节点容量。

    阈值应大于单次 worker 执行上限与 Watchdog 的超时阈值：先让 Watchdog
    看到异常并建单，再由这里自愈。
    """
    cutoff = (now if now is not None else time.time()) - older_than_s
    failed: list[str] = []
    for row in conn.execute(
        "SELECT id FROM dispatches"
        " WHERE state IN ('pending', 'created', 'dispatched') AND updated_at < ?",
        (cutoff,),
    ):
        fail_dispatch(
            conn, row["id"], detail=f"stale: 超过 {int(older_than_s)}s 没有进展"
        )
        db.record_audit(
            conn,
            "control",
            "dispatch_expired",
            {"dispatch_id": row["id"], "older_than_s": int(older_than_s)},
        )
        failed.append(row["id"])
    return failed


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
