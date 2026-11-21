import asyncio
import json

import websockets

from sniper.config import Config
from sniper.feed import Feed


def test_api_key_in_url_and_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PUMPPORTAL_API_KEY", raising=False)
    assert Config.load(None).feed_url_with_key() == "wss://pumpportal.fun/api/data"
    p = tmp_path / "c.toml"
    p.write_text('pumpportal_api_key = "abc"\n')
    assert Config.load(p).feed_url_with_key().endswith("?api-key=abc")
    monkeypatch.setenv("PUMPPORTAL_API_KEY", "fromenv")
    assert Config.load(p).feed_url_with_key().endswith("?api-key=fromenv")
    assert Feed.redact("wss://x/api/data?api-key=secret") == "wss://x/api/data?api-key=***"


def test_feed_logs_errors_and_only_queues_data(caplog):
    sent = []

    async def handler(ws):
        async for raw in ws:
            sent.append(json.loads(raw))
            if sent[-1]["method"] == "subscribeMigration":
                await ws.send(json.dumps({"message": "Subscribing to tokens requires an API key"}))
                await ws.send(json.dumps({"mint": "M", "txType": "buy", "solAmount": 1}))

    async def main():
        async with websockets.serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            feed = Feed(f"ws://127.0.0.1:{port}")
            task = asyncio.create_task(feed.run())
            msg = await asyncio.wait_for(feed.queue.get(), 5)
            task.cancel()
            return feed, msg

    feed, msg = asyncio.run(main())
    assert msg["txType"] == "buy" and feed.queue.empty()
    assert feed.n_other == 1
    assert "requires an API key" in caplog.text
    assert [s["method"] for s in sent][:2] == ["subscribeNewToken", "subscribeMigration"]
