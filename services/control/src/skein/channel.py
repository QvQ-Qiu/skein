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

from skein import db, scheduler, store

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
                scheduler.update_dispatch(self._conn, dispatch_id, state="dispatched")
            return node_id

        if kind == "worker.create_failed":
            log.warning(
                "worker create failed %s: %s",
                msg.get("worker_id"),
                msg.get("reason"),
            )
            pending = self._pending.pop(str(msg.get("worker_id")), None)
            if pending:
                scheduler.update_dispatch(
                    self._conn, pending["dispatch_id"], state="failed", detail=str(msg.get("reason"))
                )
            return node_id

        return node_id

    async def _on_worker_created(self, msg: dict, ws: WebSocket) -> None:
        worker_id = str(msg.get("worker_id") or "")
        pending = self._pending.pop(worker_id, None)
        if not pending:
            return
        scheduler.update_dispatch(
            self._conn, pending["dispatch_id"], state="created", worker_id=worker_id
        )
        ticket = self._conn.execute(
            "SELECT id, code, title FROM tickets WHERE id = ?", (pending["ticket_id"],)
        ).fetchone()
        if ticket is None:
            return
        scheduler.update_dispatch(self._conn, pending["dispatch_id"], state="dispatched")
        await ws.send_json(
            {
                "t": "dispatch",
                "worker_id": worker_id,
                "ticket": {
                    "id": ticket["id"],
                    "code": ticket["code"],
                    "title": ticket["title"],
                    "platform": pending.get("platform") or "",
                    "kind": "bug",
                    "severity": "normal",
                },
            }
        )

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
        node_id = scheduler.pick_node(self._conn, platform)
        if node_id is None:
            return {"ok": False, "reason": "no_node"}
        ws = self._sockets.get(node_id)
        if ws is None:
            return {"ok": False, "reason": "node_not_connected"}

        dispatch_id = scheduler.open_dispatch(self._conn, ticket_id, node_id)
        worker_id = f"w-{dispatch_id[:8]}"
        self._pending[worker_id] = {
            "dispatch_id": dispatch_id,
            "ticket_id": ticket_id,
            "node_id": node_id,
            "platform": platform,
        }
        log.info("dispatch ticket=%s node=%s worker=%s", ticket_id, node_id, worker_id)
        await ws.send_json(
            {
                "t": "worker.create",
                "worker_id": worker_id,
                "platform": platform or "",
                "repo": self._repos.get(platform or "", ""),
            }
        )
        return {"ok": True, "dispatch_id": dispatch_id, "worker_id": worker_id, "node_id": node_id}

    async def followup(self, thread_id: str, event: dict) -> bool:
        """把同一 Thread 的后续事件直接推给已订阅的 worker；失败由调用方回退。"""
        sub = store.active_subscription(self._conn, thread_id)
        if not sub or not sub.get("worker_id") or not sub.get("node_id"):
            return False
        ws = self._sockets.get(str(sub["node_id"]))
        if ws is None:
            return False
        await ws.send_json(
            {
                "t": "dispatch",
                "worker_id": sub["worker_id"],
                "followup": True,
                "ticket": {
                    "id": sub["ticket_id"],
                    "code": sub.get("code") or "FOLLOWUP",
                    "title": str(event.get("text") or "")[:200],
                    "platform": "",
                    "kind": "followup",
                    "severity": "normal",
                    "summary": "",
                    "source_url": "",
                },
            }
        )
        return True

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
