"""Router 宿主：聊天直答、事件分派。两条入口分开，工具集不同。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from skein import db, store
from skein.admission import DEFAULT_POLICY, Event, SourcePolicy, Verdict, decide
from skein.llm import LLMClient
from skein.router.direct import DISPATCH_RESULT_TEXT, DirectRouter
from skein.router.dispatch import ROUTE_SYSTEM_PROMPT, build_route_request, parse_route_response


@dataclass(frozen=True)
class HostResult:
    action: str
    text: str | None = None
    ticket_id: str | None = None


class RouterHost:
    def __init__(
        self,
        conn: sqlite3.Connection,
        llm: LLMClient,
        direct_model: str,
        route_model: str,
        policy: SourcePolicy = DEFAULT_POLICY,
    ) -> None:
        self._conn = conn
        self._llm = llm
        self._direct = DirectRouter(llm, direct_model)
        self._route_model = route_model
        self._policy = policy

    def chat(self, messages: list[dict]) -> HostResult:
        reply = self._direct.reply(messages)
        if reply.action == "say":
            return HostResult("say", reply.text)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            ticket_id = store.create_ticket(self._conn, title=self._title(messages))
            db.record_audit(
                self._conn, "router", "dispatch", {"ticket_id": ticket_id, "from": "chat"}
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return HostResult("dispatch", DISPATCH_RESULT_TEXT, ticket_id)

    def on_event(self, event: Event) -> HostResult:
        if store.event_seen(self._conn, event.source, event.origin_id):
            return HostResult("duplicate")

        decision = decide(event, self._policy)
        thread_origin, thread_key = store.thread_identity(event)
        thread_id = store.ensure_thread(self._conn, thread_origin, thread_key)
        store.record_event(
            self._conn,
            event.source,
            event.origin_id,
            thread_id,
            {"text": event.text, "actor": event.actor},
            decision.verdict.value,
        )

        if decision.verdict is not Verdict.PROCESS:
            return HostResult(decision.verdict.value)

        return self.handle_admitted(event, thread_id)

    def handle_admitted(self, event: Event, thread_id: str) -> HostResult:
        """事件已被准入放行、并已落库；这里只做判定与建单。"""
        raw = self._llm.complete(
            build_route_request(
                self._route_model,
                [
                    {"role": "system", "content": ROUTE_SYSTEM_PROMPT},
                    {"role": "user", "content": event.text},
                ],
            )
        )
        route = parse_route_response(raw)
        if route.action != "route":
            return HostResult(route.action)

        dedup_key = f"{event.source}:{event.origin_id}"
        existing = store.find_ticket_by_dedup(self._conn, dedup_key)
        if existing:
            return HostResult("attached", ticket_id=existing)

        ticket_id = store.create_ticket(
            self._conn,
            title=event.text[:60],
            thread_id=thread_id,
            dedup_key=dedup_key,
        )
        store.subscribe(self._conn, thread_id, ticket_id)
        db.record_audit(
            self._conn,
            "router",
            "dispatch",
            {"ticket_id": ticket_id, "from": event.source, "origin_id": event.origin_id},
        )
        return HostResult("dispatch", ticket_id=ticket_id)

    @staticmethod
    def _title(messages: list[dict]) -> str:
        for message in reversed(messages):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()[:60]
        return "聊天分派"


class DispatchHandler:
    """把采集的 on_process 接到宿主：事件已入库，这里只做判定与建单。"""

    def __init__(self, host: RouterHost) -> None:
        self._host = host

    def __call__(self, event: Event, thread_id: str) -> HostResult:
        return self._host.handle_admitted(event, thread_id)
