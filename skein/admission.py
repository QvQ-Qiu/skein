"""程序层准入：确定性规则，不调用模型。

结果是三档：忽略、只记账、处理。处理分两种来源——明确点名，
或允许名单内的人回复了 Agent 自己的上一条。后者是推断，不扩大范围。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    IGNORE = "ignore"
    RECORD = "record"
    PROCESS = "process"


@dataclass(frozen=True)
class Event:
    source: str
    origin_id: str
    text: str = ""
    actor: str | None = None
    mentioned: bool = False
    in_allowlist: bool = False
    replies_to_agent: bool = False


@dataclass(frozen=True)
class SourcePolicy:
    accept: bool = True
    require_mention: bool = True
    record_unmentioned: bool = True
    min_text_chars: int = 2


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str


DEFAULT_POLICY = SourcePolicy()


def decide(event: Event, policy: SourcePolicy = DEFAULT_POLICY) -> Decision:
    if not policy.accept:
        return Decision(Verdict.IGNORE, "source_disabled")

    text_len = len(event.text.strip())

    if event.mentioned:
        if text_len < policy.min_text_chars:
            return Decision(Verdict.RECORD, "mention_too_short")
        return Decision(Verdict.PROCESS, "explicit_mention")

    if event.replies_to_agent and event.in_allowlist:
        if text_len < policy.min_text_chars:
            return Decision(Verdict.RECORD, "reply_too_short")
        return Decision(Verdict.PROCESS, "allowed_followup")

    if event.in_allowlist:
        if policy.record_unmentioned:
            return Decision(Verdict.RECORD, "allowlisted_unmentioned")
        return Decision(Verdict.IGNORE, "unmentioned_not_recorded")

    return Decision(Verdict.IGNORE, "not_allowlisted")
