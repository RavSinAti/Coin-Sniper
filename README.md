# Coin Sniper: phase 1 (paper trading only)

Watches every new pump.fun token on Solana, filters out obvious rugs, picks the
ones that match the entry signals, and records a **paper trade** for each pick:
where it would have bought, where the exit rules would have sold, and what it
would have actually banked after costs. No wallet, no keys, no real money.

## Rules (agreed 2026-11-16)

| Rule | Value |
|---|---|
| Position size | $50 per trade (USDC) |
| Take profit | +35% **realized** |
| Stop loss | −25% |
| Time limit | 30 minutes |
| Win | a closed trade whose realized return after all costs is above +35% |

"Touched +35% at some point" is **not** a win. The report shows that number
separately (`touched_tp_pct`), so you can see how far apart the two are.

## How a paper trade is priced

1. **Signal**: all rug filters and entry signals pass (see `sniper/strategy.py`).
2. **Entry fill** happens `entry_latency_s` later (default 5 s, the speed we
   expect from a Jupiter executor in phase 2), at the price *then*, not at the
   signal price.
3. **Costs on both sides**: the Fomo fee (~1%, not officially confirmed), the
   venue fee (pump.fun curve 1.25%, AMM 0.3%), and the slippage our own $50
   causes on the pool reserves.
4. **Exit**: TP, SL or time limit triggers, and the sale fills
   `exit_latency_s` later at whatever the price is then. Stop-losses usually
   fill worse than −25%. That is real and intentional.
5. Every trade also stores its peak and lowest return, so other exit rules can
   be tested on the same data later.

A round trip with zero price movement already loses about 6% ($50 comes back as ~$47). Any edge has to
clear that first.

## Rug filters and entry signals

Filters: creator holds ≤10% of supply, creator has not sold, top-10 holders hold
≤30%, at most 5 distinct sniper wallets bought in the first 3 seconds.

Signals: age ≥30 s, ≥25 unique buyers, ≥10 buys in the last 60 s, SOL bought /
SOL sold ≥1.5 in the last 60 s, market cap 40–350 SOL (pump.fun graduates near
~400 SOL).

These thresholds are **guesses**. They are only there to start collecting data.
Tune them from the data, not from gut feel. Mint and freeze authority are not
checked because pump.fun revokes both on every launch.

## Baseline

2% of all new tokens get a random paper trade with the same costs and exits,
whether or not they pass the filters. If `signal` doesn't clearly beat
`baseline`, the algorithm has no edge, however good its win rate looks.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp config.example.toml config.toml
.venv/bin/python -m sniper                    # runs forever, logs to data/sniper.db
.venv/bin/python -m sniper.report --since-hours 24
.venv/bin/python -m pytest -q
```

It must run 24/7 to collect useful data. A laptop that sleeps loses data. A
$5/month Linux VPS is enough. `deploy/coin-sniper.service` is a systemd unit
for that.

## Dashboard

```bash
.venv/bin/python -m sniper.dashboard          # http://127.0.0.1:8050
```

It's a read-only web page over `data/sniper.db`. It shows algorithm picks vs
the random baseline (win rate, profit rate, average return, total P&L, max
drawdown), cumulative P&L after costs, open trades, the last 500 closed trades,
and why tokens were rejected. It refreshes every 30 s and has 24 h / 7 d / 30 d
/ all windows. It uses only the standard library, so there's nothing extra to
install, and it opens SQLite in read-only mode, so it can't touch the bot's log.

It binds to localhost on purpose. On a VPS, don't open the port. Tunnel to it
instead:

```bash
ssh -L 8050:localhost:8050 you@your-vps       # then open http://localhost:8050
```

`deploy/coin-sniper-dashboard.service` runs it under systemd next to the bot.

## Data

SQLite at `data/sniper.db`:
- `tokens`: every launch seen
- `evaluations`: the pick or reject verdict per token, with the reasons it
  failed and the metrics at that moment
- `positions`: every paper trade, with entry, exit, realized return, peak,
  trough and exit reason

## Known gaps

- Feed is PumpPortal's free websocket. The message fields for trades on
  pump.fun's own curve are well documented. Migration messages and AMM trades
  after graduation are handled defensively but have not been checked live yet.
  Positions without pool reserves are flagged `slippage_modeled = 0`.
- Sniper detection uses arrival time, not slot. Bundled launches that space out
  their buys will slip through.
- Fomo's fee and its real fill quality are unverified. Fomo has no public
  trading API, so phase 2 execution goes through Jupiter, and the fees should be
  updated to match it.
- Go-live bar: at least 200 closed signal trades with positive realized P&L
  after costs **and** a clear edge over baseline.
