"""运行时配置。只从环境变量读，不读配置文件。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_path: str = "data/skein.db"
    blob_root: str = "data/blobs"
    token: str = ""
    port: int = 7700
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

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        source = os.environ if env is None else env
        base = cls()
        return cls(
            db_path=source.get("SKEIN_DB", base.db_path),
            blob_root=source.get("SKEIN_BLOB_ROOT", base.blob_root),
            token=source.get("SKEIN_TOKEN", base.token),
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
        )
