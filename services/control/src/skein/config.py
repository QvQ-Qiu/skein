"""运行时配置。只从环境变量读，不读配置文件。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    db_path: str = "data/skein.db"
    blob_root: str = "data/blobs"
    token: str = ""
    host: str = "127.0.0.1"
    port: int = 7800
    direct_model: str = "direct"
    route_model: str = "route"
    forum_base: str = "http://127.0.0.1:8080"
    forum_token: str = ""
    llm_base: str = ""
    llm_key: str = ""
    forum_db_host: str = "127.0.0.1"
    forum_db_port: int = 3506
    forum_db_user: str = ""
    forum_db_password: str = ""
    forum_db_name: str = "ssemarket"
    poll_interval: int = 60
    shadow: bool = False
    dispatch: bool = False
    schedules: str = ""
    job_commands: str = ""
    jobs_file: str = ""
    driver_enabled: bool = False
    job_timeout: int = 14400
    platform_repos: dict[str, str] = field(default_factory=dict)
    router_session: bool = False
    router_batch: int = 8
    router_interval: float = 0.5
    control_url: str = ""
    watchdog: bool = False
    watchdog_auto: bool = False
    watchdog_interval: int = 300
    watchdog_tickets: bool = False
    watchdog_dispatch: bool = False
    daily_ticket_hour: int = -1
    ticket_daily_cap: int = 5
    sso_base: str = "https://sso.ssemarket.cn"
    sso_client_id: str = ""
    sso_client_secret: str = ""
    # None=按 unisso 响应种；""=host-only；其他值=显式域
    sso_cookie_domain: str | None = None
    # 给 chat 上游签用户身份头（sub/internal/email），与小秋的 SKEIN_USER_SECRET 相同
    assertion_key: str = ""
    qa_chat_upstream: str = ""
    qa_ops_upstream: str = ""
    ops_panel_secret: str = ""
    ops_panel_sub: str = "7cbb5d2c-ad9f-4184-b80b-7e7421642d3f"
    ops_panel_email: str = "skein@ssemarket.cn"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        source = os.environ if env is None else env
        base = cls()
        try:
            parsed_repos = json.loads(source.get("SKEIN_PLATFORM_REPOS", "") or "{}")
        except json.JSONDecodeError:
            parsed_repos = {}
        platform_repos = (
            {str(key): str(value) for key, value in parsed_repos.items()}
            if isinstance(parsed_repos, dict)
            else {}
        )
        return cls(
            db_path=source.get("SKEIN_DB", base.db_path),
            blob_root=source.get("SKEIN_BLOB_ROOT", base.blob_root),
            token=source.get("SKEIN_TOKEN", base.token),
            host=source.get("SKEIN_HOST", base.host),
            port=int(source.get("SKEIN_PORT", base.port)),
            direct_model=source.get("SKEIN_DIRECT_MODEL", base.direct_model),
            route_model=source.get("SKEIN_ROUTE_MODEL", base.route_model),
            forum_base=source.get("SKEIN_FORUM_BASE", base.forum_base),
            forum_token=source.get("SKEIN_FORUM_TOKEN", base.forum_token),
            llm_base=source.get("SKEIN_LLM_BASE", base.llm_base),
            llm_key=source.get("SKEIN_LLM_KEY", base.llm_key),
            forum_db_host=source.get("SKEIN_FORUM_DB_HOST", base.forum_db_host),
            forum_db_port=int(source.get("SKEIN_FORUM_DB_PORT", base.forum_db_port)),
            forum_db_user=source.get("SKEIN_FORUM_DB_USER", base.forum_db_user),
            forum_db_password=source.get("SKEIN_FORUM_DB_PASSWORD", base.forum_db_password),
            forum_db_name=source.get("SKEIN_FORUM_DB_NAME", base.forum_db_name),
            poll_interval=int(source.get("SKEIN_POLL_INTERVAL", base.poll_interval)),
            shadow=source.get("SKEIN_SHADOW", "0") in ("1", "true", "yes"),
            dispatch=source.get("SKEIN_DISPATCH", "0") in ("1", "true", "yes"),
            schedules=source.get("SKEIN_SCHEDULES", base.schedules),
            job_commands=source.get("SKEIN_JOB_COMMANDS", base.job_commands),
            jobs_file=source.get("SKEIN_JOBS_FILE", base.jobs_file),
            driver_enabled=source.get("SKEIN_DRIVER", "0") in ("1", "true", "yes"),
            job_timeout=int(source.get("SKEIN_JOB_TIMEOUT", base.job_timeout)),
            platform_repos=platform_repos,
            router_session=source.get("SKEIN_ROUTER_SESSION", "0")
            in ("1", "true", "yes"),
            router_batch=int(source.get("SKEIN_ROUTER_BATCH", base.router_batch)),
            router_interval=float(
                source.get("SKEIN_ROUTER_INTERVAL", base.router_interval)
            ),
            control_url=source.get("SKEIN_CONTROL_URL", base.control_url),
            watchdog=source.get("SKEIN_WATCHDOG", "0") in ("1", "true", "yes"),
            watchdog_auto=source.get("SKEIN_WATCHDOG_AUTO", "0")
            in ("1", "true", "yes"),
            watchdog_interval=int(
                source.get("SKEIN_WATCHDOG_INTERVAL", base.watchdog_interval)
            ),
            watchdog_tickets=source.get("SKEIN_WATCHDOG_TICKETS", "0")
            in ("1", "true", "yes"),
            watchdog_dispatch=source.get("SKEIN_WATCHDOG_DISPATCH", "0")
            in ("1", "true", "yes"),
            daily_ticket_hour=int(
                source.get("SKEIN_DAILY_TICKET_HOUR", base.daily_ticket_hour)
            ),
            ticket_daily_cap=int(
                source.get("SKEIN_TICKET_DAILY_CAP", base.ticket_daily_cap)
            ),
            sso_base=source.get("SKEIN_SSO_BASE", base.sso_base),
            sso_client_id=source.get("SKEIN_SSO_CLIENT_ID", base.sso_client_id),
            sso_client_secret=source.get(
                "SKEIN_SSO_CLIENT_SECRET", base.sso_client_secret
            ),
            sso_cookie_domain=cls._cookie_domain(
                source.get("SKEIN_COOKIE_DOMAIN", base.sso_cookie_domain)
            ),
            assertion_key=source.get("SKEIN_ASSERTION_KEY", base.assertion_key),
            qa_chat_upstream=source.get("SKEIN_QA_CHAT_UPSTREAM", base.qa_chat_upstream),
            qa_ops_upstream=source.get("SKEIN_QA_OPS_UPSTREAM", base.qa_ops_upstream),
            ops_panel_secret=source.get("SKEIN_OPS_PANEL_SECRET", base.ops_panel_secret),
            ops_panel_sub=source.get("SKEIN_OPS_PANEL_SUB", base.ops_panel_sub),
            ops_panel_email=source.get("SKEIN_OPS_PANEL_EMAIL", base.ops_panel_email),
        )

    @staticmethod
    def _cookie_domain(value: str | None) -> str | None:
        # "host" 或空串 = 不带 Domain（本地 http 调试）；缺省 = 按 unisso 响应。
        if value is None:
            return None
        if value.strip().lower() in ("", "host"):
            return ""
        return value
