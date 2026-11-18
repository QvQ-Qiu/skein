"""影子对比：导出待标注样本，计算与人工判断的一致率。

流程：
1. `python -m skein.review export --out labels.csv` 导出影子判定；
2. 人填 expected_action 一列；
3. `python -m skein.review report --labels labels.csv` 看一致率与分歧。
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path

from skein import db
from skein.config import Config

COLUMNS = ("source", "origin_id", "predicted_action", "note", "text", "expected_action")
ACTIONS = ("route", "attach", "ignore", "stop", "note")


def load_shadow(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    sql = (
        "SELECT s.source, s.origin_id, s.action AS predicted_action, s.note,"
        "       e.payload AS payload"
        " FROM shadow_decisions s"
        " LEFT JOIN events e ON e.origin = s.source AND e.origin_id = s.origin_id"
        " ORDER BY s.id DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = []
    for row in conn.execute(sql):
        text = ""
        if row["payload"]:
            try:
                text = (json.loads(row["payload"]).get("text") or "").strip()
            except json.JSONDecodeError:
                text = ""
        rows.append(
            {
                "source": row["source"],
                "origin_id": row["origin_id"],
                "predicted_action": row["predicted_action"],
                "note": row["note"] or "",
                "text": text,
            }
        )
    return rows


def export_csv(rows: list[dict], path: str | Path) -> int:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "expected_action": ""})
    return len(rows)


def load_labels(path: str | Path) -> dict[tuple[str, str], str]:
    labels: dict[tuple[str, str], str] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            expected = (row.get("expected_action") or "").strip()
            if expected:
                labels[(row["source"], row["origin_id"])] = expected
    return labels


def compare(rows: list[dict], labels: dict[tuple[str, str], str]) -> dict:
    labeled = [r for r in rows if (r["source"], r["origin_id"]) in labels]
    agree = 0
    confusion: dict[str, Counter] = {}
    disagreements = []
    for row in labeled:
        expected = labels[(row["source"], row["origin_id"])]
        predicted = row["predicted_action"]
        confusion.setdefault(expected, Counter())[predicted] += 1
        if expected == predicted:
            agree += 1
        else:
            disagreements.append(
                {
                    "source": row["source"],
                    "origin_id": row["origin_id"],
                    "expected": expected,
                    "predicted": predicted,
                    "text": row["text"][:120],
                }
            )
    total = len(labeled)
    return {
        "total_shadow": len(rows),
        "labeled": total,
        "coverage": round(total / len(rows), 3) if rows else 0.0,
        "agreement": round(agree / total, 3) if total else 0.0,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "disagreements": disagreements,
    }


def render(report: dict) -> str:
    lines = [
        (
            f"影子判定 {report['total_shadow']} 条，已标注 {report['labeled']} 条"
            f"（覆盖 {report['coverage']:.1%}）"
        ),
        f"一致率 {report['agreement']:.1%}",
    ]
    if report["confusion"]:
        lines.append("混淆矩阵（行=人工，列=模型）：")
        for expected, row in sorted(report["confusion"].items()):
            cells = "  ".join(f"{action}={count}" for action, count in sorted(row.items()))
            lines.append(f"  {expected}: {cells}")
    if report["disagreements"]:
        lines.append(f"分歧 {len(report['disagreements'])} 条：")
        for item in report["disagreements"][:20]:
            lines.append(
                f"  [{item['source']}:{item['origin_id']}] 人={item['expected']}"
                f" 模型={item['predicted']}  {item['text']}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="skein.review")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="导出影子判定为 CSV")
    export.add_argument("--out", required=True)
    export.add_argument("--limit", type=int, default=200)

    report = sub.add_parser("report", help="计算一致率与分歧")
    report.add_argument("--labels", required=True)
    report.add_argument("--limit", type=int, default=1000)

    args = parser.parse_args(argv)
    cfg = Config.from_env()
    conn = db.connect(cfg.db_path)
    db.migrate(conn)

    rows = load_shadow(conn, args.limit)
    if args.command == "export":
        count = export_csv(rows, args.out)
        print(f"已导出 {count} 条到 {args.out}，请填写 expected_action 列")
        return
    print(render(compare(rows, load_labels(args.labels))))


if __name__ == "__main__":
    main()
