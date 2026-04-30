# killtradR-local

killtradR-local is a local-first market-maker counter-trader for BloFin perpetual futures. It hunts forced-flow traps, watches for liquidity raids, cross-checks BloFin against independent public feeds, and only wakes the local Ollama brain when the detector stack smells blood.

```
                +-----------------------------+
                | BloFin Futures / Demo API   |
                | execution + primary market  |
                +-------------+---------------+
                              |
                              v
+----------------+     +------+-------+       +------------------------+
| Binance USDT-M | --> | Honesty     | ----> | Async detector bus     |
| Binance COIN-M |     | Layer       |       | liq/stop/OB/cascade    |
+----------------+     +------+-------+       +-----------+------------+
                              |                           |
                              v                           v
                    [CHOKE ALERT]                 Local Ollama JSON
                    real-source failover          signal decision
                                                          |
                                                          v
                                                   BloFin execution
                                                   + Rich dashboard
```

## Non-negotiables

- No invented market data anywhere. If a real source fails, killtradR retries, switches to another real source, or halts trading loudly.
- No external proprietary reasoning dependency. Inference is 100% local through Ollama.
- The model is not called every tick. It only receives high-confidence detector events.

## Prerequisites

- Python 3.11+
- Ollama running locally
- Local model pulled:

```bash
ollama pull deepseek-coder
```

- BloFin API key, secret, and passphrase. Start with `USE_DEMO=true`.

## Install

```bash
cd killtradR-local
python -m venv .venv
source .venv/bin/activate
pip install -e .
# or: uv pip install -e .
```

## Configure

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
BLOFIN_API_KEY=...
BLOFIN_SECRET=...
BLOFIN_PASSPHRASE=...
USE_DEMO=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=deepseek-coder
SYMBOL=BTC-USDT
BINANCE_SYMBOL=BTC/USDT:USDT
BINANCE_COINM_SYMBOL=btcusd_perp
TRADE_ENABLED=false
```

`TRADE_ENABLED=false` blocks execution even if signals fire. Flip it only after demo validation and after you understand the risk controls.

## Run

Demo first:

```bash
USE_DEMO=true killtrader run --symbol BTC-USDT
```

Verbose thesis cards:

```bash
killtrader run --symbol BTC-USDT --verbose
```

Module entrypoint:

```bash
python -m killtrader run --symbol BTC-USDT
```

## Swap local models

Pull another Ollama model and change `.env`:

```bash
ollama pull qwen2.5-coder:32b
```

```dotenv
OLLAMA_MODEL=qwen2.5-coder:32b
```

## Signal Journal

The signal journal records the live chain from detector trigger to local-model decision to outcome so you can tune confidence thresholds with real market behavior instead of vibes. It writes to SQLite at `JOURNAL_PATH` and uses a background writer queue so detector and signal paths never wait on disk I/O.

Configure it in `.env`:

```dotenv
JOURNAL_ENABLED=true
JOURNAL_PATH=./killtrader.journal.db
JOURNAL_ALL_TRIGGERS=false
JOURNAL_FLUSH_EVERY_N=50
JOURNAL_FLUSH_EVERY_SEC=5
PAPER_POSITION_TIMEOUT_SEC=3600
```

With `TRADE_ENABLED=false`, killtradR still records trigger and decision rows. When a long/short decision appears, the paper tracker follows the position against subsequent real order-book ticks and writes a paper outcome with `is_paper=1`. No fill is treated like a live fill unless `is_paper=0`.

Journal commands:

```bash
killtrader journal stats
killtrader journal recent --limit 20 --detector liquidity_grab
killtrader journal parse-failures
```

Example empty journal stats output:

```text
       killtradR Journal Stats
┏━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Detector ┃ Samples ┃ Win Rate ┃ Avg PnL % ┃ Buckets       ┃
┡━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ no rows  │       0 │    0.00% │    0.0000 │ journal empty │
└──────────┴─────────┴──────────┴───────────┴───────────────┘
```

## Binance COIN-M Liquidation Feed

The fourth detector consumes a real Binance Delivery Futures websocket captured from the browser flow:

```text
wss://dstream.binance.com/ws
{"method":"SUBSCRIBE","params":["btcusd_perp@aggTrade","btcusd_perp@forceOrder"],"id":1}
```

`aggTrade` supplies live COIN-M trade ticks. `forceOrder` supplies forced-liquidation events, which are prime killtradR territory: when a directionally biased liquidation cascade prints, the detector treats it as forced flow and prepares a fade thesis for the local model.

`LiquidationCascadeDetector` tracks a rolling window of COIN-M liquidations and fires when notional or count thresholds are exceeded with strong directional bias. Long-liquidation cascades imply panic selling and bias a long fade; short-liquidation cascades imply squeeze buying and bias a short fade.

COIN-M is an inverse BTC-settled contract, not the same instrument as BloFin or Binance USDT-M. The prices should correlate tightly but can diverge by normal basis. killtradR logs that basis and only warns when `COINM_SPREAD_ALERT_BPS` is exceeded.

This replaces the previous aggr.trade adapter, which pointed at a non-upgrading endpoint and could not be validated as a real websocket source from this environment.

## Development

Install development tools and register the commit hooks:

```bash
pip install -e ".[dev]"
pre-commit install
```

Run the hooks manually across the repo:

```bash
pre-commit run --all-files
```

Every commit runs the same gate locally: Ruff lint/format, file hygiene checks, key-material detection, the forbidden provider/data-term guard, and `pytest -q`. If any step fails, the commit is blocked until the issue is fixed.

## Safety

Live perpetual futures trading can liquidate capital quickly. Start on BloFin demo, keep `TRADE_ENABLED=false` while validating detector behavior, and only enable real execution after reviewing logs, risk limits, and exchange permissions.

## Current adapter note

BloFin order placement is wrapped through the official SDK. BloFin websocket method names vary by installed SDK version, so the streaming methods deliberately raise a real source error until wired to the exact installed SDK interface. The cross-reference coordinator then moves to independent public feeds rather than creating invented ticks.
