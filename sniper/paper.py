"""Paper-trade engine: realistic fills, mark-to-market, and exit rules.

Lifecycle of a position:
  pending_entry --(entry_latency)--> open --(TP / SL / time trigger)-->
  pending_exit --(exit_latency)--> closed

Fills use the token's state at the fill moment, not at the signal moment, so
the latency cost is in the numbers. Every fill pays the Fomo fee, the venue fee
and the slippage our own order causes on the reserves.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from . import market
from .config import Config
from .token_state import TokenState

_ids = itertools.count(1)


@dataclass
class Position:
    mint: str
    strategy: str                 # "signal" | "baseline"
    signal_ts: float
    metrics: dict
    id: int = field(default_factory=lambda: next(_ids))
    state: str = "pending_entry"
    entry_ts: float | None = None
    entry_price_sol: float | None = None
    tokens: float = 0.0
    entry_usd: float = 0.0
    sol_usd_entry: float = 0.0
    pool_entry: str = ""
    exit_trigger_ts: float | None = None
    exit_trigger_return_pct: float | None = None
    exit_reason: str | None = None
    exit_ts: float | None = None
    exit_price_sol: float | None = None
    exit_usd: float | None = None
    return_pct: float | None = None
    peak_return_pct: float = 0.0
    trough_return_pct: float = 0.0
    slippage_modeled: bool = True
    pool_exit: str = ""


class PaperEngine:
    def __init__(self, cfg: Config, on_change=None):
        self.cfg = cfg
        self.open: dict[int, Position] = {}
        self.on_change = on_change or (lambda p: None)

    def has_position(self, mint: str) -> bool:
        return any(p.mint == mint for p in self.open.values())

    def signal(self, tok: TokenState, now: float, strategy: str, metrics: dict) -> Position:
        p = Position(mint=tok.mint, strategy=strategy, signal_ts=now, metrics=metrics)
        self.open[p.id] = p
        self.on_change(p)
        return p

    # ---- fee helpers ----
    def _venue_bps(self, tok: TokenState) -> float:
        return self.cfg.curve_fee_bps if tok.pool == "pump" else self.cfg.amm_fee_bps

    def _fill_buy(self, p: Position, tok: TokenState, now: float, sol_usd: float) -> None:
        usd = self.cfg.position_usd - self.cfg.extra_fee_usd
        sol = market.apply_fee(usd / sol_usd, self.cfg.fomo_fee_bps)
        sol = market.apply_fee(sol, self._venue_bps(tok))
        if tok.has_reserves():
            p.tokens = market.buy_tokens_out(tok.v_sol, tok.v_tokens, sol)
        else:
            p.slippage_modeled = False
            p.tokens = sol / tok.last_price_sol if tok.last_price_sol else 0.0
        p.entry_ts, p.state = now, "open"
        p.entry_price_sol = tok.last_price_sol
        p.entry_usd, p.sol_usd_entry, p.pool_entry = self.cfg.position_usd, sol_usd, tok.pool

    def liquidation_usd(self, p: Position, tok: TokenState, sol_usd: float) -> float:
        """What selling the whole bag right now would bank, after all costs."""
        if tok.has_reserves():
            sol = market.sell_sol_out(tok.v_sol, tok.v_tokens, p.tokens)
        else:
            p.slippage_modeled = False
            sol = p.tokens * tok.last_price_sol
        sol = market.apply_fee(sol, self._venue_bps(tok))
        sol = market.apply_fee(sol, self.cfg.fomo_fee_bps)
        return max(0.0, sol * sol_usd - self.cfg.extra_fee_usd)

    def _ret(self, usd: float) -> float:
        return 100.0 * (usd - self.cfg.position_usd) / self.cfg.position_usd

    # ---- main driver: call after every trade on the token and on a clock tick ----
    def update(self, tok: TokenState, now: float, sol_usd: float) -> list[Position]:
        closed = []
        for p in [p for p in self.open.values() if p.mint == tok.mint]:
            if p.state == "pending_entry":
                if now - p.signal_ts >= self.cfg.entry_latency_s and tok.last_price_sol > 0:
                    self._fill_buy(p, tok, now, sol_usd)
                    self.on_change(p)
                continue
            usd = self.liquidation_usd(p, tok, sol_usd)
            r = self._ret(usd)
            p.peak_return_pct = max(p.peak_return_pct, r)
            p.trough_return_pct = min(p.trough_return_pct, r)
            if p.state == "open":
                reason = None
                if r >= self.cfg.take_profit_pct:
                    reason = "take_profit"
                elif r <= self.cfg.stop_loss_pct:
                    reason = "stop_loss"
                elif now - p.entry_ts >= self.cfg.max_hold_s:
                    reason = "time_limit"
                if reason:
                    p.state, p.exit_reason = "pending_exit", reason
                    p.exit_trigger_ts, p.exit_trigger_return_pct = now, r
            if p.state == "pending_exit" and now - p.exit_trigger_ts >= self.cfg.exit_latency_s:
                p.exit_ts, p.exit_usd, p.return_pct = now, usd, r
                p.exit_price_sol, p.pool_exit, p.state = tok.last_price_sol, tok.pool, "closed"
                del self.open[p.id]
                closed.append(p)
                self.on_change(p)
        return closed
