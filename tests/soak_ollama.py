from __future__ import annotations

import asyncio
import json
import traceback

import httpx

from killtrader.config import load_settings


async def main() -> None:
    settings = load_settings()
    url = settings.ollama_host.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
            result = {
                "ok": True,
                "url": url,
                "status_code": response.status_code,
                "body": response.text[:1000],
            }
    except Exception as exc:
        result = {"ok": False, "url": url, "error": repr(exc), "traceback": traceback.format_exc()}
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
