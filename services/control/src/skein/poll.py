"""采集循环：python -m skein.poll"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from skein import db, store
from skein.config import Config
from skein.forum_source import ForumSource, make_mysql_query
from skein.host import DispatchHandler, RouterHost
from skein.ingest import Ingestor
from skein.llm_http import HttpLLM
from skein.router.runtime import RouterRuntime
from skein.router.session import RouterSession
from skein.shadow import ShadowDispatcher

log = logging.getLogger("skein.poll")


class SubmitHandler:
    """把事件提交给常驻 Router 队列，不在采集线程里调用模型。"""

    def __init__(self, runtime: RouterRuntime) -> None:
        self._runtime = runtime

    def __call__(self, event, thread_id: str) -> None:
        self._runtime.submit(event, thread_id)


class SubscriptionForwarder:
    """同一 Thread 已有在途 worker 时直通控制面；失败回退到原 handler。"""

    def __init__(self, conn, control_url: str, token: str, fallback) -> None:
        self._conn = conn
        self._url = control_url.rstrip("/")
        self._token = token
        self._fallback = fallback

    def __call__(self, event, thread_id: str) -> None:
        sub = store.active_subscription(self._conn, thread_id)
        if sub and sub.get("worker_id") and self._url:
            payload = json.dumps(
                {
                    "thread_id": thread_id,
                    "source": event.source,
                    "origin_id": event.origin_id,
                    "text": event.text,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{self._url}/internal/events/followup",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Skein-Token": self._token,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=1.5) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if body.get("forwarded"):
                    log.info(
                        "followup thread=%s ticket=%s worker=%s",
                        thread_id,
                        sub.get("ticket_id"),
                        sub.get("worker_id"),
                    )
                    return
            except (urllib.error.URLError, TimeoutError, ValueError):
                log.warning("followup 失败，回退 Router：thread=%s", thread_id)
        return self._fallback(event, thread_id)


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
    if cfg.router_session and llm is not None:
        runtime = RouterRuntime(
            RouterHost(conn, llm, cfg.direct_model, cfg.route_model),
            RouterSession(),
            batch_size=cfg.router_batch,
            interval=cfg.router_interval,
        )
        runtime.start()
        handler = SubmitHandler(runtime)
        log.info("常驻 Router 已开启：batch=%s interval=%s", cfg.router_batch, cfg.router_interval)
    elif cfg.dispatch and llm is not None:
        handler = DispatchHandler(RouterHost(conn, llm, cfg.direct_model, cfg.route_model))
        log.info("真派单已开启：放行事件会建单")
    elif cfg.shadow:
        if llm is not None:
            handler = ShadowDispatcher(conn, llm, cfg.route_model)
            log.info("影子派单已开启，只记录判断，不建单")
        else:
            log.warning("SKEIN_SHADOW 已开但没有配置模型网关，影子派单不生效")
    if handler is not None and cfg.control_url:
        handler = SubscriptionForwarder(conn, cfg.control_url, cfg.token, handler)
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
