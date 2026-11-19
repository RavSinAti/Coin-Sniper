"""SQLite log: every token seen, every evaluation verdict, every paper trade."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
  mint TEXT PRIMARY KEY, created_ts REAL, creator TEXT, name TEXT, symbol TEXT,
  baseline INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS evaluations (
  mint TEXT PRIMARY KEY, ts REAL, decision TEXT, reasons TEXT, metrics TEXT
);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY, mint TEXT, strategy TEXT, state TEXT,
  signal_ts REAL, entry_ts REAL, entry_price_sol REAL, tokens REAL, entry_usd REAL,
  sol_usd_entry REAL, pool_entry TEXT,
  exit_trigger_ts REAL, exit_trigger_return_pct REAL, exit_reason TEXT,
  exit_ts REAL, exit_price_sol REAL, exit_usd REAL, return_pct REAL,
  peak_return_pct REAL, trough_return_pct REAL, slippage_modeled INTEGER,
  pool_exit TEXT, metrics TEXT
);
CREATE INDEX IF NOT EXISTS idx_pos_state ON positions(state);
"""


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        # positions are numbered per run in memory; offset by what's already stored
        row = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM positions").fetchone()
        self.id_offset = row[0]

    def token(self, mint, created_ts, creator, name, symbol, baseline):
        self.conn.execute("INSERT OR IGNORE INTO tokens VALUES (?,?,?,?,?,?)",
                          (mint, created_ts, creator, name, symbol, int(baseline)))
        self.conn.commit()

    def evaluation(self, mint, ts, decision, reasons, metrics):
        self.conn.execute("INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?)",
                          (mint, ts, decision, json.dumps(reasons), json.dumps(metrics)))
        self.conn.commit()

    def position(self, p):
        d = asdict(p)
        d["id"] = p.id + self.id_offset
        d["metrics"] = json.dumps(d["metrics"])
        d["slippage_modeled"] = int(d["slippage_modeled"])
        cols = ",".join(d)
        marks = ",".join("?" for _ in d)
        self.conn.execute(f"INSERT OR REPLACE INTO positions ({cols}) VALUES ({marks})",
                          list(d.values()))
        self.conn.commit()
