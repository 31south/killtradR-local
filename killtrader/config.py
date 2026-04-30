from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    blofin_api_key: str = Field(default="", alias="BLOFIN_API_KEY")
    blofin_secret: str = Field(default="", alias="BLOFIN_SECRET")
    blofin_passphrase: str = Field(default="", alias="BLOFIN_PASSPHRASE")
    use_demo: bool = Field(default=True, alias="USE_DEMO")

    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field(default="deepseek-coder", alias="OLLAMA_MODEL")

    symbol: str = Field(default="BTC-USDT", alias="SYMBOL")
    risk_pct_per_trade: float = Field(default=0.5, alias="RISK_PCT_PER_TRADE")
    max_concurrent_positions: int = Field(default=2, alias="MAX_CONCURRENT_POSITIONS")
    max_daily_loss_pct: float = Field(default=3.0, alias="MAX_DAILY_LOSS_PCT")
    trade_enabled: bool = Field(default=False, alias="TRADE_ENABLED")

    detector_confidence_threshold: float = Field(
        default=0.75, alias="DETECTOR_CONFIDENCE_THRESHOLD"
    )
    liquidity_grab_lookback_bars: int = Field(default=50, alias="LIQUIDITY_GRAB_LOOKBACK_BARS")
    stop_hunt_retrace_window_sec: int = Field(default=30, alias="STOP_HUNT_RETRACE_WINDOW_SEC")

    choke_threshold_ms: int = Field(default=500, alias="CHOKE_THRESHOLD_MS")
    binance_symbol: str = Field(default="BTC/USDT:USDT", alias="BINANCE_SYMBOL")
    aggr_trade_ws_url: str = Field(default="wss://api.aggr.trade", alias="AGGR_TRADE_WS_URL")

    verbose: bool = Field(default=False, alias="VERBOSE")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    journal_enabled: bool = Field(default=True, alias="JOURNAL_ENABLED")
    journal_path: str = Field(default="./killtrader.journal.db", alias="JOURNAL_PATH")
    journal_all_triggers: bool = Field(default=False, alias="JOURNAL_ALL_TRIGGERS")
    journal_flush_every_n: int = Field(default=50, alias="JOURNAL_FLUSH_EVERY_N")
    journal_flush_every_sec: float = Field(default=5.0, alias="JOURNAL_FLUSH_EVERY_SEC")
    paper_position_timeout_sec: int = Field(default=3600, alias="PAPER_POSITION_TIMEOUT_SEC")

    @field_validator("risk_pct_per_trade", "max_daily_loss_pct")
    @classmethod
    def positive_percent(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("risk percentages must be positive")
        return value

    @field_validator("detector_confidence_threshold")
    @classmethod
    def confidence_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence threshold must be between 0 and 1")
        return value

    @field_validator("journal_flush_every_n", "paper_position_timeout_sec")
    @classmethod
    def positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("journal integer settings must be positive")
        return value

    @field_validator("journal_flush_every_sec")
    @classmethod
    def positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("journal flush interval must be positive")
        return value


def load_settings() -> Settings:
    return Settings()
