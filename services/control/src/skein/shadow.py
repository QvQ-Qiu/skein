"""影子派单：用真实模型做判断，但不建单、不派工、不写论坛。

目的只有一个：把「模型会怎么判」记下来，和人工判断对比。所有副作用都被去掉。
"""

from __future__ import annotations

import sqlite3
import time

from skein.admission import Event
from skein.llm import LLMClient
from skein.router.dispatch import ROUTE_SYSTEM_PROMPT, build_route_request, parse_route_response


class ShadowDispatcher:
    def __init__(self, conn: sqlite3.Connection, llm: LLMClient, route_model: str) -> None:
        self._conn = conn
        self._llm = llm
        self._model = route_model

    def __call__(self, event: Event) -> str:
        raw = self._llm.complete(
            build_route_request(
                self._model,
                [
                    {"role": "system", "content": ROUTE_SYSTEM_PROMPT},
                    {"role": "user", "content": event.text},
                ],
            )
        )
        decision = parse_route_response(raw)
        self._conn.execute(
            "INSERT INTO shadow_decisions (source, origin_id, action, note, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (event.source, event.origin_id, decision.action, decision.note, time.time()),
        )
        return decision.action
