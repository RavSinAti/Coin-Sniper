"""PumpPortal websocket client with reconnect + resubscribe, and a SOL/USD poller."""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

import websockets

log = logging.getLogger("feed")
WSOL = "So11111111111111111111111111111111111111112"


class Feed:
    """One connection (PumpPortal asks clients not to open many)."""

    def __init__(self, url: str):
        self.url = url
        self.ws = None
        self.watched: set[str] = set()
        self.queue: asyncio.Queue = asyncio.Queue()

    async def _send(self, payload: dict) -> None:
        if self.ws is not None:
            try:
                await self.ws.send(json.dumps(payload))
            except Exception as e:  # reconnect loop will resubscribe
                log.warning("send failed: %s", e)

    def subscribe(self, mints: list[str]) -> None:
        self.watched.update(mints)
        asyncio.get_running_loop().create_task(
            self._send({"method": "subscribeTokenTrade", "keys": list(mints)}))

    def unsubscribe(self, mints: list[str]) -> None:
        self.watched.difference_update(mints)
        asyncio.get_running_loop().create_task(
            self._send({"method": "unsubscribeTokenTrade", "keys": list(mints)}))

    async def run(self) -> None:
        backoff = 1
        while True:
            try:
                async with websockets.connect(self.url, ping_interval=20, max_size=2**22) as ws:
                    self.ws = ws
                    backoff = 1
                    await self._send({"method": "subscribeNewToken"})
                    await self._send({"method": "subscribeMigration"})
                    if self.watched:
                        await self._send({"method": "subscribeTokenTrade", "keys": list(self.watched)})
                    log.info("connected, re-watching %d tokens", len(self.watched))
                    async for raw in ws:
                        try:
                            await self.queue.put(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                log.warning("feed dropped (%s); reconnecting in %ss", e, backoff)
            self.ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def fetch_sol_usd(timeout: float = 5.0) -> float | None:
    sources = [
        (f"https://lite-api.jup.ag/price/v3?ids={WSOL}", lambda d: d[WSOL]["usdPrice"]),
        ("https://api.coinbase.com/v2/prices/SOL-USD/spot", lambda d: d["data"]["amount"]),
    ]
    for url, pick in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "coin-sniper/0.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                price = float(pick(json.loads(r.read())))
                if price > 0:
                    return price
        except Exception as e:
            log.debug("price source %s failed: %s", url, e)
    return None
