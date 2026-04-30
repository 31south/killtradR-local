from __future__ import annotations

from dataclasses import dataclass

from killtrader.config import Settings
from killtrader.core.errors import RiskLimitExceededError
from killtrader.signal.schema import TradeSignal


@dataclass(slots=True)
class PositionState:
    symbol: str
    side: str
    entry: float
    size: float
    unrealized_pnl: float = 0.0


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.positions: list[PositionState] = []
        self.realized_daily_loss_pct = 0.0

    def validate(self, signal: TradeSignal) -> None:
        if signal.action == "pass":
            raise RiskLimitExceededError("pass signals cannot be executed")
        if len(self.positions) >= self.settings.max_concurrent_positions:
            raise RiskLimitExceededError("max concurrent positions reached")
        if self.realized_daily_loss_pct >= self.settings.max_daily_loss_pct:
            raise RiskLimitExceededError("daily loss limit reached")
        if signal.size_pct > self.settings.risk_pct_per_trade:
            raise RiskLimitExceededError("signal size exceeds configured per-trade risk")

    def position_size(self, account_equity: float, signal: TradeSignal) -> float:
        risk_cash = account_equity * (min(signal.size_pct, self.settings.risk_pct_per_trade) / 100)
        stop_distance = abs(signal.entry - signal.stop)
        if stop_distance <= 0:
            raise RiskLimitExceededError("stop distance must be positive")
        return risk_cash / stop_distance
