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
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from skein import db, prompts, runs, store, watchdog
from skein.admission import Event, decide, policy_for
from skein.channel import ChannelHub
from skein.compat import ForumCompatWriter
from skein.host import RouterHost
from skein.outbox import EffectState, EmitResult, Outbox, get
from skein.qa_proxy import QAProxy, sign_user_header
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
    # console=只放 internal=true；market=允许集市用户
    surface: str = "console"


QA_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


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


class ChatTicketIn(BaseModel):
    thread_id: str = ""
    title: str
    summary: str = ""


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
    qa_chat_upstream: str = "",
    qa_ops_upstream: str = "",
    qa_client: Any | None = None,
    qa_panel_secret: str = "",
    qa_panel_sub: str = "",
    qa_panel_email: str = "skein@ssemarket.cn",
    qa_user_secret: str = "",
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
    qa = QAProxy(
        qa_chat_upstream,
        qa_ops_upstream,
        qa_client,
        panel_secret=qa_panel_secret,
        panel_sub=qa_panel_sub,
        panel_email=qa_panel_email,
    )
    login_attempts: dict[str, deque[float]] = defaultdict(deque)
    user_secret = qa_user_secret

    async def auth(
        request: Request, x_skein_token: str = Header(default="")
    ) -> Principal | None:
        # 机器走共享令牌，人走 UniSSO 会话；两者都失败才拒。
        if token and x_skein_token == token:
            return None
        if sso_auth is not None:
            try:
                principal = await asyncio.to_thread(
                    sso_auth.verify, request.cookies, "admin"
                )
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
    async def session_probe(request: Request, surface: str = "console") -> dict:
        # 控制台启动时打这个：决定出登录页还是直接进。sso 没配就声明 token 模式。
        if sso_auth is None or sso_config is None:
            return {"mode": "token"}
        allow_market = surface == "market"
        level = "market" if allow_market else "admin"
        try:
            principal = await asyncio.to_thread(
                sso_auth.verify, request.cookies, level
            )
        except SsoRejected as error:
            raise HTTPException(status_code=error.status, detail=error.code)
        except SsoUnavailable:
            raise HTTPException(status_code=503, detail="sso_unavailable")
        CURRENT_PRINCIPAL.set(principal)
        return {
            "mode": "sso",
            "user": principal.as_dict(),
            "kind": "admin" if principal.admin else "market",
        }

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
        # 控制台只放 unisso is_admin；market 面放集市用户（internal）。
        level = "market" if body.surface == "market" else "admin"
        try:
            sso_auth.check(principal, level)
        except SsoRejected as error:
            raise HTTPException(status_code=error.status, detail=error.code)
        response = JSONResponse(
            {
                "mode": "sso",
                "user": principal.as_dict(),
                "kind": "admin" if principal.admin else "market",
            }
        )
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

    async def internal_only(
        request: Request, x_skein_token: str = Header(default="")
    ) -> None:
        # ops 面只放内部身份：服务令牌，或 SSO 会话且 internal=1。
        if token and x_skein_token == token:
            return
        if sso_auth is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        try:
            principal = await asyncio.to_thread(
                sso_auth.verify, request.cookies, "admin"
            )
        except SsoRejected as error:
            raise HTTPException(status_code=error.status, detail=error.code)
        except SsoUnavailable:
            raise HTTPException(status_code=503, detail="sso_unavailable")
        if not principal.internal:
            raise HTTPException(status_code=403, detail="not_internal")

    async def chat_principal(request: Request) -> Principal | None:
        if sso_auth is None:
            return None
        try:
            return await asyncio.to_thread(
                sso_auth.verify, request.cookies, "market"
            )
        except (SsoRejected, SsoUnavailable):
            return None

    async def chat_identity_headers(request: Request) -> dict[str, str]:
        # 集市用户/管理员都能聊天；SSO 有效就带签名身份，失败回退论坛 JWT。
        if not user_secret:
            return {}
        principal = await chat_principal(request)
        if principal is None:
            return {}
        return {"x-skein-user": sign_user_header(principal, user_secret)}

    async def record_chat_event(request: Request, path: str) -> None:
        # 只接真正发起任务的消息；bootstrap/close/resume 不算。
        if path not in ("task", "task/sse", "sse"):
            return
        principal = await chat_principal(request)
        if principal is None:
            return
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        text = str(
            payload.get("task") or payload.get("message") or payload.get("prompt") or ""
        ).strip()
        if not text:
            return
        session_id = (
            str(payload.get("session_id") or payload.get("thread_id") or "").strip()[:128]
            or f"user:{principal.sub}"
        )
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        event = Event(
            source="chat",
            origin_id=f"{session_id}#{digest}",
            text=text,
            actor=principal.sub,
            mentioned=False,
            in_allowlist=True,
            parent_id=session_id,
        )
        decision = decide(event, policy_for(conn, "chat"))
        thread_origin, thread_key = store.thread_identity(event)
        thread_id = store.ensure_thread(conn, thread_origin, thread_key)
        store.record_event(
            conn,
            event.source,
            event.origin_id,
            thread_id,
            {
                "text": text,
                "actor": principal.sub,
                "session_id": session_id,
                "admin": principal.admin,
            },
            decision.verdict.value,
        )

    @app.post("/api/v1/agent/ticket")
    async def chat_ticket(body: ChatTicketIn, request: Request) -> dict:
        """集市用户从对话里显式转工单；只建 staged，不自动派工。"""
        principal = await chat_principal(request)
        if principal is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        title = body.title.strip()[:120]
        if not title:
            raise HTTPException(status_code=400, detail="title_required")
        summary = body.summary.strip()[:2000]
        thread_key = body.thread_id.strip()[:128] or f"user:{principal.sub}"
        digest = hashlib.sha256(
            f"{principal.sub}|{thread_key}|{title}|{summary}".encode()
        ).hexdigest()[:32]
        dedup_key = f"chat:{digest}"
        existing = store.find_ticket_by_dedup(conn, dedup_key)
        if existing:
            row = conn.execute(
                "SELECT code, status FROM tickets WHERE id = ?", (existing,)
            ).fetchone()
            return {
                "ticket_id": existing,
                "code": row["code"] if row else "",
                "status": row["status"] if row else "",
                "existing": True,
            }
        event = Event(
            source="chat",
            origin_id=thread_key,
            text=summary or title,
            actor=principal.sub,
            parent_id=thread_key,
        )
        thread_origin, thread_id_key = store.thread_identity(event)
        thread_id = store.ensure_thread(conn, thread_origin, thread_id_key)
        note = f"{summary}\n\n[chat] user={principal.sub} thread={thread_key}".strip()
        ticket_id = store.create_ticket(
            conn, title, thread_id=thread_id, dedup_key=dedup_key, summary=note
        )
        db.record_audit(
            conn,
            principal.label,
            "chat.ticket",
            {"ticket_id": ticket_id, "thread_id": thread_key},
        )
        row = conn.execute(
            "SELECT code, status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        return {
            "ticket_id": ticket_id,
            "code": row["code"] if row else "",
            "status": row["status"] if row else "",
            "existing": False,
        }

    @app.api_route("/api/v1/agent/{path:path}", methods=QA_METHODS)
    async def qa_chat_agent(path: str, request: Request) -> StreamingResponse:
        """小秋 chat 面：集市用户/管理员都可；身份头优先，JWT 兜底。"""
        if not qa.enabled_chat:
            raise HTTPException(status_code=503, detail="qa_not_configured")
        await record_chat_event(request, path)
        return await qa.forward(
            request,
            qa.chat_upstream,
            f"api/v1/agent/{path}",
            await chat_identity_headers(request),
        )

    @app.api_route("/task/{path:path}", methods=QA_METHODS)
    async def qa_chat_task(path: str, request: Request) -> StreamingResponse:
        if not qa.enabled_chat:
            raise HTTPException(status_code=503, detail="qa_not_configured")
        await record_chat_event(request, path)
        return await qa.forward(
            request,
            qa.chat_upstream,
            f"task/{path}",
            await chat_identity_headers(request),
        )

    @app.api_route("/ops/{path:path}", methods=QA_METHODS)
    async def qa_ops(path: str, request: Request) -> StreamingResponse:
        """小秋 ops 面：只有内部身份能到；公网用户拿不到这条路径。"""
        await internal_only(request, request.headers.get("x-skein-token", ""))
        if not qa.enabled_ops:
            raise HTTPException(status_code=503, detail="qa_not_configured")
        return await qa.forward(request, qa.ops_upstream, f"ops/{path}")

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
