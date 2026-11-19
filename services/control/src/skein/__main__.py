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


def build() -> tuple:
    cfg = Config.from_env()
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.blob_root).mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    writer = HttpWriter(cfg.forum_base, cfg.forum_token)
    llm = HttpLLM(cfg.llm_base, cfg.llm_key) if cfg.llm_base and cfg.llm_key else None
    app = create_app(
        conn,
        cfg.blob_root,
        cfg.token,
        writer,
        llm=llm,
        direct_model=cfg.direct_model,
        route_model=cfg.route_model,
        forum_base=cfg.forum_base,
        internal_token=cfg.forum_token,
        platform_repos=cfg.platform_repos,
    )
    return app, cfg, conn


def main() -> None:
    logbuf.install()
    app, cfg, conn = build()
    logging.getLogger("skein").info(
        "control plane started db=%s port=%s", cfg.db_path, cfg.port
    )
    if cfg.driver_enabled and cfg.schedules:
        schedules = parse_schedules(cfg.schedules)
        try:
            commands = json.loads(cfg.job_commands or "{}")
        except json.JSONDecodeError:
            commands = {}
        if schedules:
            driver = Driver(conn, schedules, commands, timeout=cfg.job_timeout)
            threading.Thread(target=driver.run_forever, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=cfg.port)


if __name__ == "__main__":
    main()
