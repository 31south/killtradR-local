from __future__ import annotations

from collections import defaultdict
from typing import Any

from killtrader.config import load_settings
from killtrader.journal.store import JournalStore


def _store(path: str | None = None) -> JournalStore:
    return JournalStore(path or load_settings().journal_path)


async def _fetch(path: str | None, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    store = _store(path)
    await store.init()
    return await store.fetch_all(sql, params)


async def recent_triggers(
    limit: int = 100,
    detector: str | None = None,
    since_ms: int | None = None,
    path: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if detector:
        clauses.append("t.detector = ?")
        params.append(detector)
    if since_ms is not None:
        clauses.append("t.ts_ms >= ?")
        params.append(since_ms)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)
    return await _fetch(
        path,
        f"""
        SELECT t.*, d.action, d.model, d.parse_ok, d.confidence AS decision_confidence
        FROM triggers t
        LEFT JOIN decisions d ON d.trigger_id = t.id
        {where}
        ORDER BY t.ts_ms DESC
        LIMIT ?
        """,
        tuple(params),
    )


async def decisions_for_detector(
    detector: str, since_ms: int | None = None, path: str | None = None
) -> list[dict[str, Any]]:
    params: list[Any] = [detector]
    since_clause = ""
    if since_ms is not None:
        since_clause = "AND d.ts_ms >= ?"
        params.append(since_ms)
    return await _fetch(
        path,
        f"""
        SELECT d.*, t.detector, t.symbol, t.feed_source,
               o.pnl_pct, o.pnl_quote, o.exit_reason, o.is_paper
        FROM decisions d
        JOIN triggers t ON t.id = d.trigger_id
        LEFT JOIN outcomes o ON o.decision_id = d.id
        WHERE t.detector = ? {since_clause}
        ORDER BY d.ts_ms DESC
        """,
        tuple(params),
    )


async def win_rate_by_detector(
    since_ms: int | None = None, path: str | None = None
) -> list[dict[str, Any]]:
    params: list[Any] = []
    since_clause = ""
    if since_ms is not None:
        since_clause = "WHERE d.ts_ms >= ?"
        params.append(since_ms)
    rows = await _fetch(
        path,
        f"""
        SELECT t.detector, d.confidence, o.pnl_pct
        FROM decisions d
        JOIN triggers t ON t.id = d.trigger_id
        JOIN outcomes o ON o.decision_id = d.id
        {since_clause}
        """,
        tuple(params),
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["detector"]].append(row)
    results: list[dict[str, Any]] = []
    for detector, detector_rows in grouped.items():
        pnl_values = [float(row["pnl_pct"] or 0) for row in detector_rows]
        buckets = _bucket_stats(detector_rows)
        wins = sum(1 for value in pnl_values if value > 0)
        results.append(
            {
                "detector": detector,
                "sample_count": len(detector_rows),
                "win_rate": wins / len(detector_rows) if detector_rows else 0.0,
                "avg_pnl_pct": sum(pnl_values) / len(pnl_values) if pnl_values else 0.0,
                "confidence_buckets": buckets,
            }
        )
    return results


async def parse_failure_rate(
    since_ms: int | None = None, path: str | None = None
) -> list[dict[str, Any]]:
    params: list[Any] = []
    since_clause = ""
    if since_ms is not None:
        since_clause = "WHERE ts_ms >= ?"
        params.append(since_ms)
    rows = await _fetch(
        path,
        f"""
        SELECT model, COUNT(*) AS total, SUM(CASE WHEN parse_ok = 0 THEN 1 ELSE 0 END) AS failed
        FROM decisions
        {since_clause}
        GROUP BY model
        ORDER BY model
        """,
        tuple(params),
    )
    for row in rows:
        total = row["total"] or 0
        failed = row["failed"] or 0
        row["failure_rate"] = failed / total if total else 0.0
    return rows


def _bucket_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    buckets = {"0.75-0.80": [], "0.80-0.85": [], "0.85-0.90": [], "0.90+": []}
    for row in rows:
        confidence = float(row["confidence"] or 0)
        pnl_pct = float(row["pnl_pct"] or 0)
        if confidence < 0.80:
            buckets["0.75-0.80"].append(pnl_pct)
        elif confidence < 0.85:
            buckets["0.80-0.85"].append(pnl_pct)
        elif confidence < 0.90:
            buckets["0.85-0.90"].append(pnl_pct)
        else:
            buckets["0.90+"].append(pnl_pct)
    return {
        name: {
            "sample_count": len(values),
            "win_rate": sum(1 for value in values if value > 0) / len(values) if values else 0.0,
            "avg_pnl_pct": sum(values) / len(values) if values else 0.0,
        }
        for name, values in buckets.items()
    }
