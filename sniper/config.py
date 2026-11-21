"""All tunable numbers live here. Override any of them in config.toml."""
from __future__ import annotations

import os
import tomllib
import urllib.parse
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    # --- feed ---
    feed_url: str = "wss://pumpportal.fun/api/data"
    # PumpPortal only streams per-token trades (subscribeTokenTrade) with an API key
    # whose linked wallet holds >= 0.02 SOL; it is metered at 0.01 SOL per 10,000
    # events. Set it here (config.toml is git-ignored) or in PUMPPORTAL_API_KEY.
    pumpportal_api_key: str = ""
    db_path: str = "data/sniper.db"

    # --- trade sizing and costs ---
    position_usd: float = 50.0          # hard cap per trade (Rav's rule)
    fomo_fee_bps: float = 100.0         # ~1% per side, unconfirmed; venue spread/fee on top
    curve_fee_bps: float = 125.0        # pump.fun bonding-curve fee per side
    amm_fee_bps: float = 30.0           # PumpSwap/Raydium fee per side after migration
    extra_fee_usd: float = 0.0          # network/priority fee per side, if any
    entry_latency_s: float = 5.0        # signal -> fill delay (Jupiter executor in phase 2)
    exit_latency_s: float = 5.0         # trigger -> fill delay
    sol_usd_fallback: float = 140.0     # only used until a live SOL price arrives

    # --- exit rules (agreed 2026-11-16) ---
    take_profit_pct: float = 35.0
    stop_loss_pct: float = -25.0
    max_hold_s: float = 30 * 60

    # --- token observation window ---
    min_age_s: float = 30.0             # never pick before this age
    max_age_s: float = 600.0            # stop watching rejected tokens after this
    sniper_window_s: float = 3.0        # buys this soon after launch count as snipers

    # --- rug filters (all must pass) ---
    max_dev_share_pct: float = 10.0     # creator's share of supply
    dev_may_sell: bool = False          # any dev sell = reject
    max_top10_share_pct: float = 30.0   # top-10 holders (excluding curve/pool)
    max_sniper_count: int = 5           # distinct non-dev buyers inside sniper window

    # --- entry signals (all must pass) ---
    min_unique_buyers: int = 25
    min_buys_last_60s: int = 10
    min_buy_sell_ratio: float = 1.5     # SOL bought / SOL sold, last 60s
    min_mcap_sol: float = 40.0
    max_mcap_sol: float = 350.0         # pump.fun graduates near ~400 SOL mcap

    # --- baseline for comparison ---
    baseline_rate: float = 0.02         # share of all new tokens given a random paper trade
    random_seed: int | None = None

    total_supply: float = 1_000_000_000.0

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        cfg = cls()
        if path and Path(path).exists():
            data = tomllib.loads(Path(path).read_text())
            names = {f.name for f in fields(cls)}
            unknown = set(data) - names
            if unknown:
                raise ValueError(f"unknown config keys: {sorted(unknown)}")
            for k, v in data.items():
                setattr(cfg, k, v)
        env_key = os.environ.get("PUMPPORTAL_API_KEY", "").strip()
        if env_key:
            cfg.pumpportal_api_key = env_key
        return cfg

    def feed_url_with_key(self) -> str:
        if not self.pumpportal_api_key:
            return self.feed_url
        sep = "&" if "?" in self.feed_url else "?"
        return f"{self.feed_url}{sep}api-key={urllib.parse.quote(self.pumpportal_api_key)}"
