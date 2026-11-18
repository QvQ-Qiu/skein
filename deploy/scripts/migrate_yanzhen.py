#!/usr/bin/env python3
"""把旧雁阵 SQLite 迁到新控制面的领域库。

用法：
    python deploy/scripts/migrate_yanzhen.py \
        --old /root/yanzhen/data/yanzhen.db \
        --new /srv/skein/skein.db \
        [--dry-run]

只读旧库；新库不存在时会建表。重复执行是安全的（按唯一键跳过）。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "control" / "src"))

from skein import db  # noqa: E402

JS_DATE = re.compile(r"^(\w{3}) (\w{3}) (\d{1,2}) (\d{4}) (\d{2}):(\d{2}):(\d{2}) GMT([+-])(\d{2})(\d{2})")


def parse_ts(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    match = JS_DATE.match(text)
    if match:
        _, mon, day, year, hour, minute, second, sign, off_h, off_m = match.groups()
        try:
            dt = datetime.strptime(
                f"{mon} {day} {year} {hour}:{minute}:{second}", "%b %d %Y %H:%M:%S"
            )
            offset = (int(off_h) * 3600 + int(off_m) * 60) * (1 if sign == "+" else -1)
            return dt.replace(tzinfo=timezone.utc).timestamp() - offset
        except ValueError:
            return 0.0
    return 0.0


def event_verdict(raw) -> str:
    if not raw:
        return "record"
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return "record"
    if isinstance(data, dict):
        return "process" if data.get("is_req") else "ignore"
    return "record"


def migrate(old_path: str, new_path: str, dry_run: bool = False) -> dict:
    old = sqlite3.connect(f"file:{old_path}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    new_path_obj = Path(new_path)
    if new_path_obj.exists() and not dry_run:
        new = db.connect(new_path)
        db.migrate(new)
    elif dry_run:
        new = db.connect(":memory:")
        db.migrate(new)
    else:
        new_path_obj.parent.mkdir(parents=True, exist_ok=True)
        new = db.connect(new_path)
        db.migrate(new)
    new.row_factory = sqlite3.Row

    thread_ids: dict[str, str] = {}
    event_thread: dict[str, str] = {}
    counts = {"events": 0, "tickets": 0, "assignments": 0, "nodes": 0}

    def ensure_thread(post_key: str) -> str:
        if post_key in thread_ids:
            return thread_ids[post_key]
        row = new.execute(
            "SELECT id FROM threads WHERE origin = 'forum_post' AND origin_id = ?", (post_key,)
        ).fetchone()
        if row:
            thread_ids[post_key] = row["id"]
            return row["id"]
        thread_id = db.new_id()
        new.execute(
            "INSERT INTO threads (id, origin, origin_id, created_at) VALUES (?, 'forum_post', ?, ?)",
            (thread_id, post_key, parse_ts(None) or 0.0),
        )
        thread_ids[post_key] = thread_id
        return thread_id

    for row in old.execute("SELECT * FROM events ORDER BY fetched_at"):
        origin = "forum_post" if row["channel"] == "market-post" else "forum_comment"
        post_key = row["parent_id"] or row["source_id"]
        thread_id = ensure_thread(str(post_key))
        text = "\n".join(part for part in (row["title"], row["content"]) if part)
        payload = json.dumps(
            {"text": text, "actor": row["author_alias"], "parent_id": row["parent_id"]},
            ensure_ascii=False,
        )
        try:
            new.execute(
                "INSERT INTO events (id, origin, origin_id, thread_id, payload, verdict, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(row["id"]),
                    origin,
                    str(row["source_id"]),
                    thread_id,
                    payload,
                    event_verdict(row["verdict"]),
                    parse_ts(row["fetched_at"]) or 0.0,
                ),
            )
            counts["events"] += 1
        except sqlite3.IntegrityError:
            pass
        event_thread[str(row["id"])] = thread_id

    for row in old.execute("SELECT * FROM tickets"):
        thread_id = event_thread.get(str(row["source_event_id"]))
        try:
            new.execute(
                "INSERT INTO tickets (id, code, thread_id, title, status, dedup_key, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(row["id"]),
                    row["code"],
                    thread_id,
                    row["title"] or "",
                    row["status"] or "staged",
                    row["dedup_key"],
                    parse_ts(row["created_at"]) or 0.0,
                    parse_ts(row["updated_at"]) or 0.0,
                ),
            )
            counts["tickets"] += 1
        except sqlite3.IntegrityError:
            pass

    for row in old.execute("SELECT * FROM ticket_workers"):
        try:
            new.execute(
                "INSERT OR IGNORE INTO assignments (ticket_id, worker_id, state, result, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    str(row["ticket_id"]),
                    str(row["worker_id"]),
                    row["state"] or "assigned",
                    row["result"],
                    parse_ts(row["assigned_at"]) or 0.0,
                ),
            )
            counts["assignments"] += 1
        except sqlite3.IntegrityError:
            pass

    for row in old.execute("SELECT * FROM nodes"):
        try:
            new.execute(
                "INSERT OR REPLACE INTO nodes (id, name, host, platforms, peer_url, status, max_workers, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(row["id"]),
                    row["name"] or "",
                    row["host"] or "",
                    row["platforms"] or "[]",
                    row["peer_url"] or "",
                    "offline",
                    int(row["max_workers"] or 3),
                    parse_ts(row["last_seen"]) or 0.0,
                ),
            )
            counts["nodes"] += 1
        except sqlite3.IntegrityError:
            pass

    if not dry_run:
        new.commit()
    old.close()
    new.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    counts = migrate(args.old, args.new, args.dry_run)
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
