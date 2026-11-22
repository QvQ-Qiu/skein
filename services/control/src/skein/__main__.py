"""进程入口：python -m skein"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import uvicorn

from skein import db, logbuf, scheduler
from skein.api import create_app
from skein.config import Config
from skein.driver import Driver, parse_schedules
from skein.sso import SsoConfig


def build() -> tuple:
    cfg = Config.from_env()
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.blob_root).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.blob_root).mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    sso = SsoConfig(
        base=cfg.sso_base,
        client_id=cfg.sso_client_id,
        client_secret=cfg.sso_client_secret,
        cookie_domain=cfg.sso_cookie_domain,
    )
    app = create_app(
        conn,
        cfg.blob_root,
        cfg.token,
        platform_repos=cfg.platform_repos,
        sso_config=sso,
        qa_chat_upstream=cfg.qa_chat_upstream,
        qa_ops_upstream=cfg.qa_ops_upstream,
        qa_panel_secret=cfg.ops_panel_secret,
        qa_panel_sub=cfg.ops_panel_sub,
        qa_panel_email=cfg.ops_panel_email,
        qa_user_secret=cfg.assertion_key,
        llm_base=cfg.llm_base,
        llm_key=cfg.llm_key,
        agent_model=cfg.agent_model,
    )
    return app, cfg, conn


def reap_forever(conn, older_than_s: float, interval: float = 60.0) -> None:
    """控制面自愈：收尾长时间没有进展的派单，释放节点容量。"""
    log = logging.getLogger("skein.reaper")
    while True:
        try:
            expired = scheduler.expire_stale_dispatches(conn, older_than_s)
            if expired:
                log.warning("收尾超时派单 %s 条：%s", len(expired), expired)
        except Exception:
            log.exception("收尾超时派单失败")
        time.sleep(interval)


def main() -> None:
    logbuf.install()
    app, cfg, conn = build()
    logging.getLogger("skein").info(
        "control plane started db=%s port=%s", cfg.db_path, cfg.port
    )
    if cfg.dispatch_stale > 0:
        threading.Thread(
            target=reap_forever, args=(conn, cfg.dispatch_stale), daemon=True
        ).start()
    schedules_raw = cfg.schedules
    commands_raw = cfg.job_commands
    if cfg.jobs_file and Path(cfg.jobs_file).is_file():
        data = json.loads(Path(cfg.jobs_file).read_text(encoding="utf-8"))
        schedules_raw = json.dumps(data.get("schedules") or [])
        commands_raw = json.dumps(data.get("commands") or {})
        logging.getLogger("skein").info("作业配置来自 %s", cfg.jobs_file)
    if cfg.driver_enabled and schedules_raw:
        schedules = parse_schedules(schedules_raw)
        try:
            commands = json.loads(commands_raw or "{}")
        except json.JSONDecodeError:
            commands = {}
        if schedules:
            driver = Driver(conn, schedules, commands, timeout=cfg.job_timeout)
            threading.Thread(target=driver.run_forever, daemon=True).start()
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
