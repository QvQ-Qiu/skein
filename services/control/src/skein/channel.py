"""节点通道：node-agent 反向拨入，注册、心跳、建 worker、派单、回报。

协议与旧雁阵保持兼容，字段名不变，便于逐步替换。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from skein import db, scheduler

log = logging.getLogger("skein.channel")


class ChannelHub:
    def __init__(
        self,
        conn: sqlite3.Connection,
        token: str,
        platform_repos: dict[str, str] | None = None,
    ) -> None:
        self._conn = conn
        self._token = token
        self._repos = platform_repos or {}
        self._sockets: dict[str, WebSocket] = {}
        self._pending: dict[str, dict] = {}

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        node_id: str | None = None
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                node_id = await self._handle(msg, ws, node_id)
        except WebSocketDisconnect:
            pass
        except RuntimeError:
            pass
        finally:
            if node_id and self._sockets.get(node_id) is ws:
                self._sockets.pop(node_id, None)
                scheduler.set_status(self._conn, node_id, "offline")

    async def _handle(self, msg: dict, ws: WebSocket, node_id: str | None) -> str | None:
        kind = msg.get("t")

        if kind == "node.register":
            if msg.get("token") != self._token:
                await ws.send_json({"t": "error", "reason": "unauthorized"})
                await ws.close()
                return None
            node_id = str(msg.get("node_id") or "")
            if not node_id:
                await ws.send_json({"t": "error", "reason": "missing node_id"})
                return None
            scheduler.upsert_node(
                self._conn,
                node_id,
                name=str(msg.get("name") or node_id),
                host=str(msg.get("host") or ""),
                platforms=list(msg.get("platforms") or []),
                peer_url=str(msg.get("peer_url") or ""),
                max_workers=int(msg.get("max_workers") or 3),
            )
            self._sockets[node_id] = ws
            log.info(
                "node registered %s host=%s platforms=%s",
                node_id,
                msg.get("host"),
                msg.get("platforms"),
            )
            workers = msg.get("workers")
            if isinstance(workers, list):
                # 节点会上报本地 worker 与 busy；据此收尾断线窗口丢回执的派单。
                # 老版本节点不带这个字段时不做对账，避免误判。
                busy = {
                    str(item.get("worker_id"))
                    for item in workers
                    if isinstance(item, dict) and item.get("busy") and item.get("worker_id")
                }
                recovered = scheduler.reconcile_node(self._conn, node_id, busy)
                if recovered:
                    log.warning("节点 %s 重连：收尾丢失回执的派单 %s", node_id, recovered)
            await ws.send_json({"t": "node.registered", "node_id": node_id})
            return node_id

        if kind == "node.heartbeat":
            if node_id:
                scheduler.touch(self._conn, node_id)
            return node_id

        if kind == "worker.created":
            await self._on_worker_created(msg, ws)
            return node_id

        if kind == "result":
            self._on_result(msg)
            return node_id

        if kind == "ack":
            dispatch_id = self._dispatch_of(msg.get("worker_id"))
            if dispatch_id:
                # 迟到的 ack 不能把已被对账收尾的派单重新拉回活动态。
                self._conn.execute(
                    "UPDATE dispatches SET state = 'dispatched', updated_at = ?"
                    " WHERE id = ? AND state IN ('pending', 'created')",
                    (time.time(), dispatch_id),
                )
            return node_id

        if kind in ("worker.create_failed", "nack"):
            worker_id = str(msg.get("worker_id") or "")
            reason = str(msg.get("reason") or kind)
            log.warning("%s %s: %s", kind, worker_id, reason)
            pending = self._pending.pop(worker_id, None)
            dispatch_id = pending["dispatch_id"] if pending else self._dispatch_of(worker_id)
            if dispatch_id:
                scheduler.fail_dispatch(self._conn, dispatch_id, detail=reason)
            return node_id

        return node_id

    async def _on_worker_created(self, msg: dict, ws: WebSocket) -> None:
        worker_id = str(msg.get("worker_id") or "")
        pending = self._pending.pop(worker_id, None)
        if pending:
            dispatch_id = pending["dispatch_id"]
            ticket_id = pending["ticket_id"]
            platform = pending.get("platform") or ""
        else:
            # 控制面重启会丢掉内存里的 _pending；worker_id 已落库，按库恢复，
            # 否则 worker.created 被静默丢弃，派单永远停在 pending。
            row = self._conn.execute(
                "SELECT id, ticket_id FROM dispatches"
                " WHERE worker_id = ? AND state IN ('pending', 'created')"
                " ORDER BY updated_at DESC LIMIT 1",
                (worker_id,),
            ).fetchone()
            if row is None:
                return
            dispatch_id = row["id"]
            ticket_id = row["ticket_id"]
            platform = ""
        scheduler.update_dispatch(self._conn, dispatch_id, state="created", worker_id=worker_id)
        ticket = self._conn.execute(
            "SELECT id, code, title, summary FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if ticket is None:
            scheduler.fail_dispatch(self._conn, dispatch_id, detail="ticket missing")
            return
        try:
            await ws.send_json(
                {
                    "t": "dispatch",
                    "worker_id": worker_id,
                    "ticket": {
                        "id": ticket["id"],
                        "code": ticket["code"],
                        "title": ticket["title"],
                        "summary": ticket["summary"] or "",
                        "platform": platform,
                        "kind": "bug",
                        "severity": "normal",
                    },
                }
            )
        except (RuntimeError, WebSocketDisconnect):
            # 连接在 worker.created 之后断开：不要假装已派出去，放回队列重派。
            scheduler.fail_dispatch(self._conn, dispatch_id, detail="dispatch send failed")
            return
        scheduler.update_dispatch(self._conn, dispatch_id, state="dispatched")

    def _on_result(self, msg: dict) -> None:
        worker_id = str(msg.get("worker_id") or "")
        dispatch_id = self._dispatch_of(worker_id)
        if not dispatch_id:
            return
        row = self._conn.execute(
            "SELECT ticket_id FROM dispatches WHERE id = ?", (dispatch_id,)
        ).fetchone()
        scheduler.update_dispatch(self._conn, dispatch_id, state="done")
        if row:
            ticket_id = row["ticket_id"]
            log.info("result worker=%s ticket=%s", worker_id, ticket_id)
            self._conn.execute(
                "INSERT OR IGNORE INTO assignments (ticket_id, worker_id, state, result, updated_at)"
                " VALUES (?, ?, 'done', ?, ?)",
                (ticket_id, worker_id, json.dumps(msg.get("result") or {}, ensure_ascii=False), time.time()),
            )
            self._conn.execute(
                "UPDATE tickets SET status = 'review', updated_at = ? WHERE id = ?",
                (time.time(), ticket_id),
            )
            db.record_audit(
                self._conn, "node", "result", {"ticket_id": ticket_id, "worker_id": worker_id}
            )

    def _dispatch_of(self, worker_id: object) -> str | None:
        if not worker_id:
            return None
        row = self._conn.execute(
            "SELECT id FROM dispatches WHERE worker_id = ? ORDER BY updated_at DESC LIMIT 1",
            (str(worker_id),),
        ).fetchone()
        return row["id"] if row else None

    async def dispatch_ticket(self, ticket_id: str, platform: str | None = None) -> dict:
        ticket = self._conn.execute(
            "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if ticket is None:
            return {"ok": False, "reason": "ticket_not_found"}
        active = self._conn.execute(
            "SELECT id FROM dispatches WHERE ticket_id = ?"
            " AND state IN ('pending', 'created', 'dispatched') LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if active is not None:
            # 同一工单已有在途派单时不再开第二单，避免留下永远不回收的孤儿行。
            return {"ok": False, "reason": "already_active", "dispatch_id": active["id"]}
        node_id = scheduler.pick_node(self._conn, platform)
        if node_id is None:
            return {"ok": False, "reason": "no_node"}
        ws = self._sockets.get(node_id)
        if ws is None:
            return {"ok": False, "reason": "node_not_connected"}

        dispatch_id = scheduler.open_dispatch(self._conn, ticket_id, node_id)
        worker_id = f"w-{dispatch_id[:8]}"
        # worker_id 落库：控制面重启后仍能按 worker_id 找回这条派单。
        scheduler.update_dispatch(self._conn, dispatch_id, worker_id=worker_id)
        self._conn.execute(
            "UPDATE tickets SET status = 'assigned', updated_at = ?"
            " WHERE id = ? AND status = 'staged'",
            (time.time(), ticket_id),
        )
        self._pending[worker_id] = {
            "dispatch_id": dispatch_id,
            "ticket_id": ticket_id,
            "node_id": node_id,
            "platform": platform,
        }
        log.info("dispatch ticket=%s node=%s worker=%s", ticket_id, node_id, worker_id)
        try:
            await ws.send_json(
                {
                    "t": "worker.create",
                    "worker_id": worker_id,
                    "platform": platform or "",
                    "repo": self._repos.get(platform or "", ""),
                }
            )
        except (RuntimeError, WebSocketDisconnect):
            self._pending.pop(worker_id, None)
            scheduler.fail_dispatch(self._conn, dispatch_id, detail="worker.create send failed")
            return {"ok": False, "reason": "send_failed", "dispatch_id": dispatch_id}
        return {"ok": True, "dispatch_id": dispatch_id, "worker_id": worker_id, "node_id": node_id}

    async def cancel_ticket(self, ticket_id: str) -> bool:
        """向节点发 cancel，并把派工与工单状态落回可重派。"""
        row = self._conn.execute(
            "SELECT id, node_id, worker_id FROM dispatches"
            " WHERE ticket_id = ? AND state IN ('dispatched', 'created', 'pending')"
            " ORDER BY updated_at DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if row is None or not row["node_id"]:
            return False
        ws = self._sockets.get(str(row["node_id"]))
        if ws is None:
            return False
        await ws.send_json(
            {
                "t": "cancel",
                "ticket_id": ticket_id,
                "worker_id": row["worker_id"],
            }
        )
        scheduler.update_dispatch(self._conn, row["id"], state="failed", detail="cancelled")
        self._conn.execute(
            "UPDATE tickets SET status = 'staged', updated_at = ?"
            " WHERE id = ? AND status IN ('assigned', 'running')",
            (time.time(), ticket_id),
        )
        db.record_audit(self._conn, "human", "cancel", {"ticket_id": ticket_id})
        return True
