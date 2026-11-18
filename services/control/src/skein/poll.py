"""采集循环：python -m skein.poll"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from skein import db
from skein.config import Config
from skein.forum_source import ForumSource, make_mysql_query
from skein.ingest import Ingestor

log = logging.getLogger("skein.poll")


def build(cfg: Config) -> Ingestor:
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    query = make_mysql_query(
        cfg.forum_db_host,
        cfg.forum_db_port,
        cfg.forum_db_user,
        cfg.forum_db_password,
        cfg.forum_db_name,
    )
    return Ingestor(conn, ForumSource(query))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    if not cfg.forum_db_user:
        log.error("SKEIN_FORUM_DB_USER 未配置，采集不启动")
        return
    ingestor = build(cfg)
    log.info("采集启动，间隔 %ss", cfg.poll_interval)
    while True:
        try:
            written = ingestor.poll()
            if written:
                log.info("写入 %s 条事件", written)
        except Exception:  # noqa: BLE001 - 采集失败不应退出循环
            log.exception("采集失败，下一轮重试")
        time.sleep(cfg.poll_interval)


if __name__ == "__main__":
    main()
