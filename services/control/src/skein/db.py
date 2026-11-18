"""领域库：工单、Thread、订阅、运行、回执、提示词版本、审计。

只有这一个写进程（见 docs/architecture.md）。会话正文不放这里，
它是内容寻址文件，由 session_store 管理；这里只放索引行。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    origin      TEXT NOT NULL,
    origin_id   TEXT NOT NULL,
    thread_id   TEXT,
    payload     TEXT NOT NULL DEFAULT '{}',
    verdict     TEXT NOT NULL DEFAULT 'record',
    created_at  REAL NOT NULL,
    UNIQUE (origin, origin_id)
);

CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,
    origin      TEXT NOT NULL,
    origin_id   TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE (origin, origin_id)
);

CREATE TABLE IF NOT EXISTS tickets (
    id          TEXT PRIMARY KEY,
    code        TEXT NOT NULL,
    thread_id   TEXT,
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'staged',
    dedup_key   TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE (code),
    FOREIGN KEY (thread_id) REFERENCES threads (id)
);

CREATE TABLE IF NOT EXISTS assignments (
    ticket_id   TEXT NOT NULL,
    worker_id   TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'assigned',
    lease_until REAL,
    result      TEXT,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (ticket_id, worker_id),
    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    thread_id   TEXT NOT NULL,
    ticket_id   TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'active',
    created_at  REAL NOT NULL,
    PRIMARY KEY (thread_id, ticket_id),
    FOREIGN KEY (thread_id) REFERENCES threads (id),
    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    job         TEXT NOT NULL,
    day         TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    started_at  REAL,
    finished_at REAL,
    detail      TEXT,
    UNIQUE (job, day)
);

CREATE TABLE IF NOT EXISTS effects (
    key         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    object      TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT 'started',
    response    TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_revisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part          TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    text          TEXT NOT NULL,
    source        TEXT NOT NULL,
    signal_id     TEXT,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_pointers (
    part            TEXT PRIMARY KEY,
    stable_revision INTEGER NOT NULL DEFAULT 0,
    canary_revision INTEGER,
    frozen_until    REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    ticket_id   TEXT,
    node_id     TEXT,
    pinned      INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
);

CREATE TABLE IF NOT EXISTS session_turns (
    session_id  TEXT NOT NULL,
    turn_index  INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    head_sha    TEXT,
    branch      TEXT,
    created_at  REAL NOT NULL,
    PRIMARY KEY (session_id, turn_index),
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_thread ON events (thread_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_dedup ON tickets (dedup_key) WHERE dedup_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_subscriptions_thread ON subscriptions (thread_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit (created_at);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return current
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return SCHEMA_VERSION


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex


def record_audit(conn: sqlite3.Connection, actor: str, action: str, detail: dict) -> None:
    conn.execute(
        "INSERT INTO audit (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (actor, action, json.dumps(detail, ensure_ascii=False), now()),
    )
