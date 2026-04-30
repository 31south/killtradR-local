class KillTraderError(Exception):
    """Base exception for killtradR."""


class SourceUnavailableError(KillTraderError):
    """Raised when a real market data source cannot provide data."""


class NoDataAvailableError(KillTraderError):
    """Raised when all configured real market data sources are unavailable."""


class SignalRejectedError(KillTraderError):
    """Raised when local LLM output cannot be parsed or validated."""


class RiskLimitExceededError(KillTraderError):
    """Raised when a trade would violate configured risk controls."""


class ExchangeExecutionError(KillTraderError):
    """Raised when exchange execution fails after retries."""


class DemoNotSupportedError(KillTraderError):
    """Raised when USE_DEMO=true is requested but the installed SDK cannot support it."""
