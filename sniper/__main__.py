"""Run the paper-trading sniper:  python -m sniper [--config config.toml]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from .config import Config
from .db import DB
from .engine import Sniper
from .feed import Feed, fetch_sol_usd

log = logging.getLogger("sniper")


async def main(cfg: Config) -> None:
    db = DB(cfg.db_path)
    feed = Feed(cfg.feed_url_with_key())
    log.info("feed %s", Feed.redact(feed.url))
    if not cfg.pumpportal_api_key:
        log.error("NO PUMPPORTAL API KEY: new tokens will arrive but per-token trades will not, "
                  "so prices never move and no signal can fire. See README 'PumpPortal API key'.")
    sniper = Sniper(cfg, db, subscribe=feed.subscribe, unsubscribe=feed.unsubscribe)

    async def price_loop():
        while True:
            p = await asyncio.to_thread(fetch_sol_usd)
            if p:
                sniper.sol_usd = p
            else:
                log.warning("SOL price unavailable, using %.2f", sniper.sol_usd)
            await asyncio.sleep(60)

    async def tick_loop():
        last_status, last_trades = 0.0, 0
        while True:
            now = time.time()
            sniper.tick(now)
            if now - last_status > 300:
                trades = sniper.stats["trades"]
                log.info("status %s watching=%d open=%d sol=$%.2f trade_events_cost~%.4f SOL",
                         sniper.stats, len(sniper.tokens), len(sniper.paper.open), sniper.sol_usd,
                         trades * 0.01 / 10_000)
                # watchdog: tokens are arriving but no trades are -> the trade stream is dead
                if last_status and feed.connected and trades == last_trades and len(sniper.tokens) >= 10:
                    log.error("NO TRADE EVENTS in the last 5 minutes while watching %d tokens. "
                              "The trade stream is not working (missing/unfunded API key?). "
                              "Run: python -m sniper.probe", len(sniper.tokens))
                last_status, last_trades = now, trades
            await asyncio.sleep(1)

    async def consume():
        while True:
            msg = await feed.queue.get()
            sniper.handle(msg, time.time())

    await asyncio.gather(feed.run(), price_loop(), tick_loop(), consume())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main(Config.load(args.config)))
    except KeyboardInterrupt:
        pass
