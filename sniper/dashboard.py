"""Read-only web dashboard over the paper-trade log.

    python -m sniper.dashboard [--db data/sniper.db] [--host 127.0.0.1] [--port 8050]

Standard library only. It opens SQLite in read-only mode, so it can never
change the log and can run next to the bot. It binds to localhost by default.
On a VPS, reach it through an SSH tunnel:

    ssh -L 8050:localhost:8050 you@your-vps   # then open http://localhost:8050
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config
from .report import report_conn

CLOSED_LIMIT = 500
WINDOWS = {"24h": 24.0, "7d": 168.0, "30d": 720.0, "all": None}


def connect_ro(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise FileNotFoundError(db_path)
    conn = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def snapshot(db_path: str, window: str, cfg: Config) -> dict:
    hours = WINDOWS.get(window, None)
    since = time.time() - hours * 3600 if hours else 0.0
    conn = connect_ro(db_path)
    try:
        stats = report_conn(conn, hours, cfg.take_profit_pct)
        tok = "LEFT JOIN tokens t ON t.mint = p.mint"
        cols = ("p.id, p.mint, p.strategy, p.state, p.signal_ts, p.entry_ts, p.entry_usd, "
                "p.exit_ts, p.exit_usd, p.return_pct, p.exit_reason, p.exit_trigger_return_pct, "
                "p.peak_return_pct, p.trough_return_pct, p.slippage_modeled, p.pool_entry, "
                "p.pool_exit, t.name, t.symbol")
        open_rows = conn.execute(
            f"SELECT {cols} FROM positions p {tok} WHERE p.state != 'closed' "
            "ORDER BY p.signal_ts DESC").fetchall()
        closed_rows = conn.execute(
            f"SELECT {cols} FROM positions p {tok} WHERE p.state = 'closed' AND p.exit_ts >= ? "
            "ORDER BY p.exit_ts DESC LIMIT ?", (since, CLOSED_LIMIT)).fetchall()
        curve = {}
        for strat in ("signal", "baseline"):
            eq, pts = 0.0, []
            for r in conn.execute(
                    "SELECT exit_ts, exit_usd - entry_usd AS pnl FROM positions "
                    "WHERE state='closed' AND strategy=? AND exit_ts >= ? ORDER BY exit_ts",
                    (strat, since)):
                eq += r["pnl"]
                pts.append([r["exit_ts"], round(eq, 2)])
            curve[strat] = pts
    finally:
        conn.close()
    return {
        "generated_ts": time.time(),
        "window": window,
        "rules": {"position_usd": cfg.position_usd, "take_profit_pct": cfg.take_profit_pct,
                  "stop_loss_pct": cfg.stop_loss_pct, "max_hold_min": cfg.max_hold_s / 60},
        "stats": stats,
        "open": [dict(r) for r in open_rows],
        "closed": [dict(r) for r in closed_rows],
        "closed_limit": CLOSED_LIMIT,
        "equity": curve,
    }


def make_handler(db_path: str, cfg: Config):
    page = resources.files("sniper").joinpath("dashboard.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif url.path == "/api/data":
                window = parse_qs(url.query).get("window", ["all"])[0]
                if window not in WINDOWS:
                    window = "all"
                try:
                    body = json.dumps(snapshot(db_path, window, cfg)).encode()
                    self._send(200, body, "application/json")
                except FileNotFoundError:
                    self._send(503, json.dumps({"error": f"no database at {db_path} yet; "
                                                "start the bot first"}).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def log_message(self, fmt, *args):  # keep the bot's terminal quiet
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--db", default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8050)
    a = ap.parse_args()
    cfg = Config.load(a.config)
    db = a.db or cfg.db_path
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(db, cfg))
    print(f"dashboard on http://{a.host}:{a.port}  (db: {db}, read-only)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
