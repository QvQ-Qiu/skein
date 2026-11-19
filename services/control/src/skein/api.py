"""控制面的 HTTP 接口：健康检查、transcript 上传、Outbox 写入。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from skein import db, prompts, runs, store, watchdog
from skein.admission import Event, decide
from skein.channel import ChannelHub
from skein.compat import ForumCompatWriter
from skein.host import RouterHost
from skein.outbox import EffectState, EmitResult, Outbox, get
from skein.session_store import SessionStore, TurnConflict
from skein.sso import (
    COOKIE_NAMES,
    CURRENT_PRINCIPAL,
    Principal,
    SsoAuth,
    SsoClient,
    SsoConfig,
    SsoRejected,
    SsoUnavailable,
    cookie_delete_domain,
    cookie_domain_for,
    principal_from,
)
from skein.summary import summary as build_summary
from skein.ui_api import create_ui_router, ticket_rows


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


class FollowupIn(BaseModel):
    thread_id: str
    source: str = ""
    origin_id: str = ""
    text: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class TriggerIn(BaseModel):
    source: str
    origin_id: str
    text: str = ""
    actor: str | None = None
    mentioned: bool = False
    parent_id: str | None = None


class TicketIn(BaseModel):
    title: str
    summary: str = ""
    platform: str = ""
    dedup_key: str = ""
    dispatch: bool = False


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
    sso_config: SsoConfig | None = None,
    sso_client: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="skein-control", version="0.1.0")
    sessions = SessionStore(conn, blob_root)
    outbox = Outbox(conn, writer)
    compat = Outbox(conn, ForumCompatWriter(forum_base)) if forum_base else None
    host = RouterHost(conn, llm, direct_model, route_model) if llm is not None else None
    hub = ChannelHub(conn, token, platform_repos or {})
    sso_auth: SsoAuth | None = None
    if sso_config is not None and sso_config.enabled:
        if sso_client is None:
            sso_client = SsoClient(sso_config)
        sso_auth = SsoAuth(sso_client, sso_config)
    login_attempts: dict[str, deque[float]] = defaultdict(deque)

    async def auth(
        request: Request, x_skein_token: str = Header(default="")
    ) -> Principal | None:
        # 机器走共享令牌，人走 UniSSO 会话；两者都失败才拒。
        if token and x_skein_token == token:
            return None
        if sso_auth is not None:
            try:
                principal = await asyncio.to_thread(sso_auth.verify, request.cookies)
            except SsoRejected as error:
                raise HTTPException(status_code=error.status, detail=error.code)
            except SsoUnavailable:
                raise HTTPException(status_code=503, detail="sso_unavailable")
            CURRENT_PRINCIPAL.set(principal)
            return principal
        raise HTTPException(status_code=401, detail="unauthorized")

    def _login_allowed(ip: str) -> bool:
        now = time.time()
        window = login_attempts[ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= 10:
            return False
        window.append(now)
        return True

    app.include_router(create_ui_router(conn, blob_root, auth))

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "ok"}

    @app.get("/api/session")
    async def session_probe(request: Request) -> dict:
        # 控制台启动时打这个：决定出登录页还是直接进。sso 没配就声明 token 模式。
        if sso_auth is None or sso_config is None:
            return {"mode": "token"}
        try:
            principal = await asyncio.to_thread(sso_auth.verify, request.cookies)
        except SsoRejected as error:
            raise HTTPException(status_code=error.status, detail=error.code)
        except SsoUnavailable:
            raise HTTPException(status_code=503, detail="sso_unavailable")
        CURRENT_PRINCIPAL.set(principal)
        return {"mode": "sso", "user": principal.as_dict()}

    @app.post("/api/session/login")
    async def session_login(request: Request, body: LoginIn) -> JSONResponse:
        if sso_auth is None or sso_config is None or sso_client is None:
            raise HTTPException(status_code=404, detail="sso_not_configured")
        client_ip = request.client.host if request.client else "?"
        if not _login_allowed(client_ip):
            raise HTTPException(status_code=429, detail="too_many_attempts")
        try:
            payload = await asyncio.to_thread(
                sso_client.password_verify, body.email.strip(), body.password
            )
        except SsoRejected as error:
            raise HTTPException(status_code=error.status, detail=error.code)
        except SsoUnavailable:
            raise HTTPException(status_code=503, detail="sso_unavailable")
        principal = principal_from(payload)
        if principal is None:
            raise HTTPException(status_code=502, detail="sso_bad_response")
        if sso_config.require_internal and not principal.internal:
            raise HTTPException(status_code=403, detail="not_internal")
        response = JSONResponse({"mode": "sso", "user": principal.as_dict()})
        session = str(payload.get("sso_session") or "")
        if session:
            response.set_cookie(
                key=str(payload.get("sso_cookie_name") or "unisso_sid"),
                value=session,
                domain=cookie_domain_for(
                    sso_config, str(payload.get("sso_cookie_domain") or "") or None
                ),
                max_age=int(payload.get("sso_max_age") or 604800),
                path="/",
                httponly=True,
                secure=True,
                samesite="lax",
            )
        CURRENT_PRINCIPAL.set(principal)
        return response

    @app.post("/api/session/logout")
    async def session_logout(request: Request) -> JSONResponse:
        if sso_auth is not None and sso_client is not None and sso_config is not None:
            session = SsoAuth.cookie_value(request.cookies)
            if session:
                try:
                    await asyncio.to_thread(sso_client.session_revoke, session)
                except (SsoUnavailable, SsoRejected):
                    pass  # unisso 不可达也要把本地清干净
                sso_auth.invalidate(session)
        response = JSONResponse({"ok": True})
        domain = (
            cookie_delete_domain(sso_config) if sso_config is not None else ".ssemarket.cn"
        )
        for name in COOKIE_NAMES:
            response.delete_cookie(key=name, domain=domain, path="/")
        return response

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

    @app.get("/internal/signals", dependencies=[Depends(auth)])
    def list_signals() -> dict:
        return {"signals": watchdog.signals(conn)}

    @app.get("/internal/tickets", dependencies=[Depends(auth)])
    def list_tickets(status: str | None = None, limit: int = 100) -> dict:
        return {"tickets": ticket_rows(conn, status=status, limit=limit)}

    @app.get("/internal/nodes", dependencies=[Depends(auth)])
    def list_nodes() -> dict:
        rows = conn.execute(
            "SELECT id, name, host, platforms, status, max_workers, last_seen FROM nodes"
            " ORDER BY id"
        ).fetchall()
        return {
            "nodes": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "host": r["host"],
                    "platforms": r["platforms"],
                    "status": r["status"],
                    "max_workers": r["max_workers"],
                    "last_seen": r["last_seen"],
                }
                for r in rows
            ]
        }

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

    @app.post("/internal/events/followup", dependencies=[Depends(auth)])
    async def followup(body: FollowupIn) -> dict:
        forwarded = await hub.followup(body.thread_id, body.model_dump())
        return {"forwarded": forwarded}

    @app.post("/internal/tickets/{ticket_id}/cancel", dependencies=[Depends(auth)])
    async def cancel_ticket(ticket_id: str) -> dict:
        if not await hub.cancel_ticket(ticket_id):
            raise HTTPException(status_code=409, detail="no_live_dispatch")
        return {"cancelled": True}

    @app.websocket("/api/agent/channel")
    async def agent_channel(ws: WebSocket) -> None:
        await hub.serve(ws)

    @app.post("/internal/tickets/{ticket_id}/dispatch", dependencies=[Depends(auth)])
    async def dispatch_ticket(ticket_id: str, platform: str | None = None) -> dict:
        return await hub.dispatch_ticket(ticket_id, platform)

    @app.post("/internal/tickets", dependencies=[Depends(auth)])
    async def create_ticket(body: TicketIn) -> dict:
        """外部控制回路（Watchdog、巡检）建单：dedup_key 幂等，可选直接派。"""
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="title required")
        if body.dedup_key:
            existing = store.find_ticket_by_dedup(conn, body.dedup_key)
            if existing:
                return {"ticket_id": existing, "created": False, "dispatch": None}
        ticket_id = store.create_ticket(
            conn, title, None, body.dedup_key or None, body.summary.strip()
        )
        db.record_audit(
            conn,
            "auto",
            "ticket",
            {"ticket_id": ticket_id, "title": title[:120], "dedup_key": body.dedup_key},
        )
        result = None
        if body.dispatch and body.platform:
            result = await hub.dispatch_ticket(ticket_id, body.platform)
        return {"ticket_id": ticket_id, "created": True, "dispatch": result}

    @app.post("/internal/tickets/{ticket_id}/review", dependencies=[Depends(auth)])
    def review_ticket(ticket_id: str, body: ReviewIn) -> dict:
        if body.decision not in ("approve", "reject"):
            raise HTTPException(status_code=400, detail="decision must be approve or reject")
        status = "done" if body.decision == "approve" else "rejected"
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), ticket_id),
        )
        principal = CURRENT_PRINCIPAL.get()
        db.record_audit(
            conn,
            principal.label if principal else "human",
            "review",
            {"ticket_id": ticket_id, "decision": body.decision},
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

    @app.post("/internal/trigger", dependencies=[Depends(auth)])
    def trigger(body: TriggerIn) -> dict:
        event = Event(
            source=body.source,
            origin_id=body.origin_id,
            text=body.text,
            actor=body.actor,
            mentioned=body.mentioned,
            parent_id=body.parent_id,
        )
        if host is not None:
            result = host.on_event(event)
            return {"action": result.action, "ticket_id": result.ticket_id}
        decision = decide(event)
        thread_origin, thread_key = store.thread_identity(event)
        thread_id = store.ensure_thread(conn, thread_origin, thread_key)
        store.record_event(
            conn,
            event.source,
            event.origin_id,
            thread_id,
            {"text": event.text, "actor": event.actor},
            decision.verdict.value,
        )
        return {"action": decision.verdict.value, "thread_id": thread_id}

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
        if not forum_base:
            raise HTTPException(status_code=503, detail="forum not configured")
        result = outbox.emit(body.key, body.kind, body.object, body.payload)
        if result == EmitResult.FAILED:
            raise HTTPException(status_code=502, detail="effect failed")
        return {"result": result}

    console_root = Path(__file__).resolve().parents[3] / "console"
    legacy_dir = console_root / "legacy"
    dist_dir = console_root / "dist"
    # 先挂 /console-legacy，再挂 /console，避免较短前缀截获旧页。
    if legacy_dir.is_dir():
        app.mount(
            "/console-legacy",
            StaticFiles(directory=str(legacy_dir), html=True),
            name="console-legacy",
        )
    console_dir = dist_dir if dist_dir.is_dir() else legacy_dir
    if console_dir.is_dir():
        app.mount(
            "/console",
            StaticFiles(directory=str(console_dir), html=True),
            name="console",
        )

        @app.get("/")
        def root() -> RedirectResponse:
            return RedirectResponse(url="/console/")

    return app
