#!/usr/bin/env python3
"""
killtradR – autonomous MM-style scalping bot for BloFin.
Replaces Claude Code with any open-source LLM (Ollama/LM Studio/Groq/etc.)
"""

import asyncio
import json
import os
import time
import logging
import dataclasses
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

# Scheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# OpenAI client (works with any OpenAI-compatible endpoint)
from openai import AsyncOpenAI

# MCP client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
BLOFIN_ENV = {
    "BLOFIN_API_KEY": os.getenv("BLOFIN_API_KEY"),
    "BLOFIN_API_SECRET": os.getenv("BLOFIN_API_SECRET"),
    "BLOFIN_PASSPHRASE": os.getenv("BLOFIN_PASSPHRASE"),
    "BLOFIN_BASE_URL": os.getenv("BLOFIN_BASE_URL", "https://openapi.blofin.com"),  # Fixed: removed trailing space
}

LLM_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.1:8b")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")  # LM Studio/Ollama ignore this

# Trading constants
MAX_TRADE_DURATION_SEC = 300   # 5 minutes
TRAIL_PCT_MAJOR = 0.4 / 100    # 0.4%
TRAIL_PCT_ALT = 1.5 / 100      # 1.5%
MARGIN_CLOSE_THRESHOLD = -0.5  # -50% margin
NOTIONAL_PER_TRADE = 250.0     # $250

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("session.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("killtradR")

# -----------------------------------------------------------------------------
# MCP Client (BloFin)
# -----------------------------------------------------------------------------
_mcp_session: Optional[ClientSession] = None


async def get_mcp_session() -> ClientSession:
    """Create or reuse the global MCP session."""
    global _mcp_session
    if _mcp_session is None or _mcp_session._read_task is None:
        server_params = StdioServerParameters(
            command="npx", args=["-y", "blofin-mcp"], env=BLOFIN_ENV
        )
        read, write = await stdio_client(server_params).__aenter__()
        session = ClientSession(read, write)
        await session.initialize()
        _mcp_session = session
    return _mcp_session


async def call_mcp_tool(tool_name: str, arguments: dict = None) -> dict:
    """Call a tool on the BloFin MCP server and return the result."""
    session = await get_mcp_session()
    result = await session.call_tool(tool_name, arguments or {})
    # result.content is a list of TextContent objects
    text = ""
    if result.content:
        text = result.content[0].text
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}

# -----------------------------------------------------------------------------
# BloFin helper functions
# -----------------------------------------------------------------------------


async def get_balance():
    return await call_mcp_tool("get_balance")


async def get_positions():
    return await call_mcp_tool("get_positions")


async def get_funding_rate(inst_id: str):
    return await call_mcp_tool("get_funding_rate", {"instId": inst_id})


async def get_candlesticks(inst_id: str, bar: str = "1m", limit: str = "5"):
    return await call_mcp_tool("get_candlesticks", {"instId": inst_id, "bar": bar, "limit": limit})


async def get_instruments(inst_id: str):
    return await call_mcp_tool("get_instruments", {"instId": inst_id})


async def get_tickers(inst_id: str = None):
    if inst_id:
        return await call_mcp_tool("get_tickers", {"instId": inst_id})
    # full tickers (can be huge) – we'll handle in scan later
    return await call_mcp_tool("get_tickers")


async def set_leverage(inst_id: str, leverage: str, margin_mode: str = "cross"):
    return await call_mcp_tool("set_leverage", {"instId": inst_id, "leverage": leverage, "marginMode": margin_mode})


async def set_margin_mode(margin_mode: str):
    return await call_mcp_tool("set_margin_mode", {"marginMode": margin_mode})


async def place_order(inst_id: str, margin_mode: str, position_side: str, side: str, order_type: str, size: str):
    return await call_mcp_tool("place_order", {
        "instId": inst_id, "marginMode": margin_mode, "positionSide": position_side,
        "side": side, "orderType": order_type, "size": size
    })


async def place_tpsl(inst_id: str, margin_mode: str, position_side: str, side: str,
                     size: str, tp_trigger_price: str, tp_order_price: str,
                     sl_trigger_price: str, sl_order_price: str):
    return await call_mcp_tool("place_tpsl", {
        "instId": inst_id, "marginMode": margin_mode, "positionSide": position_side,
        "side": side, "size": size,
        "tpTriggerPrice": tp_trigger_price, "tpOrderPrice": tp_order_price,
        "slTriggerPrice": sl_trigger_price, "slOrderPrice": sl_order_price
    })


async def cancel_tpsl(orders: str):  # stringified JSON array of {instId, tpslId}
    return await call_mcp_tool("cancel_tpsl", {"orders": orders})


async def close_position(inst_id: str, margin_mode: str = "cross", position_side: str = "net"):
    return await call_mcp_tool("close_position", {"instId": inst_id, "marginMode": margin_mode, "positionSide": position_side})


async def get_mark_price(inst_id: str):
    return await call_mcp_tool("get_mark_price", {"instId": inst_id})

# -----------------------------------------------------------------------------
# Global state (updated by scans)
# -----------------------------------------------------------------------------


latest_scan: Dict[str, Any] = {
    "tier1": [],   # list of dicts: instId, price, chg24h, vol, funding, score
    "tier2": [],
    "tier3": [],
    "timestamp": None
}

# -----------------------------------------------------------------------------
# Trading state (for trailing stop)
# -----------------------------------------------------------------------------


@dataclasses.dataclass
class ActiveTrade:
    inst_id: str
    side: str  # "buy" or "sell" – but BloFin uses net; we treat buy=long, sell=short
    entry_price: float
    mark_price: float
    margin: float
    quantity: float  # number of contracts
    leverage: float
    tp_price: float
    sl_price: float
    entry_time: datetime
    high_watermark: float  # best price reached for long (highest), for short (lowest)
    last_trail_time: Optional[float] = None
    tpsl_id: Optional[str] = None  # ID of the TP/SL order (if available)
    entry_equity: Optional[float] = None


_current_trade: Optional[ActiveTrade] = None

# -----------------------------------------------------------------------------
# System prompt (your exact rules)
# -----------------------------------------------------------------------------
SYSTEM_PROMPT = """
You are killtradR, an autonomous MM-style scalping bot on BloFin.
You have access to real-time market data and can execute trades.

Your job: When there is NO open position, pick the best pair to trade based on the market snapshot provided. Output ONLY a JSON object with "action": "enter", "instId": "...", "side": "buy"/"sell". Do NOT output anything else.

Key market-maker patterns you look for:
- Massive stop-hunt flushes (100k+ contract candle) followed by a recovery candle → Fade the flush (go long on a flush low, go short on a flush high).
- Extreme negative funding (shorts paying longs > 0.2%) → long bias.
- Extreme positive funding → short bias.
- Avoid pairs that are within 2% of their 24h high if going long (they're likely distributing).
- Prefer high volume (>5M) and high daily change (>30% for tier 1).

Rules (the script handles execution; you only choose entry):
- Only one trade at a time. If a position is open, you will not be asked.
- Max trade duration: 5 minutes.
- -50% margin = instant closure.
- Trailing stop is used.
- Fixed notional size: $250 per trade.

Be ruthless. Don't chase a dying pair. If nothing looks clean, say {"action": "idle"}.
"""

# We'll append a "Lessons from recent sessions" section to the prompt when loaded.

# -----------------------------------------------------------------------------
# LLM decision helper
# -----------------------------------------------------------------------------
llm_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


async def ask_llm_for_entry(scan_data: dict) -> dict:
    """
    Given a market snapshot (tier1/2/3 data), return a decision JSON.
    Returns dict with "action": "enter"/"idle", and if enter: "instId", "side".
    """
    # Build a compact text summary of top candidates
    snapshot_text = "Current market snapshot (tier 1 & 2):\n"
    for pair in scan_data.get("tier1", []):
        snapshot_text += (
            f"- {pair['instId']}: price={pair['price']}, "
            f"24h chg={pair['chg24h']}%, vol={pair.get('vol', 'N/A')}, "
            f"funding={pair['funding']}, recent action={pair.get('note', '')}\n"
        )
    for pair in scan_data.get("tier2", []):
        snapshot_text += (
            f"- {pair['instId']}: price={pair['price']}, "
            f"24h chg={pair['chg24h']}%, vol={pair.get('vol', 'N/A')}, "
            f"funding={pair['funding']}, recent action={pair.get('note', '')}\n"
        )
    if not snapshot_text.strip():
        snapshot_text = "No high-conviction pairs right now.\n"

    # Load recent lessons
    lessons = load_lessons()

    full_prompt = f"""{SYSTEM_PROMPT}

{lessons}

{snapshot_text}

Decide. Output ONLY JSON.
"""
    try:
        response = await llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": full_prompt.strip()}],
            temperature=0.1,
            max_tokens=200
        )
        reply = response.choices[0].message.content.strip()
        # Extract JSON from response (it may be wrapped in ```)
        if reply.startswith("```"):
            lines = reply.split("\n", 1)
            if len(lines) > 1:
                reply = lines[1]
            if reply.endswith("```"):
                reply = reply[:-3]
        decision = json.loads(reply)
        return decision
    except Exception as e:
        logger.error(f"LLM decision failed: {e}")
        return {"action": "idle"}

# -----------------------------------------------------------------------------
# Lessons storage (memory)
# -----------------------------------------------------------------------------
LESSONS_FILE = "lessons.json"


def load_lessons() -> str:
    try:
        with open(LESSONS_FILE, "r") as f:
            data = json.load(f)
        return "\n".join(data[-5:])  # last 5 lessons
    except Exception:
        return ""


def save_lesson(text: str):
    try:
        with open(LESSONS_FILE, "r") as f:
            lessons = json.load(f)
    except Exception:
        lessons = []
    lessons.append(f"{datetime.utcnow().isoformat()}: {text}")
    with open(LESSONS_FILE, "w") as f:
        json.dump(lessons[-50:], f)  # keep last 50

# -----------------------------------------------------------------------------
# Tier scan implementation (called by scheduler)
# -----------------------------------------------------------------------------


async def scan_tiers(tier: str):
    """
    Update the global latest_scan dict with top pairs from each tier.
    We'll only process Tier 1 (5m) and Tier 2 (10m) and Tier 3 (15m) separately,
    but the scheduler will call this with the appropriate tier.
    For simplicity, we'll fetch all tickers and filter here, but BloFin's
    get_tickers without param may be huge. We'll limit to a curated list or use
    the full one carefully.
    """
    global latest_scan
    # Fetch all tickers (might be heavy; we can cache for 30m full scan)
    # For speed, we'll get a pre-defined list of known volatile pairs.
    # You can extend this list based on your experience.
    symbols_of_interest = [
        "BTC-USDT", "ETH-USDT", "SOL-USDT", "EDU-USDT", "RAVE-USDT",
        "GUN-USDT", "ORDI-USDT", "PORTAL-USDT", "ONDO-USDT", "MEME-USDT"
    ]
    results = []
    for sym in symbols_of_interest:
        try:
            ticker = await get_tickers(sym)
            if ticker.get("code") == "0":
                data = ticker.get("data", [{}])[0]
                results.append(data)
        except Exception:
            continue
    # Sort by absolute 24h change * volume
    def score(item):
        try:
            chg = float(item.get("chg24h", "0").replace("%", ""))
            vol = float(item.get("volCurrency24h", "0"))
            return abs(chg) * vol
        except Exception:
            return 0
    results.sort(key=score, reverse=True)
    # Classify into tiers
    tier1 = []
    tier2 = []
    for item in results:
        chg_str = item.get("chg24h", "0")
        if isinstance(chg_str, str):
            chg = float(chg_str.replace("%", ""))
        else:
            chg = float(chg_str)
        vol = float(item.get("volCurrency24h", "0"))
        if vol < 5_000_000:
            continue  # skip low volume
        if abs(chg) > 30:
            # Get funding rate
            inst_id = item["instId"]
            try:
                fr = await get_funding_rate(inst_id)
                funding = fr["data"][0]["fundingRate"]
            except Exception:
                funding = "N/A"
            # Grab last few candles for a note
            try:
                candles = await get_candlesticks(inst_id, "1m", "5")
                # look for flush
                candle_vols = [float(c[5]) for c in candles.get("data", []) if len(c) > 5]
                max_vol = max(candle_vols) if candle_vols else 0
                note = f"max 1m vol: {max_vol:.0f}" if max_vol else ""
            except Exception:
                note = ""
            tier1.append({
                "instId": inst_id,
                "price": item.get("last"),
                "chg24h": f"{chg:.1f}%",
                "vol": f"{vol:.0f}",
                "funding": funding,
                "note": note
            })
        elif abs(chg) >= 10:
            tier2.append({
                "instId": item["instId"],
                "price": item.get("last"),
                "chg24h": f"{chg:.1f}%",
                "vol": f"{vol:.0f}"
            })
    latest_scan["tier1"] = tier1
    latest_scan["tier2"] = tier2
    latest_scan["timestamp"] = datetime.utcnow().isoformat()
    logger.info(f"Scan completed: Tier1: {len(tier1)} pairs, Tier2: {len(tier2)} pairs")

# -----------------------------------------------------------------------------
# Position monitor & trade execution (1-minute loop)
# -----------------------------------------------------------------------------


async def monitor_and_trade():
    global _current_trade
    try:
        balance = await get_balance()
        positions = await get_positions()
    except Exception as e:
        logger.error(f"Failed to fetch balance/positions: {e}")
        return

    equity = float(balance["data"][0]["equity"])
    # Parse positions
    pos_list = positions.get("data", [])
    if len(pos_list) == 0:
        # No position – ask LLM for entry
        if _current_trade is not None:
            # There was a trade but it closed; log result
            pnl = equity - (_current_trade.entry_equity or equity)  # rough
            logger.info(f"Position closed. PnL: {pnl:.2f} USDT. Equity: {equity:.2f}")
            _current_trade = None
        # Get entry decision
        decision = await ask_llm_for_entry(latest_scan)
        if decision.get("action") == "enter":
            inst_id = decision["instId"]
            side = decision["side"]
            # Determine max leverage from instrument info
            try:
                instr = await get_instruments(inst_id)
                max_leverage = int(float(instr["data"][0]["leverage"]))
            except Exception:
                max_leverage = 20  # safe default for alts
            # Set leverage to max
            await set_leverage(inst_id, str(max_leverage), "cross")
            # Calculate size for $250 notional
            mark_price_resp = await get_mark_price(inst_id)
            mark_price = float(mark_price_resp["data"][0]["markPrice"])
            size_contracts = NOTIONAL_PER_TRADE / mark_price
            # Round to minimum lot size (BloFin uses integer contracts for some, check)
            # For simplicity, we'll use string with 4 decimal for BTC, else 0 decimal
            if "BTC" in inst_id:
                size_str = f"{size_contracts:.4f}"
            else:
                size_str = str(int(round(size_contracts)))
            # Place market order
            await place_order(inst_id, "cross", "net", side, "market", size_str)
            # Entry price
            entry_price = mark_price  # approximate; actual fill may differ
            # Calculate TP and SL based on trail distance
            if inst_id.startswith(("BTC", "ETH", "SOL")):
                tp_offset = 0.008  # 0.8% TP
                sl_offset = 0.004  # 0.4% initial SL (will trail)
            else:
                tp_offset = 0.04   # 4% TP for alts
                sl_offset = 0.015  # 1.5% initial SL
            if side == "buy":
                tp_price = entry_price * (1 + tp_offset)
                sl_price = entry_price * (1 - sl_offset)
            else:
                tp_price = entry_price * (1 - tp_offset)
                sl_price = entry_price * (1 + sl_offset)
            # Place TP/SL
            tpsl_side = "sell" if side == "buy" else "buy"
            await place_tpsl(inst_id, "cross", "net",
                             tpsl_side,
                             "-1",
                             str(round(tp_price, 8)), "-1",
                             str(round(sl_price, 8)), "-1")
            # Create active trade object
            _current_trade = ActiveTrade(
                inst_id=inst_id, side=side, entry_price=entry_price,
                mark_price=entry_price, margin=NOTIONAL_PER_TRADE / max_leverage,
                quantity=float(size_str), leverage=max_leverage,
                tp_price=tp_price, sl_price=sl_price,
                entry_time=datetime.utcnow(),
                high_watermark=entry_price,
                entry_equity=equity
            )
            logger.info(f"ENTER {side.upper()} {inst_id} @ {entry_price:.8f} size={size_str} x{max_leverage}")
            save_lesson(f"Entered {side} {inst_id} at {entry_price}")
        else:
            logger.info("No entry signal. Idling.")
        return

    # We have an open position (assume one at a time)
    pos = pos_list[0]
    inst_id = pos["instId"]
    pos_side = pos.get("posSide", "net")
    side = "buy" if pos_side == "long" else "sell"
    entry_price = float(pos["avgPx"])
    mark_price = float(pos["markPx"])
    margin = float(pos["margin"])
    unrealized_pnl = float(pos["upl"])
    margin_ratio = unrealized_pnl / margin if margin > 0 else 0
    entry_time = None
    if _current_trade and _current_trade.inst_id == inst_id:
        entry_time = _current_trade.entry_time
        _current_trade.mark_price = mark_price
    else:
        # Reconstruct trade from position (session restart)
        entry_time = datetime.utcnow()  # we don't know exact time, assume fresh

    age = (datetime.utcnow() - entry_time).total_seconds()

    # RULE: -50% margin → close instantly
    if margin_ratio <= MARGIN_CLOSE_THRESHOLD:
        logger.warning(f"Margin ratio {margin_ratio:.1%} <= -50%. CLOSING {inst_id}.")
        await close_position(inst_id)
        save_lesson(f"Closed {inst_id} at -50% margin rule. Equity: {equity:.2f}")
        _current_trade = None
        return

    # RULE: 5-minute hard close
    if age >= MAX_TRADE_DURATION_SEC:
        logger.info(f"Position age {age:.0f}s reached 5 min. CLOSING {inst_id}.")
        await close_position(inst_id)
        save_lesson(f"Closed {inst_id} at 5-minute hard limit. PnL: {unrealized_pnl:.2f} USDT")
        _current_trade = None
        return

    # Trailing stop logic
    if _current_trade and unrealized_pnl > 0:
        # Update high watermark
        if side == "buy" and mark_price > _current_trade.high_watermark:
            _current_trade.high_watermark = mark_price
        elif side == "sell" and mark_price < _current_trade.high_watermark:  # Fixed: was "short"
            _current_trade.high_watermark = mark_price

        # Check if we should trail
        if side == "buy":
            trail_threshold = _current_trade.entry_price * 0.002
            trail = (_current_trade.high_watermark - _current_trade.entry_price) >= trail_threshold
        else:  # side == "sell"
            trail_threshold = _current_trade.entry_price * 0.002
            trail = (_current_trade.entry_price - _current_trade.high_watermark) >= trail_threshold

        if trail:
            # Cancel old TP/SL
            if _current_trade.tpsl_id:
                try:
                    await cancel_tpsl(json.dumps([{"instId": inst_id, "tpslId": _current_trade.tpsl_id}]))
                except Exception:
                    pass
            # Calculate new SL
            if inst_id.startswith(("BTC", "ETH", "SOL")):
                trail_dist = TRAIL_PCT_MAJOR
            else:
                trail_dist = TRAIL_PCT_ALT
            if side == "buy":
                new_sl = _current_trade.high_watermark * (1 - trail_dist)
            else:
                new_sl = _current_trade.high_watermark * (1 + trail_dist)
            # TP remains unchanged
            tp = _current_trade.tp_price
            # Place new TP/SL
            tpsl_side = "sell" if side == "buy" else "buy"
            resp = await place_tpsl(inst_id, "cross", "net",
                                    tpsl_side,
                                    "-1",
                                    str(round(tp, 8)), "-1",
                                    str(round(new_sl, 8)), "-1")
            # Store new TP/SL ID
            try:
                if resp.get("code") == "0":
                    tpsl_id = resp["data"].get("tpslId") or resp["data"].get("algoId")
                    _current_trade.tpsl_id = tpsl_id
            except Exception:
                pass
            _current_trade.sl_price = new_sl
            logger.info(f"Trailing stop updated: new SL={new_sl:.8f} (locked profit)")

    # Log status
    logger.info(
        f"POS {inst_id} {side} | entry={entry_price:.2f} mark={mark_price:.2f} "
        f"PnL={unrealized_pnl:.2f} ({margin_ratio:.1%}) age={age:.0f}s"
    )

# -----------------------------------------------------------------------------
# Main scheduler
# -----------------------------------------------------------------------------


async def main():
    logger.info("Starting killtradR bot...")
    # Ensure margin mode is cross for all (set once)
    try:
        await set_margin_mode("cross")
    except Exception:
        pass

    scheduler = AsyncIOScheduler()
    # Position monitor every 60 seconds
    scheduler.add_job(monitor_and_trade, 'interval', seconds=60, id='monitor')
    # Tier scans
    scheduler.add_job(scan_tiers, 'interval', minutes=5, args=['tier1'], id='scan_tier1')
    scheduler.add_job(scan_tiers, 'interval', minutes=10, args=['tier2'], id='scan_tier2')
    # We can combine tier3 with full scan
    scheduler.add_job(scan_tiers, 'interval', minutes=15, args=['tier3'], id='scan_tier3')
    scheduler.start()

    # Run initial scan immediately
    await scan_tiers('tier1')
    # Keep alive
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown()
        if _current_trade:
            # Close any open position on exit? Optional.
            pass


if __name__ == "__main__":
    asyncio.run(main())
