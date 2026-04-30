from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TradeSignal(BaseModel):
    action: Literal["long", "short", "pass"]
    confidence: float = Field(ge=0, le=1)
    entry: float = Field(ge=0)
    stop: float = Field(ge=0)
    tp1: float = Field(ge=0)
    tp2: float = Field(ge=0)
    size_pct: float = Field(ge=0, le=100)
    reasoning: str
    market_maker_thesis: str
    journal_trigger_id: str | None = None
    journal_decision_id: str | None = None

    @field_validator("reasoning", "market_maker_thesis")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text fields cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def validate_prices(self) -> TradeSignal:
        if self.action == "pass":
            return self
        if self.action == "long" and not (self.stop < self.entry < self.tp1 <= self.tp2):
            raise ValueError("long signal requires stop < entry < tp1 <= tp2")
        if self.action == "short" and not (self.stop > self.entry > self.tp1 >= self.tp2):
            raise ValueError("short signal requires stop > entry > tp1 >= tp2")
        return self
