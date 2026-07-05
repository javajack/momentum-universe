# Momentum Universe

A self-contained, **point-in-time momentum research system for Indian equities**
— an adaptive momentum strategy engine plus its own NSE universe oracle and
~13 years of market data, in one repository. Clone it and run backtests,
market-regime checks, and rebalance planning with **zero setup and no
credentials**.

> **Educational / research use only — not financial advice.** See
> [DISCLAIMER.md](DISCLAIMER.md).

## Quickstart

```bash
git clone <this-repo> momentum-universe
cd momentum-universe
./start.sh            # bootstraps a venv, installs, launches the CLI
```

First launch builds a local DuckDB from the committed parquet (a few seconds,
one-time). Then you get an interactive menu:

```
1  Configure Zerodha credentials   optional — only for live features
2  Universe update                 rebuild / fetch latest NSE data
3  Select strategy                 dual_momentum / emerging_momentum
4  Select universe + rank range    v1/v2, e.g. ranks 201-600
5  Backtest                        historical simulation
6  Market / trigger check          current regime from latest data
7  Rebalance (from inputs)         capital + holdings -> target + orders
8  Swing research                  ryner / high_base / bake-off
0  Exit
```

## Credentials & safety

The repo ships **credential-free**. Everything under "analysis" — universe
update, backtest, market/trigger check, rebalance-from-inputs, swing research —
needs **no credentials at all**; it runs entirely on the vendored data.

The optional **live** features (live cache update, live-holdings rebalance) use
the [Zerodha Kite Connect](https://kite.trade) API with **your own** keys.
Configure them from menu option **1** (or copy `.env.example` → `.env`) — your
keys are written to a gitignored `.env` and the access token to a gitignored
cache, so nothing sensitive is ever committed.

## What's inside

- **`fortress/`** — the strategy engine: adaptive dual / emerging momentum,
  regime detection with graduated equity/gold allocation, tiered stops,
  recovery modes, a point-in-time backtester, and a rebalance planner.
  - `fortress/actions/` — a small **pure-function layer** (selection, backtest,
    market state, rebalance, credentials, universe update) that the CLI is a
    thin shell over. Import and reuse it from scripts or notebooks.
- **`nse_universe/`** — a vendored, self-contained package that answers
  point-in-time NSE index membership (`Universe(version=...).members_df(...)`)
  over a DuckDB view of the committed parquet. No network needed for reads.
- **`data/`** — ~13 years of daily OHLCV parquet + derived rank tables +
  corporate actions + NIFTY 50 / India VIX benchmarks (the runtime DuckDB is
  rebuilt from these and gitignored).
- **`tools/`** — research scanners and the swing bake-off (`swing_bakeoff.py`,
  `ryner_pullback_scan.py`, `high_base_scan.py`, sector/rename builders).
- **`nightlog.md`** — the research log behind the strategy choices.

## Programmatic use

```python
from fortress.config import load_config
from fortress import actions as A

cfg = load_config("config.yaml")
cfg = A.apply_selection(cfg, strategy="dual_momentum", rank_range=[201, 600])

state = A.current_market_state(cfg)          # current regime from latest data
result = A.run_backtest(cfg, "2013-01-01", "2026-01-01")
plan = A.plan_rebalance(cfg, capital=1_000_000, holdings={"BLISSGVS": 100})
```

## Strategies

- **`dual_momentum`** (default) — adaptive dual momentum: 12-1 NMS ranking with
  regime-aware allocation, recovery/crash-avoidance state machines, tiered
  stops.
- **`emerging_momentum`** — velocity-weighted (1m/3m/6m/12m) scoring with
  breakout + volume-confirmed boosts; catches earlier-stage momentum.

## Data provenance

The universe data is a mirror of the public
[custom-nse-500-historical-data](https://github.com/javajack/custom-nse-500-historical-data)
project (NSE public bhavcopy + yfinance corporate actions). "Universe update"
(menu 2) refreshes it from NSE's public endpoints — no broker or login.

## Requirements

Python ≥ 3.11. Dependencies install via `./start.sh` (or `pip install -e .`).

## License

MIT — see [LICENSE](LICENSE).
