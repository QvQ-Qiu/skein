"""控制面的 HTTP 接口：健康检查、transcript 上传、Outbox 写入。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
import time

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket
from pydantic import BaseModel, Field

from skein import db, prompts, runs
from skein.channel import ChannelHub
from skein.compat import ForumCompatWriter
from skein.host import RouterHost
from skein.outbox import EffectState, EmitResult, Outbox, get
from skein.session_store import SessionStore, TurnConflict
from skein.summary import summary as build_summary


class ChatIn(BaseModel):
    messages: list[dict]


class TurnIn(BaseModel):
    session_id: str
    turn_index: int = Field(ge=0)
    data: str
    head_sha: str | None = None
    branch: str | None = None


class EmitIn(BaseModel):
    key: str
    kind: str
    object: str
    payload: dict = Field(default_factory=dict)


class ReviewIn(BaseModel):
    decision: str


class FeaturedIn(BaseModel):
    post_ids: list[int]


class RunIn(BaseModel):
    job: str
    day: str


class FinishIn(BaseModel):
    status: str
    detail: str | None = None


class PromptProposeIn(BaseModel):
    part: str
    text: str
    base_revision: int
    source: str = "watchdog"
    signal_id: str | None = None


class PromptActionIn(BaseModel):
    part: str


def create_app(
    conn: sqlite3.Connection,
    blob_root: str,
    token: str,
    writer,
    llm=None,
    direct_model: str = "direct",
    route_model: str = "route",
    platform_repos: dict[str, str] | None = None,
    forum_base: str = "",
    internal_token: str = "",
) -> FastAPI:
    app = FastAPI(title="skein-control", version="0.1.0")
    sessions = SessionStore(conn, blob_root)
    outbox = Outbox(conn, writer)
    compat = Outbox(conn, ForumCompatWriter(forum_base)) if forum_base else None
    host = RouterHost(conn, llm, direct_model, route_model) if llm is not None else None
    hub = ChannelHub(conn, token, platform_repos or {})

    def auth(x_skein_token: str = Header(default="")) -> None:
        if not token or x_skein_token != token:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "ok"}

    @app.get("/internal/summary", dependencies=[Depends(auth)])
    def internal_summary() -> dict:
        return build_summary(conn)

    @app.post("/internal/runs", dependencies=[Depends(auth)])
    def start_run(body: RunIn) -> dict:
        run_id = runs.start(conn, body.job, body.day)
        return {"run_id": run_id, "existing": run_id is None}

    @app.post("/internal/runs/{run_id}/finish", dependencies=[Depends(auth)])
    def finish_run(run_id: str, body: FinishIn) -> dict:
        runs.finish(conn, run_id, body.status, body.detail)
        return {"run_id": run_id, "status": body.status}

    @app.get("/internal/runs", dependencies=[Depends(auth)])
    def list_runs(limit: int = 20) -> dict:
        return {
            "runs": [
                {"id": r.id, "job": r.job, "day": r.day, "status": r.status, "detail": r.detail}
                for r in runs.recent(conn, limit)
            ]
        }

    @app.get("/internal/prompts", dependencies=[Depends(auth)])
    def list_prompts() -> dict:
        return {"prompts": [prompts.state(conn, part) for part in prompts.EDITABLE_PARTS]}

    @app.post("/internal/prompts/propose", dependencies=[Depends(auth)])
    def propose_prompt(body: PromptProposeIn) -> dict:
        try:
            revision = prompts.propose(
                conn, body.part, body.text, body.base_revision, body.source, body.signal_id
            )
        except prompts.PromptFrozen as exc:
            raise HTTPException(status_code=423, detail=str(exc))
        except prompts.PromptConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except prompts.PromptError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"revision": revision, "part": body.part}

    @app.post("/internal/prompts/promote", dependencies=[Depends(auth)])
    def promote_prompt(body: PromptActionIn) -> dict:
        try:
            revision = prompts.promote(conn, body.part)
        except prompts.PromptError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"revision": revision, "part": body.part}

    @app.post("/internal/prompts/rollback", dependencies=[Depends(auth)])
    def rollback_prompt(body: PromptActionIn) -> dict:
        prompts.rollback(conn, body.part)
        return prompts.state(conn, body.part)

    @app.post("/internal/prompts/unfreeze", dependencies=[Depends(auth)])
    def unfreeze_prompt(body: PromptActionIn) -> dict:
        prompts.unfreeze(conn, body.part)
        return prompts.state(conn, body.part)

    @app.websocket("/api/agent/channel")
    async def agent_channel(ws: WebSocket) -> None:
        await hub.serve(ws)

    @app.post("/internal/tickets/{ticket_id}/dispatch", dependencies=[Depends(auth)])
    async def dispatch_ticket(ticket_id: str, platform: str | None = None) -> dict:
        return await hub.dispatch_ticket(ticket_id, platform)

    @app.post("/internal/tickets/{ticket_id}/review", dependencies=[Depends(auth)])
    def review_ticket(ticket_id: str, body: ReviewIn) -> dict:
        if body.decision not in ("approve", "reject"):
            raise HTTPException(status_code=400, detail="decision must be approve or reject")
        status = "done" if body.decision == "approve" else "rejected"
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), ticket_id),
        )
        db.record_audit(
            conn, "human", "review", {"ticket_id": ticket_id, "decision": body.decision}
        )
        return {"ticket_id": ticket_id, "status": status}

    @app.post("/internal/featured")
    def compat_featured(body: FeaturedIn, x_internal_token: str = Header(default="")) -> dict:
        if not internal_token or x_internal_token != internal_token:
            raise HTTPException(status_code=403, detail="forbidden")
        if compat is None:
            raise HTTPException(status_code=503, detail="forum not configured")
        marked = 0
        for post_id in body.post_ids:
            key = f"featured:{post_id}"
            existing = get(conn, key)
            if existing and existing.state == EffectState.CONFIRMED:
                marked += 1
                continue
            result = compat.emit(key, "featured", str(post_id), {"token": internal_token})
            if result != EmitResult.FAILED:
                marked += 1
        return {"code": 200, "data": {"marked": marked}}

    @app.post("/api/auth/post")
    def compat_post(body: dict, authorization: str = Header(default="")) -> dict:
        if compat is None:
            raise HTTPException(status_code=503, detail="forum not configured")
        material = json.dumps(
            {k: body.get(k) for k in ("title", "content", "partition", "isPrivate", "userTelephone")},
            sort_keys=True,
            ensure_ascii=False,
        )
        key = "post:" + hashlib.sha256(material.encode()).hexdigest()[:32]
        existing = get(conn, key)
        if existing and existing.state == EffectState.CONFIRMED and existing.response:
            return existing.response
        result = compat.emit(key, "post", key, {"body": body, "authorization": authorization})
        if result == EmitResult.FAILED:
            raise HTTPException(status_code=502, detail="forum write failed")
        stored = get(conn, key)
        return stored.response if stored and stored.response else {"code": 200, "data": None}

    if host is not None:

        @app.post("/internal/chat", dependencies=[Depends(auth)])
        def chat(body: ChatIn) -> dict:
            result = host.chat(body.messages)
            return {"action": result.action, "text": result.text, "ticket_id": result.ticket_id}

    @app.post("/internal/worker/turn", dependencies=[Depends(auth)])
    def upload_turn(body: TurnIn) -> dict:
        try:
            data = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="invalid base64")
        try:
            sessions.open(body.session_id)
            turn = sessions.append_turn(
                body.session_id, body.turn_index, data, body.head_sha, body.branch
            )
        except TurnConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"session_id": turn.session_id, "turn_index": turn.turn_index, "sha256": turn.sha256}

    @app.post("/internal/outbox/emit", dependencies=[Depends(auth)])
    def emit(body: EmitIn) -> dict:
        result = outbox.emit(body.key, body.kind, body.object, body.payload)
        if result == EmitResult.FAILED:
            raise HTTPException(status_code=502, detail="effect failed")
        return {"result": result}

    return app
