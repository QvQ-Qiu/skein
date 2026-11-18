"""Router 宿主：聊天直答、事件分派。两条入口分开，工具集不同。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from skein import db, store
from skein.admission import DEFAULT_POLICY, Event, SourcePolicy, Verdict, decide
from skein.llm import LLMClient
from skein.router.direct import DISPATCH_RESULT_TEXT, DirectRouter
from skein.router.dispatch import build_route_request, parse_route_response


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
        ticket_id = store.create_ticket(self._conn, title=self._title(messages))
        db.record_audit(self._conn, "router", "dispatch", {"ticket_id": ticket_id, "from": "chat"})
        return HostResult("dispatch", DISPATCH_RESULT_TEXT, ticket_id)

    def on_event(self, event: Event) -> HostResult:
        if store.event_seen(self._conn, event.source, event.origin_id):
            return HostResult("duplicate")

        decision = decide(event, self._policy)
        thread_id = store.ensure_thread(self._conn, event.source, self._origin_thread(event))
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

        raw = self._llm.complete(
            build_route_request(
                self._route_model,
                [{"role": "user", "content": event.text}],
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
        return HostResult("dispatch", ticket_id=ticket_id)

    @staticmethod
    def _title(messages: list[dict]) -> str:
        for message in reversed(messages):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()[:60]
        return "聊天分派"

    @staticmethod
    def _origin_thread(event: Event) -> str:
        return event.origin_id
