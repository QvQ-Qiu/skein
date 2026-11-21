"""进程入口：python -m skein"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import uvicorn

from skein import db, logbuf
from skein.api import create_app
from skein.channels import HttpWriter
from skein.config import Config
from skein.driver import Driver, parse_schedules
from skein.llm_http import HttpLLM
from skein.sso import SsoConfig


def build() -> tuple:
    cfg = Config.from_env()
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.blob_root).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.blob_root).mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    writer = HttpWriter(cfg.forum_base, cfg.forum_token)
    llm = HttpLLM(cfg.llm_base, cfg.llm_key) if cfg.llm_base and cfg.llm_key else None
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
        writer,
        llm=llm,
        direct_model=cfg.direct_model,
        route_model=cfg.route_model,
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


def main() -> None:
    logbuf.install()
    app, cfg, conn = build()
    logging.getLogger("skein").info(
        "control plane started db=%s port=%s", cfg.db_path, cfg.port
    )
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
