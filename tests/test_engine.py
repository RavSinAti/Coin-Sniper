import json
import sqlite3

import pytest

from sniper import market
from sniper.config import Config
from sniper.db import DB
from sniper.engine import Sniper
from sniper.report import report

V_SOL0, V_TOK0 = 30.0, 1_073_000_000.0   # pump.fun starting virtual reserves
DEV = "dev"


def cfg(**kw):
    c = Config(baseline_rate=0.0, random_seed=1, sol_usd_fallback=100.0,
               min_unique_buyers=5, min_buys_last_60s=3, min_mcap_sol=0, max_mcap_sol=1e9,
               max_sniper_count=100, entry_latency_s=5, exit_latency_s=5)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


class Curve:
    """Simulates a pump.fun curve and emits PumpPortal-shaped messages."""
    def __init__(self, mint="M"):
        self.mint, self.vs, self.vt = mint, V_SOL0, V_TOK0
        self.bal = {}

    def _msg(self, tx, trader, sol, tok):
        return {"mint": self.mint, "txType": tx, "traderPublicKey": trader,
                "solAmount": sol, "tokenAmount": tok, "newTokenBalance": self.bal.get(trader, 0),
                "vSolInBondingCurve": self.vs, "vTokensInBondingCurve": self.vt,
                "marketCapSol": self.vs / self.vt * 1e9, "pool": "pump"}

    def create(self, init_sol=0.5):
        tok = market.buy_tokens_out(self.vs, self.vt, init_sol)
        self.vs += init_sol; self.vt -= tok; self.bal[DEV] = tok
        m = self._msg("create", DEV, init_sol, tok)
        m["initialBuy"] = tok; m["name"] = "Test"; m["symbol"] = "TST"
        return m

    def buy(self, trader, sol):
        tok = market.buy_tokens_out(self.vs, self.vt, sol)
        self.vs += sol; self.vt -= tok
        self.bal[trader] = self.bal.get(trader, 0) + tok
        return self._msg("buy", trader, sol, tok)

    def sell(self, trader, tok):
        sol = market.sell_sol_out(self.vs, self.vt, tok)
        self.vs -= sol; self.vt += tok
        self.bal[trader] = self.bal.get(trader, 0) - tok
        return self._msg("sell", trader, sol, tok)


@pytest.fixture
def db(tmp_path):
    return DB(str(tmp_path / "t.db"))


def pump_up(s, c, t0, n=8, sol=0.6):
    t = t0
    for i in range(n):
        t += 4
        s.handle(c.buy(f"b{i}", sol), t)
    return t


def test_round_trip_costs_money():
    """Buy and immediately sell at the same reserves must lose fees + slippage."""
    c = cfg()
    from sniper.paper import PaperEngine
    from sniper.token_state import TokenState
    tok = TokenState("M", 0, DEV, v_sol=V_SOL0, v_tokens=V_TOK0, last_price_sol=V_SOL0 / V_TOK0)
    eng = PaperEngine(c)
    p = eng.signal(tok, 0, "signal", {})
    eng.update(tok, 10, 100.0)
    assert p.state == "open"
    usd = eng.liquidation_usd(p, tok, 100.0)
    # two Fomo fees + two curve fees + slippage both ways: ~4.5% loss at minimum
    assert 44.0 < usd < 48.0


def test_pick_take_profit_with_latency(db):
    s = Sniper(cfg(), db)
    c = Curve()
    s.handle(c.create(), 0)
    t = pump_up(s, c, 0)               # 8 buyers by t=32 -> pick
    assert s.stats["picks"] == 1
    p = next(iter(s.paper.open.values()))
    assert p.state == "pending_entry"
    s.handle(c.buy("x1", 0.5), t + 6)   # first trade after latency fills the entry
    assert p.state == "open"
    t += 6
    for i in range(40):                 # strong pump
        t += 2
        s.handle(c.buy(f"y{i}", 1.0), t)
        if p.state == "pending_exit":
            break
    assert p.exit_reason == "take_profit"
    s.tick(t + 6)
    assert p.state == "closed"
    assert p.return_pct > 35
    row = sqlite3.connect(db.conn.execute("PRAGMA database_list").fetchone()[2]).execute(
        "SELECT state, exit_reason FROM positions").fetchone()
    assert row == ("closed", "take_profit")


def test_stop_loss_realized_worse_than_trigger(db):
    s = Sniper(cfg(), db)
    c = Curve()
    s.handle(c.create(), 0)
    t = pump_up(s, c, 0)
    s.handle(c.buy("x1", 0.5), t + 6); t += 6
    p = next(iter(s.paper.open.values()))
    for i in range(8):                  # holders dump
        t += 1
        s.handle(c.sell(f"b{i}", c.bal[f"b{i}"]), t)
    assert p.exit_reason == "stop_loss"
    s.handle(c.sell(DEV, c.bal[DEV]), t + 5)   # dev dumps during our exit latency
    assert p.state == "closed"
    assert p.return_pct < p.exit_trigger_return_pct <= -25


def test_time_limit_on_dead_token(db):
    s = Sniper(cfg(), db)
    c = Curve()
    s.handle(c.create(), 0)
    t = pump_up(s, c, 0)
    s.tick(t + 6)                       # no trades: fill from last known state
    p = next(iter(s.paper.open.values()))
    assert p.state == "open"
    s.tick(t + 6 + 1800)
    assert p.exit_reason == "time_limit"
    s.tick(t + 6 + 1806)
    assert p.state == "closed"


def test_rug_filters_reject(db):
    s = Sniper(cfg(max_dev_share_pct=1.0), db)
    c = Curve()
    s.handle(c.create(init_sol=3.0), 0)          # dev holds ~9%
    pump_up(s, c, 0)
    assert s.stats["picks"] == 0
    s.tick(700)
    reasons = json.loads(db.conn.execute("SELECT reasons FROM evaluations").fetchone()[0])
    assert "dev_share" in reasons
    assert "M" not in s.tokens                   # unsubscribed and dropped


def test_dev_sell_rejects(db):
    s = Sniper(cfg(), db)
    c = Curve()
    s.handle(c.create(), 0)
    s.handle(c.sell(DEV, c.bal[DEV] / 2), 1)
    pump_up(s, c, 1)
    assert s.stats["picks"] == 0


def test_baseline_trades_ignore_filters(db):
    s = Sniper(cfg(baseline_rate=1.0, max_dev_share_pct=0.0), db)
    c = Curve()
    s.handle(c.create(), 0)
    at = s.baseline_at["M"]
    t = 0
    while t < at + 10:
        t += 5
        s.handle(c.buy(f"r{t}", 0.1), t)
    assert s.stats["picks"] == 0 and s.stats["baseline"] == 1


def test_report_flags_small_sample(db, tmp_path):
    s = Sniper(cfg(), db)
    c = Curve()
    s.handle(c.create(), 0)
    t = pump_up(s, c, 0)
    s.tick(t + 6); s.tick(t + 1806); s.tick(t + 1812)
    r = report(str(tmp_path / "t.db"), None, 35.0)
    assert r["signal"]["trades"] == 1
    assert "NOT ENOUGH DATA" in r["verdict"]
