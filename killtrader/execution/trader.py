from __future__ import annotations

from killtrader.config import Settings
from killtrader.core.errors import ExchangeExecutionError
from killtrader.core.logger import get_logger
from killtrader.exchange.blofin import BloFinExchange
from killtrader.execution.risk import PositionState, RiskManager
from killtrader.signal.schema import TradeSignal

log = get_logger(__name__)


class Trader:
    def __init__(self, settings: Settings, exchange: BloFinExchange, risk: RiskManager) -> None:
        self.settings = settings
        self.exchange = exchange
        self.risk = risk

    async def execute(self, signal: TradeSignal, account_equity: float) -> PositionState | None:
        if signal.action == "pass":
            log.info("signal_passed", reason=signal.reasoning)
            return None
        self.risk.validate(signal)
        size = self.risk.position_size(account_equity, signal)
        if not self.settings.trade_enabled:
            raise ExchangeExecutionError("TRADE_ENABLED=false; execution blocked by configuration")
        side = "buy" if signal.action == "long" else "sell"
        await self.exchange.place_market_order(self.settings.symbol, side, size)
        await self.exchange.set_tp_sl(
            self.settings.symbol, signal.action, size, signal.stop, signal.tp1, signal.tp2
        )
        position = PositionState(
            symbol=self.settings.symbol, side=signal.action, entry=signal.entry, size=size
        )
        self.risk.positions.append(position)
        log.info("trade_executed", action=signal.action, entry=signal.entry, size=size)
        return position

    async def cancel_if_invalidated(
        self, order_id: str, latest_price: float, signal: TradeSignal
    ) -> None:
        invalidated = (signal.action == "long" and latest_price <= signal.stop) or (
            signal.action == "short" and latest_price >= signal.stop
        )
        if invalidated:
            await self.exchange.cancel_order(self.settings.symbol, order_id)
