"""常驻 Router 会话：有界历史 + 待处理 Trigger 队列。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from skein.admission import Event

MAX_HISTORY = 16
MAX_PENDING = 200


@dataclass(frozen=True)
class PendingTrigger:
    event: Event
    thread_id: str
    enqueued_at: float


class RouterSession:
    def __init__(
        self,
        max_history: int = MAX_HISTORY,
        max_pending: int = MAX_PENDING,
    ) -> None:
        self._history: deque[dict] = deque(maxlen=max_history)
        self._pending: deque[PendingTrigger] = deque()
        self._max_pending = max_pending
        self._dropped = 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def dropped_count(self) -> int:
        return self._dropped

    def submit(self, event: Event, thread_id: str) -> bool:
        """入队；队列满时丢最旧的一条，返回是否发生了丢弃。"""
        dropped = False
        if len(self._pending) >= self._max_pending:
            self._pending.popleft()
            self._dropped += 1
            dropped = True
        self._pending.append(PendingTrigger(event, thread_id, time.time()))
        return dropped

    def drain(self, max_batch: int) -> list[PendingTrigger]:
        if max_batch <= 0:
            return []
        batch: list[PendingTrigger] = []
        while self._pending and len(batch) < max_batch:
            batch.append(self._pending.popleft())
        return batch

    def remember(self, role: str, content: str) -> None:
        text = content.strip()
        if text:
            self._history.append({"role": role, "content": text})

    def remember_batch(self, triggers: list[PendingTrigger], actions: list[str]) -> None:
        events = "；".join(item.event.text[:60] for item in triggers if item.event.text)
        if events:
            self.remember("user", f"事件批次：{events}")
        summary = "，".join(f"{index}:{action}" for index, action in enumerate(actions))
        self.remember("assistant", f"决策：{summary}" if summary else "决策：无")

    def history(self) -> list[dict]:
        return list(self._history)
