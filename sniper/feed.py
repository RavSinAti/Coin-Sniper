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
        self.n_other = 0        # messages that are not token/trade data (acks, errors)
        self.connected = False

    @staticmethod
    def redact(url: str) -> str:
        return url.split("api-key=")[0] + ("api-key=***" if "api-key=" in url else "")

    def _note_other(self, msg) -> None:
        """PumpPortal reports errors (e.g. missing API key) as plain JSON messages.
        They used to be dropped silently; now they are logged."""
        self.n_other += 1
        if self.n_other <= 10 or self.n_other % 1000 == 0:
            log.warning("feed message (not trade data) #%d: %s", self.n_other,
                        json.dumps(msg)[:300] if not isinstance(msg, str) else msg[:300])

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
                    self.connected = True
                    backoff = 1
                    await self._send({"method": "subscribeNewToken"})
                    await self._send({"method": "subscribeMigration"})
                    if self.watched:
                        await self._send({"method": "subscribeTokenTrade", "keys": list(self.watched)})
                    log.info("connected, re-watching %d tokens", len(self.watched))
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            self._note_other(raw if isinstance(raw, str) else repr(raw))
                            continue
                        if isinstance(msg, dict) and msg.get("mint") and msg.get("txType"):
                            await self.queue.put(msg)
                        else:
                            self._note_other(msg)
            except Exception as e:
                log.warning("feed dropped (%s); reconnecting in %ss", e, backoff)
            self.ws = None
            self.connected = False
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
