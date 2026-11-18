import csv
import json

import pytest

from skein import db
from skein.review import compare, export_csv, load_labels, load_shadow, render


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "review.db")
    db.migrate(c)
    yield c
    c.close()


def seed(conn):
    conn.execute(
        "INSERT INTO events (id, origin, origin_id, payload, verdict, created_at)"
        " VALUES ('e1', 'forum_comment', 'c1', ?, 'process', 1)",
        (json.dumps({"text": "帮忙看下白屏"}),),
    )
    conn.execute(
        "INSERT INTO shadow_decisions (source, origin_id, action, note, created_at)"
        " VALUES ('forum_comment', 'c1', 'route', NULL, 1)"
    )
    conn.execute(
        "INSERT INTO shadow_decisions (source, origin_id, action, note, created_at)"
        " VALUES ('forum_comment', 'c2', 'ignore', NULL, 2)"
    )


def test_load_shadow_joins_text(conn):
    seed(conn)
    rows = load_shadow(conn)
    assert [r["origin_id"] for r in rows] == ["c2", "c1"]
    assert rows[1]["text"] == "帮忙看下白屏"


def test_export_and_load_roundtrip(conn, tmp_path):
    seed(conn)
    path = tmp_path / "labels.csv"
    assert export_csv(load_shadow(conn), path) == 2
    labels = load_labels(path)
    assert labels == {}
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["expected_action"] = "ignore"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    assert load_labels(path) == {("forum_comment", "c2"): "ignore"}


def test_compare_agreement_and_confusion(conn):
    rows = [
        {"source": "forum_comment", "origin_id": "c1", "predicted_action": "route", "text": "a"},
        {"source": "forum_comment", "origin_id": "c2", "predicted_action": "ignore", "text": "b"},
        {"source": "forum_comment", "origin_id": "c3", "predicted_action": "route", "text": "c"},
    ]
    labels = {
        ("forum_comment", "c1"): "route",
        ("forum_comment", "c2"): "route",
        ("forum_comment", "c3"): "ignore",
    }
    report = compare(rows, labels)
    assert report["labeled"] == 3
    assert report["agreement"] == round(1 / 3, 3)
    assert report["confusion"]["route"] == {"route": 1, "ignore": 1}
    assert report["confusion"]["ignore"] == {"route": 1}
    assert len(report["disagreements"]) == 2


def test_compare_without_labels(conn):
    report = compare([{"source": "s", "origin_id": "1", "predicted_action": "route", "text": ""}], {})
    assert report["labeled"] == 0
    assert report["agreement"] == 0.0


def test_render_contains_numbers():
    report = {
        "total_shadow": 10,
        "labeled": 4,
        "coverage": 0.4,
        "agreement": 0.75,
        "confusion": {"route": {"route": 3}},
        "disagreements": [
            {"source": "forum_comment", "origin_id": "c9", "expected": "ignore", "predicted": "route", "text": "x"}
        ],
    }
    text = render(report)
    assert "10" in text and "4" in text and "75.0%" in text and "c9" in text
