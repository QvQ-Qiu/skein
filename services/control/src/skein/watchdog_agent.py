"""Watchdog 独立进程：只观察信号；可选把模型建议写成 Worker 提示词金丝雀。

默认只记录信号，不自动改词。`SKEIN_WATCHDOG_AUTO=1` 才提议，且永远只进金丝雀，
不会自动升格；冻结与乐观锁由 prompts.propose 负责。
"""

from __future__ import annotations

import json
import logging
import time

from skein import db, prompts, store, watchdog
from skein.config import Config
from skein.llm_http import HttpLLM

log = logging.getLogger("skein.watchdog")

WATCHDOG_PROMPT = (
    "你是 Watchdog，只负责观察和提出 Worker 执行提示词的修订建议。"
    "只输出 JSON：{\"text\":\"修订后的提示词\"}。"
    "只能改 Worker 的执行段，不能改准入、Router、工具权限或对外措辞；"
    "改动要小，保留原有结构和 Markdown 标记。"
)


def _seen_key(signal: dict) -> str:
    return f"watchdog:seen:{signal.get('kind')}:{signal.get('id')}"


def _propose(conn, llm, model: str, signal: dict) -> None:
    part = "runtime/worker"
    state = prompts.state(conn, part)
    current_text = prompts.current(conn, part)
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": WATCHDOG_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"signal": signal, "current_prompt": current_text},
                    ensure_ascii=False,
                ),
            },
        ],
        "max_tokens": 1200,
    }
    try:
        raw = llm.complete(request)
        text = raw.get("text") if isinstance(raw, dict) else None
        if not isinstance(text, str) or not text.strip():
            log.warning("watchdog 提议为空，signal=%s", signal.get("id"))
            return
        revision = prompts.propose(
            conn,
            part,
            text.strip(),
            state["stable_revision"],
            "watchdog",
            str(signal.get("id")),
        )
        log.info("watchdog 提议进入金丝雀 revision=%s signal=%s", revision, signal.get("id"))
    except prompts.PromptError as exc:
        log.warning("watchdog 提议被拒绝：%s", exc)
    except Exception:
        log.exception("watchdog 提议失败")


def observe(
    conn,
    *,
    auto_propose: bool = False,
    llm=None,
    model: str = "route",
    now: float | None = None,
) -> list[dict]:
    fresh: list[dict] = []
    for signal in watchdog.signals(conn, now=now):
        key = _seen_key(signal)
        if store.kv_get(conn, key):
            continue
        store.kv_set(conn, key, str(int(time.time())))
        db.record_audit(conn, "watchdog", "signal", signal)
        fresh.append(signal)
        if auto_propose and llm is not None:
            _propose(conn, llm, model, signal)
    return fresh


def run_forever(
    conn,
    *,
    interval: int = 300,
    auto_propose: bool = False,
    llm=None,
    model: str = "route",
) -> None:
    log.info("Watchdog 启动：interval=%ss auto=%s", interval, auto_propose)
    while True:
        try:
            fresh = observe(conn, auto_propose=auto_propose, llm=llm, model=model)
            if fresh:
                log.info("Watchdog 新信号 %s 条", len(fresh))
        except Exception:
            log.exception("Watchdog 轮询失败")
        time.sleep(max(10, interval))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    if not cfg.watchdog:
        log.info("SKEIN_WATCHDOG 未开启")
        return
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    llm = (
        HttpLLM(cfg.llm_base, cfg.llm_key)
        if cfg.watchdog_auto and cfg.llm_base and cfg.llm_key
        else None
    )
    if cfg.watchdog_auto and llm is None:
        log.warning("SKEIN_WATCHDOG_AUTO=1 但没有模型网关，只记录信号")
    run_forever(
        conn,
        interval=cfg.watchdog_interval,
        auto_propose=cfg.watchdog_auto,
        llm=llm,
        model=cfg.route_model,
    )


if __name__ == "__main__":
    main()
