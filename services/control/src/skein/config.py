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

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
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
        )
