"""Check the PumpPortal feed in 45 seconds:  python -m sniper.probe [--config config.toml]

Subscribes to new tokens, then to trades on the first few, and prints a summary.
If it shows new tokens but zero trades, the trade stream is not working:
usually a missing or unfunded API key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets

from .config import Config
from .feed import Feed


async def probe(cfg: Config, seconds: float, n_tokens: int) -> int:
    url = cfg.feed_url_with_key()
    print("connecting to", Feed.redact(url))
    print("API key:", "set" if cfg.pumpportal_api_key else "NOT SET")
    creates = trades = 0
    other: list[str] = []
    watched: list[str] = []
    async with websockets.connect(url, ping_interval=20) as ws:
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        end = time.time() + seconds
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                break
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                other.append(str(raw)[:200]); continue
            tx = m.get("txType") if isinstance(m, dict) else None
            if tx == "create":
                creates += 1
                if len(watched) < n_tokens:
                    watched.append(m["mint"])
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [m["mint"]]}))
            elif tx in ("buy", "sell"):
                trades += 1
                if trades <= 3:
                    print("trade:", json.dumps(m)[:200])
            else:
                other.append(json.dumps(m)[:200])
        if watched:
            await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": watched}))
    print(f"\nin {seconds:.0f}s: {creates} new tokens, {trades} trades on {len(watched)} watched tokens")
    for o in other[:10]:
        print("other message:", o)
    if creates and not trades:
        print("\nRESULT: BROKEN. New tokens arrive but no trades. Check the API key and that its "
              "wallet holds at least 0.02 SOL.")
        return 1
    if not creates:
        print("\nRESULT: BROKEN. No new tokens at all. Is the server online and the URL right?")
        return 1
    print("\nRESULT: OK. Trades are flowing.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--seconds", type=float, default=45)
    ap.add_argument("--tokens", type=int, default=5)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(probe(Config.load(a.config), a.seconds, a.tokens)))
