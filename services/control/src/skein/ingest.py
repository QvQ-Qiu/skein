"""采集：把外部来源变成 Trigger，先记账，不派单。

水位按来源存一行，重复与回看靠 events 的唯一键兜底。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from skein import db, store
from skein.admission import DEFAULT_POLICY, Event, SourcePolicy, decide


@dataclass(frozen=True)
class RawItem:
    source: str
    origin_id: str
    text: str
    created_at: float
    parent_id: str | None = None
    actor: str | None = None
    mentioned: bool = False


class Source(Protocol):
    name: str

    def fetch(self, since: float | None, limit: int) -> list[RawItem]:
        ...


class Ingestor:
    def __init__(
        self,
        conn: sqlite3.Connection,
        source: Source,
        policy: SourcePolicy = DEFAULT_POLICY,
    ) -> None:
        self._conn = conn
        self._source = source
        self._policy = policy

    def poll(self, limit: int = 100) -> int:
        key = f"wm:{self._source.name}"
        raw = store.kv_get(self._conn, key)
        since = float(raw) if raw else None
        items = self._source.fetch(since, limit)

        written = 0
        latest = since or 0.0
        for item in items:
            latest = max(latest, item.created_at)
            if store.event_seen(self._conn, item.source, item.origin_id):
                continue
            event = Event(
                source=item.source,
                origin_id=item.origin_id,
                text=item.text,
                actor=item.actor,
                mentioned=item.mentioned,
                parent_id=item.parent_id,
            )
            decision = decide(event, self._policy)
            thread_origin, thread_key = store.thread_identity(event)
            thread_id = store.ensure_thread(self._conn, thread_origin, thread_key)
            store.record_event(
                self._conn,
                item.source,
                item.origin_id,
                thread_id,
                {"text": item.text, "actor": item.actor, "parent_id": item.parent_id},
                decision.verdict.value,
            )
            written += 1

        if items:
            store.kv_set(self._conn, key, str(latest))
            db.record_audit(
                self._conn, "ingest", "poll", {"source": self._source.name, "written": written}
            )
        return written
