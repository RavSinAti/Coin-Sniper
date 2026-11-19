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
    feed = Feed(cfg.feed_url)
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
        last_status = 0.0
        while True:
            now = time.time()
            sniper.tick(now)
            if now - last_status > 300:
                last_status = now
                log.info("status %s watching=%d open=%d sol=$%.2f", sniper.stats,
                         len(sniper.tokens), len(sniper.paper.open), sniper.sol_usd)
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
