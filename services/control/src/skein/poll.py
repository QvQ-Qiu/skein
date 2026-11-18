"""采集循环：python -m skein.poll"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from skein import db
from skein.config import Config
from skein.forum_source import ForumSource, make_mysql_query
from skein.host import DispatchHandler, RouterHost
from skein.ingest import Ingestor
from skein.llm_http import HttpLLM
from skein.shadow import ShadowDispatcher

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
    handler = None
    llm = HttpLLM(cfg.llm_base, cfg.llm_key) if cfg.llm_base and cfg.llm_key else None
    if cfg.dispatch and llm is not None:
        handler = DispatchHandler(RouterHost(conn, llm, cfg.direct_model, cfg.route_model))
        log.info("真派单已开启：放行事件会建单")
    elif cfg.shadow:
        if llm is not None:
            handler = ShadowDispatcher(conn, llm, cfg.route_model)
            log.info("影子派单已开启，只记录判断，不建单")
        else:
            log.warning("SKEIN_SHADOW 已开但没有配置模型网关，影子派单不生效")
    return Ingestor(conn, ForumSource(query), on_process=handler)


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
        except Exception:
            log.exception("采集失败，下一轮重试")
        time.sleep(cfg.poll_interval)


if __name__ == "__main__":
    main()
