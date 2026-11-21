"""控制面的 HTTP 接口：健康检查、transcript 上传、Outbox 写入。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from skein import db, llm_trace, prompts, runs, store, watchdog
from skein.admission import Event, decide, policy_for
from skein.agent import Agent
from skein.channel import ChannelHub
from skein.host import RouterHost
from skein.outbox import EmitResult, Outbox
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

log = logging.getLogger("skein.api")


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


class LLMCallIn(BaseModel):
    session_id: str = ""
    actor: str = ""
    source: str = ""
    model: str = ""
    prompt: str = ""
    response: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    status: str = "ok"
    error: str = ""


class AgentChatIn(BaseModel):
    thread_id: str = ""
    message: str


class IntakeIn(BaseModel):
    source: str
    thread_id: str
    message_id: str = ""
    parent_id: str = ""
    requester: str = ""
    title: str
    summary: str = ""
    category: str = "bug"
    severity: str = "normal"
    visibility: str = "market"
    dedup_key: str = ""


def create_app(
    conn: sqlite3.Connection,
    blob_root: str,
    token: str,
    writer,
    llm=None,
    direct_model: str = "direct",
    route_model: str = "route",
    platform_repos: dict[str, str] | None = None,
    sso_config: SsoConfig | None = None,
    sso_client: Any | None = None,
    qa_chat_upstream: str = "",
    qa_ops_upstream: str = "",
    qa_client: Any | None = None,
    qa_panel_secret: str = "",
    qa_panel_sub: str = "",
    qa_panel_email: str = "skein@ssemarket.cn",
    qa_user_secret: str = "",
    llm_base: str = "",
    llm_key: str = "",
    agent_model: str = "deepseek-v4-flash-vision-exp",
) -> FastAPI:
    app = FastAPI(title="skein-control", version="0.1.0")
    sessions = SessionStore(conn, blob_root)
    outbox = Outbox(conn, writer)
    host = RouterHost(conn, llm, direct_model, route_model) if llm is not None else None
    hub = ChannelHub(conn, token, platform_repos or {})
    agent = (
        Agent(conn, llm_base, llm_key, agent_model, hub)
        if llm_base and llm_key
        else None
    )
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
    if llm is not None:
        llm.on_call = lambda call: llm_trace.record(conn, **call)

    def notify_public(ticket_id: str, status: str) -> None:
        row = conn.execute(
            "SELECT code, origin, origin_id, title, summary, public_status"
            " FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None or not str(row["origin"]).startswith("forum"):
            return
        public = {"working": "working", "done": "fixed", "rejected": "need_info"}.get(status)
        if not public or row["public_status"] == public:
            return
        conn.execute(
            "UPDATE tickets SET public_status = ?, updated_at = ? WHERE id = ?",
            (public, time.time(), ticket_id),
        )
        if not qa.chat_upstream:
            return
        payload = {
            "ticket_id": ticket_id,
            "code": row["code"],
            "public_status": public,
            "origin": row["origin"],
            "origin_id": row["origin_id"],
            "title": row["title"],
            "summary": row["summary"],
        }

        def _send() -> None:
            try:
                request = urllib.request.Request(
                    qa.chat_upstream.rstrip("/") + "/internal/skein/notify",
                    data=json.dumps(payload, ensure_ascii=False).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "X-Skein-Token": token,
                    },
                    method="POST",
                )
                urllib.request.urlopen(request, timeout=3).read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.warning("public notify %s %s failed: %s", ticket_id, public, exc)

        threading.Thread(target=_send, daemon=True).start()

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

    async def admin_principal(
        request: Request, x_skein_token: str = Header(default="")
    ) -> Principal:
        if token and x_skein_token == token:
            raise HTTPException(status_code=403, detail="admin_sso_required")
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
        CURRENT_PRINCIPAL.set(principal)
        return principal

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

    @app.get("/health/ready")
    def ready() -> dict:
        conn.execute("SELECT 1").fetchone()
        return {"status": "ready"}

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

    @app.post("/internal/llm/calls", dependencies=[Depends(auth)])
    def record_llm_call(body: LLMCallIn) -> dict:
        call_id = llm_trace.record(conn, **body.model_dump())
        return {"ok": True, "id": call_id}

    @app.get("/internal/llm/calls", dependencies=[Depends(auth)])
    def list_llm_calls(
        session_id: str | None = None, source: str | None = None, limit: int = 200
    ) -> dict:
        return {
            "calls": llm_trace.calls(
                conn, session_id=session_id, source=source, limit=limit
            )
        }

    @app.get("/internal/llm/sessions", dependencies=[Depends(auth)])
    def list_llm_sessions(limit: int = 100) -> dict:
        return {"sessions": llm_trace.sessions(conn, limit=limit)}

    @app.post("/internal/agent/chat", dependencies=[Depends(admin_principal)])
    async def agent_chat(body: AgentChatIn) -> dict:
        principal = CURRENT_PRINCIPAL.get()
        if principal is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        if agent is None:
            raise HTTPException(status_code=503, detail="agent_not_configured")
        message = body.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="message_required")
        thread_id = body.thread_id.strip()[:64] or (
            f"agent-{principal.sub[:12]}-{int(time.time())}"
        )
        result = await agent.chat(principal, thread_id, message)
        return {"thread_id": thread_id, **result}

    @app.get("/internal/agent/thread/{thread_id}", dependencies=[Depends(admin_principal)])
    async def agent_thread(thread_id: str) -> dict:
        principal = CURRENT_PRINCIPAL.get()
        if principal is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        key = thread_id[:64]
        rows = conn.execute(
            "SELECT role, content, created_at FROM agent_messages"
            " WHERE thread_id = ? ORDER BY rowid",
            (key,),
        ).fetchall()
        return {"thread_id": key, "actor": principal.sub, "messages": [dict(row) for row in rows]}

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

    @app.post("/internal/intake", dependencies=[Depends(auth)])
    def intake(body: IntakeIn) -> dict:
        """前厅（小秋）建单：来源/thread/用户/摘要；dedup 幂等。"""
        title = body.title.strip()[:120]
        if not title:
            raise HTTPException(status_code=400, detail="title required")
        source = body.source.strip() or "forum_post"
        thread_key = body.thread_id.strip()
        if not thread_key:
            raise HTTPException(status_code=400, detail="thread required")
        if source == "forum_ccomment" and (
            not body.parent_id.strip() or not body.message_id.strip()
        ):
            raise HTTPException(status_code=400, detail="parent and message required")
        material = (
            f"{source}|{thread_key}|{body.message_id}|{body.requester}"
            f"|{title}|{body.summary}"
        )
        dedup = body.dedup_key.strip() or (
            "intake:" + hashlib.sha256(material.encode()).hexdigest()[:32]
        )
        existing = store.find_ticket_by_dedup(conn, dedup)
        if existing:
            row = conn.execute(
                "SELECT code, status, public_status FROM tickets WHERE id = ?",
                (existing,),
            ).fetchone()
            return {
                "ticket_id": existing,
                "code": row["code"] if row else "",
                "status": row["status"] if row else "",
                "public_status": row["public_status"] if row else "",
                "created": False,
            }
        thread_origin = "chat" if source == "chat" else "forum"
        thread_id = store.ensure_thread(conn, thread_origin, thread_key)
        if source in ("forum_comment", "forum_ccomment"):
            active = conn.execute(
                "SELECT id, code, status, public_status FROM tickets"
                " WHERE thread_id = ? AND status NOT IN ('done','rejected','cancelled')"
                " ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if active is not None:
                addition = (
                    f"\n\n[followup message={body.message_id or '-'}"
                    f" requester={body.requester}] {body.summary.strip() or title}"
                )
                conn.execute(
                    "UPDATE tickets SET summary = substr(summary || ?, -16000),"
                    " updated_at = ? WHERE id = ?",
                    (addition, time.time(), active["id"]),
                )
                db.record_audit(
                    conn,
                    "intake",
                    "ticket.followup",
                    {
                        "ticket_id": active["id"],
                        "thread": thread_key,
                        "message": body.message_id,
                        "parent": body.parent_id,
                        "requester": body.requester,
                    },
                )
                return {
                    "ticket_id": active["id"],
                    "code": active["code"],
                    "status": active["status"],
                    "public_status": active["public_status"],
                    "created": False,
                    "followup": True,
                }
        origin_id = thread_key
        if source == "forum_comment" and body.message_id:
            origin_id = f"{thread_key}:{body.message_id}"
        elif source == "forum_ccomment" and body.message_id:
            origin_id = f"{thread_key}:{body.parent_id.strip()}:{body.message_id}"
        note = body.summary.strip()
        note += (
            f"\n\n[category={body.category} severity={body.severity}"
            f" visibility={body.visibility} requester={body.requester}]"
        )
        ticket_id = store.create_ticket(
            conn, title, thread_id=thread_id, dedup_key=dedup, summary=note
        )
        conn.execute(
            "UPDATE tickets SET origin = ?, origin_id = ?, requester = ?,"
            " public_status = 'received', updated_at = ? WHERE id = ?",
            (
                source,
                origin_id,
                body.requester,
                time.time(),
                ticket_id,
            ),
        )
        db.record_audit(
            conn,
            "intake",
            "ticket.intake",
            {
                "ticket_id": ticket_id,
                "source": source,
                "thread": thread_key,
                "message": body.message_id,
                "parent": body.parent_id,
                "requester": body.requester,
                "category": body.category,
                "severity": body.severity,
            },
        )
        row = conn.execute(
            "SELECT code, status, public_status FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        return {
            "ticket_id": ticket_id,
            "code": row["code"] if row else "",
            "status": row["status"] if row else "staged",
            "public_status": row["public_status"] if row else "received",
            "created": True,
        }

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
        result = await hub.dispatch_ticket(ticket_id, platform)
        if result.get("ok"):
            notify_public(ticket_id, "working")
        return result

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
            if result.get("ok"):
                notify_public(ticket_id, "working")
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
        notify_public(ticket_id, status)
        return {"ticket_id": ticket_id, "status": status}

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
        result = outbox.emit(body.key, body.kind, body.object, body.payload)
        if result == EmitResult.FAILED:
            raise HTTPException(status_code=502, detail="effect failed")
        return {"result": result}

    console_root = Path(__file__).resolve().parents[3] / "console"
    dist_dir = console_root / "dist"
    if dist_dir.is_dir():
        app.mount(
            "/console",
            StaticFiles(directory=str(dist_dir), html=True),
            name="console",
        )

        @app.get("/")
        def root() -> RedirectResponse:
            return RedirectResponse(url="/console/")

    return app
