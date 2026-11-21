import sqlite3

import pytest

from skein import db


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


def test_migration_is_idempotent(conn):
    assert db.migrate(conn) == db.SCHEMA_VERSION
    assert db.migrate(conn) == db.SCHEMA_VERSION


def test_events_unique_per_origin(conn):
    row = (db.new_id(), "forum_comment", "42", None, "{}", "record", db.now())
    conn.execute(
        "INSERT INTO events (id, origin, origin_id, thread_id, payload, verdict, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        row,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO events (id, origin, origin_id, thread_id, payload, verdict, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (db.new_id(), "forum_comment", "42", None, "{}", "record", db.now()),
        )


def test_ticket_code_unique(conn):
    t = db.now()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("t1", "YZ-1", "a", "staged", t, t),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tickets (id, code, title, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("t2", "YZ-1", "b", "staged", t, t),
        )


def test_dedup_key_unique(conn):
    t = db.now()
    conn.execute(
        "INSERT INTO tickets (id, code, title, status, dedup_key, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("t8", "SK-8", "a", "staged", "forum:c1", t, t),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tickets (id, code, title, status, dedup_key, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t9", "SK-9", "b", "staged", "forum:c1", t, t),
        )


def test_foreign_keys_are_enforced(conn):
    t = db.now()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tickets (id, code, thread_id, title, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("t3", "YZ-2", "missing-thread", "x", "staged", t, t),
        )


def test_runs_unique_per_job_and_day(conn):
    t = db.now()
    conn.execute(
        "INSERT INTO runs (id, job, day, status, started_at) VALUES (?, ?, ?, ?, ?)",
        ("r1", "daily", "2026-11-18", "running", t),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO runs (id, job, day, status, started_at) VALUES (?, ?, ?, ?, ?)",
            ("r2", "daily", "2026-11-18", "running", t),
        )


def test_audit_appends(conn):
    db.record_audit(conn, "router", "dispatch", {"ticket": "YZ-1"})
    db.record_audit(conn, "watchdog", "prompt_propose", {"part": "runtime/work"})
    rows = conn.execute("SELECT actor, action FROM audit ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("router", "dispatch"),
        ("watchdog", "prompt_propose"),
    ]
