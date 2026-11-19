"""上下文注入：Inline 摘要，超出上限的部分 Spill 到窗口外。"""

from __future__ import annotations

import re

from skein.admission import Event

MAX_INLINE_CHARS = 200
MAX_SPILL_CHARS = 800

_WHITESPACE = re.compile(r"\s+")


def compact(text: str) -> str:
    return _WHITESPACE.sub(" ", text or "").strip()


def inline_text(text: str, limit: int = MAX_INLINE_CHARS) -> str:
    value = compact(text)
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


def spill_text(text: str, limit: int = MAX_SPILL_CHARS) -> tuple[str, str]:
    """返回 (窗口内摘要, 窗口外剩余)；剩余为空表示没有 Spill。"""
    value = compact(text)
    if len(value) <= limit:
        return value, ""
    return value[:limit].rstrip() + "…", value[limit:]


def event_context(
    event: Event,
    index: int,
    thread_id: str,
    limit: int = MAX_INLINE_CHARS,
) -> dict:
    """事件进入 Router 上下文时的最小表示；完整正文留在领域库。"""
    return {
        "index": index,
        "source": event.source,
        "thread_id": thread_id,
        "text": inline_text(event.text, limit),
        "text_chars": len(compact(event.text)),
        "mentioned": event.mentioned,
        "parent_id": event.parent_id,
    }
