from __future__ import annotations

import json
import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


DetectorName = Literal["liquidity_grab", "stop_hunt", "order_book_imbalance", "LiquidityGrabDetector", "StopHuntDetector", "OrderBookImbalanceDetector"]
FeedSource = Literal["blofin", "binance", "aggr_trade", "kraken", "BloFin-primary", "Binance-fallback", "aggr.trade-fallback", "HALTED"]
Action = Literal["long", "short", "pass"]
ExitReason = Literal["tp1", "tp2", "stop", "manual", "invalidated", "expired", "paper"]


def new_id() -> str:
    return uuid4().hex


def now_ms() -> int:
    return int(time.time() * 1000)


def compact_json(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


class TriggerRow(BaseModel):
    id: str = Field(default_factory=new_id)
    ts_ms: int = Field(default_factory=now_ms)
    symbol: str
    detector: str
    confidence: float = Field(ge=0, le=1)
    invoked_llm: bool
    feed_source: str
    market_snapshot_json: str
    detector_meta_json: str | None = None

    @field_validator("market_snapshot_json", "detector_meta_json")
    @classmethod
    def valid_json_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        json.loads(value)
        return value


class DecisionRow(BaseModel):
    id: str = Field(default_factory=new_id)
    trigger_id: str
    ts_ms: int = Field(default_factory=now_ms)
    model: str
    action: Action
    confidence: float = Field(ge=0, le=1)
    entry: float | None = None
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    size_pct: float | None = None
    reasoning: str | None = None
    market_maker_thesis: str | None = None
    latency_ms: int
    parse_ok: bool
    raw_response: str | None = None

    @field_validator("raw_response")
    @classmethod
    def trim_raw_response(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return value[:16_384]


class OutcomeRow(BaseModel):
    id: str = Field(default_factory=new_id)
    decision_id: str
    opened_ts_ms: int | None = None
    closed_ts_ms: int = Field(default_factory=now_ms)
    entry_fill: float | None = None
    exit_fill: float | None = None
    pnl_quote: float | None = None
    pnl_pct: float | None = None
    exit_reason: ExitReason
    max_favorable_excursion: float | None = None
    max_adverse_excursion: float | None = None
    is_paper: bool = False
