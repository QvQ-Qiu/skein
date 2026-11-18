"""进程入口：python -m skein"""

from __future__ import annotations

from pathlib import Path

import uvicorn

from skein import db
from skein.api import create_app
from skein.channels import HttpWriter
from skein.config import Config


def build() -> tuple:
    cfg = Config.from_env()
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.blob_root).mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    writer = HttpWriter(cfg.forum_base, cfg.forum_token)
    return create_app(conn, cfg.blob_root, cfg.token, writer), cfg


def main() -> None:
    app, cfg = build()
    uvicorn.run(app, host="127.0.0.1", port=cfg.port)


if __name__ == "__main__":
    main()
