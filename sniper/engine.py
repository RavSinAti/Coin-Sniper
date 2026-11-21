"""Glue: turns feed messages into token state, verdicts and paper trades.

Pure logic, no network, so it can be replayed from a recorded feed in tests.
"""
from __future__ import annotations

import random

from .config import Config
from .db import DB
from .paper import PaperEngine
from .strategy import evaluate
from .token_state import Trade, TokenState


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


class Sniper:
    def __init__(self, cfg: Config, db: DB, subscribe=None, unsubscribe=None):
        self.cfg, self.db = cfg, db
        self.rng = random.Random(cfg.random_seed)
        self.tokens: dict[str, TokenState] = {}
        self.decided: set[str] = set()
        self.last_fails: dict[str, tuple[list[str], dict]] = {}
        self.baseline_at: dict[str, float] = {}       # mint -> age to enter at
        self.paper = PaperEngine(cfg, on_change=db.position)
        self.subscribe = subscribe or (lambda mints: None)
        self.unsubscribe = unsubscribe or (lambda mints: None)
        self.sol_usd = cfg.sol_usd_fallback
        self.stats = {"created": 0, "trades": 0, "picks": 0, "baseline": 0, "closed": 0}

    # ---------- feed messages ----------
    def handle(self, msg: dict, now: float) -> None:
        tx = msg.get("txType")
        mint = msg.get("mint")
        if not mint:
            return
        if tx == "create":
            self._on_create(msg, now)
        elif tx in ("buy", "sell"):
            self._on_trade(msg, now)
        elif tx == "migrate" or msg.get("pool") in ("pump-amm", "raydium") and tx is None:
            tok = self.tokens.get(mint)
            if tok:
                tok.migrated = True
                tok.pool = msg.get("pool") or "pump-amm"
                tok.v_sol = tok.v_tokens = 0.0   # curve reserves no longer valid

    def _on_create(self, msg: dict, now: float) -> None:
        mint = msg["mint"]
        if mint in self.tokens:
            return
        creator = msg.get("traderPublicKey", "")
        tok = TokenState(mint=mint, created_ts=now, creator=creator,
                         name=str(msg.get("name", ""))[:64], symbol=str(msg.get("symbol", ""))[:16],
                         pool=msg.get("pool") or "pump")
        self.tokens[mint] = tok
        self.stats["created"] += 1
        init = _f(msg.get("initialBuy")) or 0.0
        tok.apply(Trade(ts=now, trader=creator, side="buy", sol=_f(msg.get("solAmount")) or 0.0,
                        tokens=init, new_balance=init, v_sol=_f(msg.get("vSolInBondingCurve")),
                        v_tokens=_f(msg.get("vTokensInBondingCurve")),
                        mcap_sol=_f(msg.get("marketCapSol")), pool=tok.pool),
                  self.cfg.sniper_window_s)
        is_base = self.rng.random() < self.cfg.baseline_rate
        if is_base:
            self.baseline_at[mint] = self.rng.uniform(self.cfg.min_age_s, self.cfg.max_age_s)
        self.db.token(mint, now, creator, tok.name, tok.symbol, is_base)
        self.subscribe([mint])

    def _on_trade(self, msg: dict, now: float) -> None:
        tok = self.tokens.get(msg["mint"])
        if not tok:
            return
        self.stats["trades"] += 1
        tok.apply(Trade(ts=now, trader=msg.get("traderPublicKey", ""), side=msg["txType"],
                        sol=_f(msg.get("solAmount")) or 0.0, tokens=_f(msg.get("tokenAmount")) or 0.0,
                        new_balance=_f(msg.get("newTokenBalance")),
                        v_sol=_f(msg.get("vSolInBondingCurve")),
                        v_tokens=_f(msg.get("vTokensInBondingCurve")),
                        mcap_sol=_f(msg.get("marketCapSol")), pool=msg.get("pool") or tok.pool),
                  self.cfg.sniper_window_s)
        if tok.mint not in self.decided and tok.age(now) <= self.cfg.max_age_s:
            ok, fails, metrics = evaluate(tok, now, self.cfg)
            if ok:
                self.decided.add(tok.mint)
                self.db.evaluation(tok.mint, now, "pick", [], metrics)
                self.paper.signal(tok, now, "signal", metrics)
                self.stats["picks"] += 1
            else:
                self.last_fails[tok.mint] = (fails, metrics)
        self._maybe_baseline(tok, now)
        self.stats["closed"] += len(self.paper.update(tok, now, self.sol_usd))

    def _maybe_baseline(self, tok: TokenState, now: float) -> None:
        at = self.baseline_at.get(tok.mint)
        if at is not None and tok.age(now) >= at and tok.last_price_sol > 0:
            del self.baseline_at[tok.mint]
            _, _, metrics = evaluate(tok, now, self.cfg)
            self.paper.signal(tok, now, "baseline", metrics)
            self.stats["baseline"] += 1

    # ---------- clock ----------
    def tick(self, now: float) -> None:
        for tok in [self.tokens[m] for m in {p.mint for p in self.paper.open.values()}]:
            self.stats["closed"] += len(self.paper.update(tok, now, self.sol_usd))
        drop = []
        for mint, tok in self.tokens.items():
            if mint in self.baseline_at:
                self._maybe_baseline(tok, now)
            if tok.age(now) <= self.cfg.max_age_s:
                continue
            if mint not in self.decided:
                self.decided.add(mint)
                fails, metrics = self.last_fails.pop(mint, (["no_trades"], {}))
                self.db.evaluation(mint, now, "reject", fails, metrics)
            if mint in self.baseline_at:      # token went quiet before baseline entry age
                del self.baseline_at[mint]
            if not self.paper.has_position(mint):
                drop.append(mint)
        if drop:
            for m in drop:
                del self.tokens[m]
                self.decided.discard(m)
                self.last_fails.pop(m, None)
            self.unsubscribe(drop)
