"""Everything we know about one token, rebuilt from the trade stream."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class Trade:
    ts: float
    trader: str
    side: str            # "buy" | "sell"
    sol: float
    tokens: float
    new_balance: float | None
    v_sol: float | None
    v_tokens: float | None
    mcap_sol: float | None
    pool: str


@dataclass
class TokenState:
    mint: str
    created_ts: float
    creator: str
    name: str = ""
    symbol: str = ""
    pool: str = "pump"
    v_sol: float = 0.0
    v_tokens: float = 0.0
    last_price_sol: float = 0.0
    mcap_sol: float = 0.0
    last_trade_ts: float = 0.0
    migrated: bool = False
    balances: dict[str, float] = field(default_factory=dict)
    buyers: set[str] = field(default_factory=set)
    snipers: set[str] = field(default_factory=set)
    dev_sold: bool = False
    recent: deque = field(default_factory=lambda: deque(maxlen=2000))
    n_trades: int = 0

    def age(self, now: float) -> float:
        return now - self.created_ts

    def has_reserves(self) -> bool:
        return self.v_sol > 0 and self.v_tokens > 0

    def apply(self, t: Trade, sniper_window_s: float) -> None:
        self.n_trades += 1
        self.last_trade_ts = t.ts
        self.pool = t.pool or self.pool
        if t.v_sol and t.v_tokens:
            self.v_sol, self.v_tokens = t.v_sol, t.v_tokens
            self.last_price_sol = t.v_sol / t.v_tokens
        elif t.tokens > 0 and t.sol > 0:
            # no reserves on this message (e.g. after migration): use trade price
            self.last_price_sol = t.sol / t.tokens
        if t.mcap_sol:
            self.mcap_sol = t.mcap_sol
        if t.new_balance is not None:
            self.balances[t.trader] = t.new_balance
        elif t.side == "buy":
            self.balances[t.trader] = self.balances.get(t.trader, 0.0) + t.tokens
        else:
            self.balances[t.trader] = max(0.0, self.balances.get(t.trader, 0.0) - t.tokens)
        if t.side == "buy" and t.trader != self.creator:
            self.buyers.add(t.trader)
            if t.ts - self.created_ts <= sniper_window_s:
                self.snipers.add(t.trader)
        if t.side == "sell" and t.trader == self.creator:
            self.dev_sold = True
        self.recent.append(t)

    # ---- derived metrics ----
    def dev_share_pct(self, supply: float) -> float:
        return 100.0 * self.balances.get(self.creator, 0.0) / supply

    def top10_share_pct(self, supply: float) -> float:
        top = sorted(self.balances.values(), reverse=True)[:10]
        return 100.0 * sum(top) / supply

    def window(self, now: float, seconds: float) -> list[Trade]:
        return [t for t in self.recent if now - t.ts <= seconds]

    def flow(self, now: float, seconds: float) -> tuple[int, float, float]:
        """(number of buys, SOL bought, SOL sold) over the last `seconds`."""
        w = self.window(now, seconds)
        buys = [t for t in w if t.side == "buy"]
        return (len(buys), sum(t.sol for t in buys),
                sum(t.sol for t in w if t.side == "sell"))
