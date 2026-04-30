# killtradR-local soak report

Date: 2026-04-30 04:01 UTC

Scope: local scaffold soak test with `TRADE_ENABLED=false`, blank BloFin credentials, public market data only, and local Ollama expected at `http://localhost:11434`.

## Environment checks

Passed:

- `python -m pip install -e .` completed successfully.
- `.env` was configured with `USE_DEMO=false`, `TRADE_ENABLED=false`, `OLLAMA_HOST=http://localhost:11434`, `OLLAMA_MODEL=deepseek-coder`, `SYMBOL=BTC-USDT`, `BINANCE_SYMBOL=BTC/USDT:USDT`, and `LOG_LEVEL=DEBUG`.
- Full import smoke check passed:
  - `killtrader.cli`
  - `killtrader.feeds.binance_ccxt`
  - `killtrader.feeds.aggr_trade` (since replaced by `killtrader.feeds.binance_coinm`)
  - `killtrader.feeds.crossref`
  - `killtrader.exchange.blofin`
  - all three detector modules
  - `killtrader.signal.llm`
  - `killtrader.ui.dashboard`
- CLI help passed:
  - `killtrader --help`
  - `killtrader run --help`
- Syntax and tests passed:
  - `python -m compileall -q killtrader tests`
  - `pytest -q` → `2 passed`
- Provider scan passed before this report was written:
  - no `anthropic` or `claude` matches in the project
- Non-real-data implementation scan passed before this report was written:
  - no runtime/test implementation matches for invented-market-data terminology were found

## Real data sources

### BloFin public market data

Reachable from this sandbox.

Probe: `python tests/soak_blofin.py`

Result:

```json
{
  "ok": true,
  "source": "blofin_ccxt_public",
  "symbol": "BTC-USDT",
  "ccxt_symbol": "BTC/USDT:USDT",
  "candles": 10,
  "last_close": 75879.5,
  "best_bid": 75878.2,
  "best_ask": 75878.3
}
```

Observed BloFin public endpoints during CLI soak:

- `GET https://openapi.blofin.com/api/v1/market/instruments` → `200`, response code `0`, message `success`
- `GET https://openapi.blofin.com/api/v1/market/books?instId=BTC-USDT&size=50` → `200`, response code `0`, message `success`

The CLI soak also captured a real BloFin order book snapshot with best book around `75894.7 / 75894.8`.

### Binance USDM public market data

Blocked from this sandbox by regional restriction.

Probe: `python tests/soak_binance.py`

Endpoint attempted by CCXT:

- `GET https://fapi.binance.com/fapi/v1/exchangeInfo`

Result:

```text
HTTP 451
Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms.
```

The project correctly raised `SourceUnavailableError('Binance perps OHLCV fetch failed')` rather than continuing with invented data.

### aggr.trade websocket

The configured endpoint did not accept a websocket upgrade from this sandbox.

Probe: `python tests/soak_aggr_trade.py`

Endpoint attempted:

- `wss://api.aggr.trade`

Result:

```text
websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 200
HTTP body: {"message":"hi"}
```

This confirms `wss://api.aggr.trade` is not the live websocket endpoint/protocol needed by the browser client. The adapter fails loudly and the endpoint remains configurable.

## Detector wiring on real data

Probe: `python tests/soak_detector_wiring.py`

Result: passed on real BloFin public market data.

```json
{
  "ok": true,
  "source": "blofin",
  "candles_processed": 120,
  "order_books_processed": 1,
  "events_emitted": 4,
  "events": [
    {"detector": "StopHuntDetector", "side": "short", "confidence": 0.8514960629921285, "trigger_price": 75752.8, "source": "blofin"},
    {"detector": "StopHuntDetector", "side": "long", "confidence": 0.85435458547993, "trigger_price": 75852.9, "source": "blofin"},
    {"detector": "StopHuntDetector", "side": "long", "confidence": 0.8497388062407819, "trigger_price": 75849.4, "source": "blofin"},
    {"detector": "StopHuntDetector", "side": "long", "confidence": 0.8322358070679119, "trigger_price": 75879.5, "source": "blofin"}
  ]
}
```

The full detector stack was instantiated and processed a real 120-candle BloFin slice plus a real L2 book snapshot without crashing. The event bus received four detector events, all from `StopHuntDetector` on the captured market slice.

## Ollama probe

Probe: `python tests/soak_ollama.py`

Endpoint attempted:

- `GET http://localhost:11434/api/tags`

Result:

```text
httpx.ConnectError: All connection attempts failed
```

This sandbox does not have the user's local Ollama instance reachable. No substitute LLM path was added.

## Dashboard render

Probe: `python tests/soak_dashboard.py`

Result:

```json
{
  "ok": true,
  "rendered_chars": 2178,
  "contains_signal_panel": true
}
```

The Rich dashboard layout rendered one empty-state frame successfully.

## Cross-reference failover behavior

Probe: `python tests/soak_crossref.py`

Method: configured BloFin with a deliberately invalid market symbol so the primary source produced a real exchange/source error. Binance and aggr.trade were then tried as configured real sources.

Result:

```json
{
  "ok": false,
  "state_after_bad_primary_symbol": "HALTED",
  "error": "NoDataAvailableError('all configured real market data sources failed; trading halted')",
  "choke_alerts_queued": 1
}
```

Observed sequence:

1. BloFin primary raised a real public order book source error.
2. Coordinator emitted `[CHOKE ALERT] BloFin source unavailable: BloFin public order book fetch failed`.
3. Binance was attempted and failed with the same regional `451` restriction seen above.
4. aggr.trade websocket failed on handshake with HTTP `200` JSON `{"message":"hi"}`.
5. Coordinator moved to `HALTED` and raised `NoDataAvailableError('all configured real market data sources failed; trading halted')`.

This is the intended fail-closed behavior when every configured real source is unavailable.

## CLI soak run

Command:

```bash
timeout 20 killtrader run --symbol BTC-USDT 2>&1 | tee /tmp/killtrader-soak.log
```

Run-log excerpt:

```text
GET https://openapi.blofin.com/api/v1/market/instruments ... Response: 200 ... {"code":"0","msg":"success" ...}
GET https://openapi.blofin.com/api/v1/market/books?instId=BTC-USDT&size=50 ... Response: 200 ... {"code":"0","msg":"success" ...}
{"latency_ms": 774.601946, "message": "[CHOKE ALERT] BloFin latency exceeded threshold; switching signal feed", "event": "choke_alert", "level": "warning"}
{"symbol": "BTC/USDT:USDT", "event": "binance_feed_connected", "level": "info"}
GET https://fapi.binance.com/fapi/v1/exchangeInfo ... Response: 451 ...
"Service unavailable from a restricted location according to 'b. Eligibility' in https://www.binance.com/en/terms."
{"error": "Binance perps order book fetch failed", "event": "binance_source_failed", "level": "warning"}
> GET / HTTP/1.1
> Host: api.aggr.trade
< HTTP/1.1 200 OK
< Content-Type: application/json; charset=utf-8
< [body] (16 bytes)
```

The CLI started, pulled real BloFin public market data, detected a BloFin latency spike over the configured `CHOKE_THRESHOLD_MS=500`, attempted Binance failover, then attempted aggr.trade. Because both configured non-BloFin real sources were unavailable from this sandbox, the feed path reached the same halt condition as the cross-reference probe.

One implementation risk surfaced: `NoDataAvailableError` is raised inside `feed_loop`, which is an `asyncio` task. The outer `try/except NoDataAvailableError` in `cli.py` does not directly await that task, so the dashboard process can sit until externally stopped instead of surfacing the halt immediately. This is not a data integrity problem, but it should be fixed so feed-task failure cancels the dashboard immediately.

## Trading safety confirmation

- `.env` has `TRADE_ENABLED=false`.
- The CLI path currently instantiates `RiskManager` but does not instantiate `Trader`, `BloFinExchange`, or any private authenticated execution path.
- The execution guard in `killtrader/execution/trader.py` blocks before `place_market_order` when `settings.trade_enabled` is false.
- The soak run used blank BloFin credentials and public market endpoints only.

No order attempts were made during this soak.

## Data integrity confirmation

No invented market data, generated ticks, placeholder books, or test-double market sources were added for this soak. When Binance, aggr.trade, or Ollama were unavailable, the probes reported the real errors and stopped that path.

## 2026-04-30 Binance COIN-M wired

The user captured a real Binance Delivery Futures websocket subscription from browser devtools:

```text
wss://dstream.binance.com/ws
{"method":"SUBSCRIBE","params":["btcusd_perp@aggTrade","btcusd_perp@forceOrder"],"id":1}
```

Implementation update:

- The non-upgrading aggr.trade placeholder adapter was removed.
- `killtrader.feeds.binance_coinm.BinanceCoinMFeed` now subscribes to `aggTrade` and `forceOrder` on Binance COIN-M.
- `LiquidationCascadeDetector` consumes real COIN-M liquidation events and emits fade signals when a biased liquidation cascade exceeds configured count/notional thresholds.
- Cross-reference failover is now `BloFin → Binance USDT-M → Binance COIN-M → HALT`.
- COIN-M basis spread is logged separately because the inverse BTC-settled contract is correlated with, but not identical to, USDT-margined perps.

Sandbox reachability:

- `wss://dstream.binance.com/ws` connected successfully.
- Captured 15 real `aggTrade` events in a 20-second window and saved a recorded real fixture at `tests/fixtures/binance_coinm_real_aggtrade_2026_04_30.json`.
- No `forceOrder` events arrived during that short window, so liquidation parser/detector tests skip their force-order fixture checks until a real liquidation fixture is captured.

## What must be validated locally

The user's local environment is expected to differ from this sandbox in the important ways that matter:

1. Ollama should be running locally with `deepseek-coder` pulled:
   - `ollama pull deepseek-coder`
   - `curl http://localhost:11434/api/tags`
2. Binance USDT-M may work from the user's own network even though this sandbox gets HTTP `451`.
3. Binance COIN-M websocket is reachable from this sandbox and should provide liquidation events whenever the market prints them.
4. BloFin credentials should be inserted into `.env` only on the user's own machine.
5. Keep `TRADE_ENABLED=false` during the first full local soak so signals and dashboard behavior can be reviewed before any execution path is enabled.

## Recommended next fixes

1. Update CLI task supervision so `feed_loop`, `signal_loop`, or `alert_loop` exceptions cancel the other tasks and surface immediately.
2. Add the signal journal requested next: persist every real detector event, Ollama decision, source state, and later realized outcome for threshold tuning.
3. Capture a real `forceOrder` fixture during an active liquidation burst and commit it for parser/detector regression coverage.
4. Re-run the full soak locally with Ollama online and unblocked Binance/BloFin network access.
