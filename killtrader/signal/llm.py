from __future__ import annotations

import json
from dataclasses import asdict
from time import perf_counter

import httpx
from pydantic import ValidationError

from killtrader.config import Settings
from killtrader.core.bus import DetectorEvent
from killtrader.core.errors import SignalRejectedError
from killtrader.core.logger import get_logger
from killtrader.journal.schema import DecisionRow, TriggerRow, compact_json
from killtrader.journal.writer import JournalWriter, normalize_detector_name
from killtrader.signal.prompts import KILLTRADER_SYSTEM_PROMPT, event_prompt
from killtrader.signal.schema import TradeSignal

log = get_logger(__name__)


class OllamaSignalEngine:
    def __init__(self, settings: Settings, journal: JournalWriter | None = None) -> None:
        self.settings = settings
        self.journal = journal

    async def decide(self, event: DetectorEvent) -> TradeSignal | None:
        if event.confidence < self.settings.detector_confidence_threshold:
            log.info("event_below_threshold", detector=event.detector, confidence=event.confidence)
            if self.settings.journal_all_triggers:
                self._write_trigger(event, invoked_llm=False)
            return None
        trigger_id = self._write_trigger(event, invoked_llm=True)
        payload = self._payload(event, strict=False)
        try:
            signal = await self._request_signal(payload, trigger_id)
            signal.journal_trigger_id = trigger_id
            return signal
        except SignalRejectedError:
            log.warning("ollama_signal_retry", detector=event.detector)
            signal = await self._request_signal(self._payload(event, strict=True), trigger_id)
            signal.journal_trigger_id = trigger_id
            return signal

    def _payload(self, event: DetectorEvent, strict: bool) -> dict:
        event_dict = asdict(event)
        user_prompt = event_prompt(json.dumps(event_dict, default=str), self.settings.risk_pct_per_trade)
        if strict:
            user_prompt += "\nYour previous response failed validation. Return valid JSON only; no prose."
        return {
            "model": self.settings.ollama_model,
            "format": "json",
            "stream": False,
            "messages": [
                {"role": "system", "content": KILLTRADER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }

    async def _request_signal(self, payload: dict, trigger_id: str | None) -> TradeSignal:
        url = self.settings.ollama_host.rstrip("/") + "/api/chat"
        started = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        except Exception as exc:
            raise SignalRejectedError("local Ollama request failed") from exc
        latency_ms = int((perf_counter() - started) * 1000)
        raw_response = response.text

        try:
            body = response.json()
            content = body["message"]["content"]
            data = json.loads(content)
            signal = TradeSignal.model_validate(data)
            decision_id = self._write_decision(trigger_id, signal, latency_ms, True, content)
            signal.journal_decision_id = decision_id
            return signal
        except (KeyError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            self._write_decision(trigger_id, None, latency_ms, False, raw_response)
            raise SignalRejectedError("local Ollama response failed signal validation") from exc

    def _write_trigger(self, event: DetectorEvent, invoked_llm: bool) -> str | None:
        if self.journal is None or not self.settings.journal_enabled:
            return None
        event_dict = asdict(event)
        row = TriggerRow(
            symbol=event.symbol,
            detector=normalize_detector_name(event.detector),
            confidence=event.confidence,
            invoked_llm=invoked_llm,
            feed_source=event.source,
            market_snapshot_json=compact_json(event_dict),
            detector_meta_json=compact_json(event.features),
        )
        self.journal.enqueue(row)
        return row.id

    def _write_decision(self, trigger_id: str | None, signal: TradeSignal | None, latency_ms: int, parse_ok: bool, raw_response: str) -> str | None:
        if self.journal is None or trigger_id is None or not self.settings.journal_enabled:
            return None
        row = DecisionRow(
            trigger_id=trigger_id,
            model=self.settings.ollama_model,
            action=signal.action if signal else "pass",
            confidence=signal.confidence if signal else 0.0,
            entry=signal.entry if signal else None,
            stop=signal.stop if signal else None,
            tp1=signal.tp1 if signal else None,
            tp2=signal.tp2 if signal else None,
            size_pct=signal.size_pct if signal else None,
            reasoning=signal.reasoning if signal else None,
            market_maker_thesis=signal.market_maker_thesis if signal else None,
            latency_ms=latency_ms,
            parse_ok=parse_ok,
            raw_response=raw_response,
        )
        self.journal.enqueue(row)
        return row.id
