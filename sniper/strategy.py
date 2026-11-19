"""Rug filters + entry signals. Returns (passed, reasons_it_failed, metrics)."""
from __future__ import annotations

from .config import Config
from .token_state import TokenState


def evaluate(tok: TokenState, now: float, cfg: Config) -> tuple[bool, list[str], dict]:
    s = cfg.total_supply
    n_buys, sol_in, sol_out = tok.flow(now, 60)
    ratio = sol_in / sol_out if sol_out > 0 else (float("inf") if sol_in > 0 else 0.0)
    m = {
        "age_s": round(tok.age(now), 1),
        "dev_share_pct": round(tok.dev_share_pct(s), 2),
        "dev_sold": tok.dev_sold,
        "top10_share_pct": round(tok.top10_share_pct(s), 2),
        "snipers": len(tok.snipers),
        "unique_buyers": len(tok.buyers),
        "buys_60s": n_buys,
        "buy_sell_ratio_60s": round(ratio, 2) if ratio != float("inf") else 999.0,
        "mcap_sol": round(tok.mcap_sol, 2),
        "pool": tok.pool,
    }
    fails: list[str] = []
    # rug filters
    if m["dev_share_pct"] > cfg.max_dev_share_pct:
        fails.append("dev_share")
    if tok.dev_sold and not cfg.dev_may_sell:
        fails.append("dev_sold")
    if m["top10_share_pct"] > cfg.max_top10_share_pct:
        fails.append("top10_concentration")
    if m["snipers"] > cfg.max_sniper_count:
        fails.append("snipers")
    # entry signals
    if tok.age(now) < cfg.min_age_s:
        fails.append("too_young")
    if m["unique_buyers"] < cfg.min_unique_buyers:
        fails.append("few_buyers")
    if n_buys < cfg.min_buys_last_60s:
        fails.append("low_buy_velocity")
    if ratio < cfg.min_buy_sell_ratio:
        fails.append("sell_pressure")
    if not (cfg.min_mcap_sol <= tok.mcap_sol <= cfg.max_mcap_sol):
        fails.append("mcap_out_of_range")
    return (not fails, fails, m)
