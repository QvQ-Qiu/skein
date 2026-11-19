"""进程内日志环形缓冲：给 /internal/logs 和日志页用。"""

from __future__ import annotations

import logging
import threading
from collections import deque

MAX_RECORDS = 2000

_records: deque[dict] = deque(maxlen=MAX_RECORDS)
_lock = threading.Lock()
_installed = False


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        item = {
            "at": record.created,
            "level": record.levelname.lower(),
            "component": record.name,
            "message": message,
        }
        with _lock:
            _records.append(item)


def install(level: int = logging.INFO) -> None:
    global _installed
    if _installed:
        return
    handler = _RingHandler()
    handler.setLevel(level)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    _installed = True


def recent(limit: int = 300, component: str | None = None, level: str | None = None) -> list[dict]:
    with _lock:
        items = list(_records)
    if component:
        items = [item for item in items if item["component"] == component]
    if level:
        wanted = level.lower()
        items = [item for item in items if item["level"] == wanted]
    return items[-max(0, limit):]
