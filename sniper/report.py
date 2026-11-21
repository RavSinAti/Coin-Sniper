"""Performance report:  python -m sniper.report [--db data/sniper.db] [--since-hours 24]

A "win" is a closed trade whose REALIZED return (after fees, slippage and
latency) is above the take-profit threshold, not a coin that merely touched it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
from collections import Counter

from .config import Config


def summarize(rows: list[sqlite3.Row], win_pct: float) -> dict:
    rets = [r["return_pct"] for r in rows]
    pnl = [r["exit_usd"] - r["entry_usd"] for r in rows]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in pnl:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        "trades": len(rows),
        "win_rate_pct": round(100 * sum(r > win_pct for r in rets) / len(rets), 1) if rets else 0,
        "profitable_pct": round(100 * sum(r > 0 for r in rets) / len(rets), 1) if rets else 0,
        "avg_return_pct": round(statistics.mean(rets), 2) if rets else 0,
        "median_return_pct": round(statistics.median(rets), 2) if rets else 0,
        "total_pnl_usd": round(sum(pnl), 2),
        "max_drawdown_usd": round(dd, 2),
        "touched_tp_pct": round(100 * sum(r["peak_return_pct"] >= win_pct for r in rows) / len(rows), 1) if rows else 0,
        "exit_reasons": dict(Counter(r["exit_reason"] for r in rows)),
        "unmodeled_slippage": sum(1 for r in rows if not r["slippage_modeled"]),
    }


def report(db_path: str, since_hours: float | None, win_pct: float) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    since = time.time() - since_hours * 3600 if since_hours else 0
    out = {"window_hours": since_hours or "all"}
    for strat in ("signal", "baseline"):
        rows = conn.execute("SELECT * FROM positions WHERE state='closed' AND strategy=? "
                            "AND exit_ts>=? ORDER BY exit_ts", (strat, since)).fetchall()
        out[strat] = summarize(rows, win_pct)
    out["open_positions"] = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE state!='closed'").fetchone()[0]
    out["tokens_seen"] = conn.execute(
        "SELECT COUNT(*) FROM tokens WHERE created_ts>=?", (since,)).fetchone()[0]
    reasons = Counter()
    for (r,) in conn.execute("SELECT reasons FROM evaluations WHERE decision='reject' AND ts>=?", (since,)):
        reasons.update(json.loads(r))
    out["reject_reasons"] = dict(reasons.most_common())
    s, b = out["signal"], out["baseline"]
    if s["trades"] and b["trades"]:
        out["edge_vs_baseline_pct"] = round(s["avg_return_pct"] - b["avg_return_pct"], 2)
    if s["trades"] < 200:
        out["verdict"] = f"NOT ENOUGH DATA: {s['trades']}/200 signal trades. Do not trust these numbers yet."
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=Config().db_path)
    ap.add_argument("--since-hours", type=float, default=None)
    a = ap.parse_args()
    print(json.dumps(report(a.db, a.since_hours, Config().take_profit_pct), indent=2))
