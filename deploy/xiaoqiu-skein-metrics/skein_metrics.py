"""雁阵平台指标采集（替代 qdockd 指标）。数据源：控制面 `/internal/summary`、`/internal/signals`、`/internal/topology`。
表沿用 qdock_metric_samples（名字前缀 skein_），序列结构保持 /ops/platform/series 原约定：
    warm ← 在线节点数      creating ← 在途派工
    watermark ← 待验收积压  reject_rate ← 卡住信号数
    p50/p99/rps/err5xx ← 缺省 None（雁阵是控制面，不是请求网关）
"""

from __future__ import annotations

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

import httpx

from app import db
from app.config import get_config

log = logging.getLogger(__name__)

_pool: db.Pool | None = None

DDL = """
CREATE TABLE IF NOT EXISTS qdock_metric_samples (
    id     BIGSERIAL PRIMARY KEY,
    ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    name   TEXT NOT NULL,
    labels TEXT NOT NULL DEFAULT '',
    value  DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qms_ts ON qdock_metric_samples (ts);
CREATE INDEX IF NOT EXISTS idx_qms_name_ts ON qdock_metric_samples (name, ts);
"""

ACTIVE_STATES = ("dispatched", "created", "pending")
STALLED_KINDS = ("dispatch_timeout", "node_offline_with_work")
FAILED_KINDS = ("run_failed", "effect_failed")


async def init(dsn: str) -> None:
    global _pool
    _pool = await db.create_pool(dsn, name="ops-skein", min_size=1, max_size=2)
    async with _pool.acquire() as conn:
        await conn.execute(DDL)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
    _pool = None


def _base() -> str:
    cfg = get_config().skein
    return (os.environ.get(cfg.url_env, "") or cfg.base_url).rstrip("/")


def _headers() -> dict[str, str]:
    token = os.environ.get(get_config().skein.token_env, "")
    return {"X-Skein-Token": token} if token else {}


async def scrape_once() -> str:
    cfg = get_config().skein
    if not cfg.enabled:
        return "disabled"
    if _pool is None:
        return "error"
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as hc:
            summary = (await hc.get(f"{_base()}/internal/summary", headers=_headers())).json()
            signals = (await hc.get(f"{_base()}/internal/signals", headers=_headers())).json()
            topo = (await hc.get(f"{_base()}/internal/topology", headers=_headers())).json()
    except Exception as e:  # noqa: BLE001 —— 采集失败不影响服务
        log.warning("skein metrics scrape failed: %s: %s", type(e).__name__, e)
        return "error"

    by_status = (summary.get("tickets") or {}).get("by_status") or {}
    review = float(by_status.get("review", 0))
    active = float(
        by_status.get("staged", 0) + by_status.get("assigned", 0) + by_status.get("running", 0)
    )
    sig_list = signals.get("signals") or []
    stalled = float(sum(1 for s in sig_list if s.get("kind") in STALLED_KINDS))
    failed = float(sum(1 for s in sig_list if s.get("kind") in FAILED_KINDS))
    node_list = topo.get("nodes") or []
    online = float(sum(1 for n in node_list if n.get("status") == "online"))
    dispatch_list = topo.get("dispatches") or []
    in_flight = float(sum(1 for d in dispatch_list if d.get("state") in ACTIVE_STATES))

    rows = [
        ("skein_review_backlog", review),
        ("skein_tickets_active", active),
        ("skein_tickets_done", float(by_status.get("done", 0))),
        ("skein_tickets_rejected", float(by_status.get("rejected", 0))),
        ("skein_active_dispatches", in_flight),
        ("skein_nodes_online", online),
        ("skein_nodes_total", float(len(node_list))),
        ("skein_signals_total", float(len(sig_list))),
        ("skein_stalled_signals", stalled),
        ("skein_failed_signals", failed),
    ]
    if _pool is None:
        return "error"
    async with _pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO qdock_metric_samples (name, labels, value) VALUES ($1,'',$2)", rows
        )
        await conn.execute(
            "DELETE FROM qdock_metric_samples WHERE ts < now() - $1::interval",
            timedelta(days=cfg.prune_days),
        )
    return f"scraped:{len(rows)}"


async def _latest(conn, name: str) -> float | None:
    value = await conn.fetchval(
        "SELECT value FROM qdock_metric_samples WHERE name=$1 ORDER BY ts DESC LIMIT 1", name
    )
    return float(value) if value is not None else None


async def alert_metrics() -> dict:
    out: dict = {}
    if _pool is None:
        return out
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT max(ts) AS ts FROM qdock_metric_samples WHERE name LIKE 'skein_%'"
        )
        if row and row["ts"] is not None:
            out["skein_scrape_age_s"] = max(
                (datetime.now(UTC) - row["ts"]).total_seconds(), 0.0
            )
        else:
            out["skein_scrape_age_s"] = 99999.0
        for name in (
            "skein_review_backlog",
            "skein_stalled_signals",
            "skein_active_dispatches",
            "skein_failed_signals",
        ):
            value = await _latest(conn, name)
            if value is not None:
                out[name] = value
        total = await _latest(conn, "skein_nodes_total")
        online = await _latest(conn, "skein_nodes_online")
        if total is not None and online is not None:
            out["skein_nodes_offline"] = max(total - online, 0.0)
    return out


async def interval_series(minutes: int = 30) -> dict:
    """保持 /ops/platform/series 原返回结构，字段映射见模块头。"""
    out: dict = {
        "ts": [],
        "warm": [],
        "creating": [],
        "pop_p50": [],
        "pop_p99": [],
        "rps": [],
        "err5xx": [],
        "watermark": [],
        "reject_rate": [],
    }
    if _pool is None:
        return out
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT ts, name, value FROM qdock_metric_samples"
            " WHERE name LIKE 'skein_%' AND ts > now() - ($1::text)::interval ORDER BY ts",
            f"{minutes} minutes",
        )
    frames: list[tuple[datetime, dict[str, float]]] = []
    cur_ts: datetime | None = None
    cur: dict[str, float] = {}
    for r in rows:
        if cur_ts is None or r["ts"] != cur_ts:
            if cur_ts is not None:
                frames.append((cur_ts, cur))
            cur_ts, cur = r["ts"], {}
        cur[r["name"]] = float(r["value"])
    if cur_ts is not None:
        frames.append((cur_ts, cur))
    for ts, frame in frames:
        out["ts"].append(ts.isoformat())
        out["warm"].append(frame.get("skein_nodes_online"))
        out["creating"].append(frame.get("skein_active_dispatches"))
        out["watermark"].append(frame.get("skein_review_backlog"))
        out["reject_rate"].append(frame.get("skein_stalled_signals"))
        out["pop_p50"].append(None)
        out["pop_p99"].append(None)
        out["rps"].append(None)
        out["err5xx"].append(None)
    return out
