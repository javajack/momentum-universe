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
6  Market phases                   per-phase returns vs NIFTY, 2013 -> date
7  Market / trigger check          current regime from latest data
8  Rebalance (from inputs)         capital + holdings -> target + orders
9  Swing research                  ryner / high_base / bake-off
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
    market phases, market state, rebalance, credentials, universe update) that
    the CLI is a thin shell over. Import and reuse it from scripts or notebooks.
    The 2013→date market-phase timeline (incl. a data-driven re-segmentation of
    the 2024-09 → 2026-04 tail) lives in `fortress/actions/phases.py`.
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
report = A.run_market_phases(cfg)            # per-phase returns vs NIFTY
plan = A.plan_rebalance(cfg, capital=1_000_000, holdings={"BLISSGVS": 100})
```

## Use the universe data for your own strategies

The vendored `nse_universe` package is a standalone **point-in-time universe
oracle** — it answers "who was in this index, at what rank, on this date"
(survivorship-free), so you can build and test your own strategies on the same
data the built-in strategies use.

```python
from datetime import date
from nse_universe import Universe

u = Universe(version="v2")            # v2 = momentum-grade; v1 = raw turnover
u.indices()                           # nifty_50/100/200/500/1000, midcap_150,
                                      #   smallcap_250, largecap_100  (add your own)
u.members(date(2024, 1, 15), "midcap_150")      # point-in-time index members
u.rank("SANSERA", date(2024, 1, 15))            # rank on a date  (-> 511)
u.universe_at(date(2024, 1, 15))                # full ranked snapshot that day
u.members_df(date(2023, 1, 1), date(2023, 12, 31), "nifty_1000")  # per-day membership
u.walk(date(2024, 1, 1), date(2024, 12, 31), "midcap_150", freq="M")  # iterate in time
u.health()                            # coverage: 2005 -> 2026-07, ~4,200 symbols
```

A **custom rank window** (e.g. small/mid ranks 201-600) is just a filter on
`members_df` / `universe_at`. Named indices live in `config/indices.yml` — add
your own rank bands freely.

Two runnable examples:

- **`examples/explore_universe.py`** — a tour of every query above.
- **`examples/custom_strategy.py`** — a complete ~60-line template: a monthly
  top-N momentum backtest on the [201,600] band using point-in-time membership +
  prices, with no look-ahead. Swap in your own `score()` to test any idea (the
  bundled version does ~+21.8% CAGR, 2018→2026).

```bash
.venv/bin/python examples/explore_universe.py
.venv/bin/python examples/custom_strategy.py
```

For quick interactive exploration, run `examples/explore_universe.py`, or import
`Universe` in a Python REPL / notebook. (Menu option 2, "Universe update", keeps
the underlying data current from NSE's public feed.)

## Strategies

- **`dual_momentum`** (default) — adaptive dual momentum: 12-1 NMS ranking with
  regime-aware allocation, recovery/crash-avoidance state machines, tiered
  stops.
- **`emerging_momentum`** — velocity-weighted (1m/3m/6m/12m) scoring with
  breakout + volume-confirmed boosts; catches earlier-stage momentum.

## Strategy comparison (last 10 years)

Both strategies trade the same point-in-time `[201, 600]` small/mid-cap
universe and share all regime/exit/sizing machinery — they differ only in the
*scoring*. Head-to-head over **Jun 2016 → Jun 2026** (30-day rebalance,
survivorship-free, real costs), vs simply holding the index:

| | CAGR | Sharpe | Max DD | Total | ₹20L → |
|---|--:|--:|--:|--:|--:|
| **dual_momentum** | **+18.2%** | **0.78** | **−23.4%** | +432% | ₹1.06 Cr |
| **emerging_momentum** | +16.7% | 0.70 | −26.2% | +368% | ₹93.7 L |
| _NIFTY 50_ (passive) | +11.2% | — | — | — | ₹57.9 L |
| _NIFTY Midcap 50_ (passive) | +17.6% | — | — | — | — |
| _NIFTY Midcap 150_ (passive) | +18.9%\* | — | — | — | — |

\* NIFTY Midcap 150 index history begins 2019, so that figure covers ~2019→2026,
not the full decade; **NIFTY Midcap 50** is the like-for-like 10-year midcap
benchmark. Both strategies **comfortably beat large-cap NIFTY 50** and roughly
match the midcap indices — while adding a regime-based defensive overlay
(gold/cash in stress) that passive index holding lacks.

**Which wins where** — mean per-phase alpha vs NIFTY 50 across the 2013→2026
market-phase timeline (reproduce via menu option 6):

| Regime | dual_momentum | emerging_momentum |
|---|--:|--:|
| Bull markets (n=9) | +8.5% | **+10.3%** |
| Bear / corrections (n=8) | **+6.1%** | +3.4% |
| Sideways / recovery (n=2) | +5.0% | +5.4% |

The trade-off is clear and consistent:

- **emerging_momentum wins in bull markets** (+10.3% vs +8.5% alpha) — its
  velocity + breakout scoring catches trends earlier, so it rips harder once
  momentum establishes.
- **dual_momentum defends far better in bears/corrections** (+6.1% vs +3.4%
  alpha) — classic 12-1 momentum is steadier and less whippy in downturns.

Over a full cycle the **better bear defense outweighs the bull edge**:
`dual_momentum` ends with the higher CAGR (20.9% vs 19.0% over the full 13-year
timeline), higher Sharpe (0.92 vs 0.82) and shallower drawdown (−25.0% vs
−28.4%). `emerging_momentum` is the more aggressive, higher-beta choice — it
shone in the recent 2026 stabilization (+9.6% vs +8.1% alpha).

**`emerging_momentum` is the shipped default** (early-stage momentum is the
project's headline idea); switch to `dual_momentum` from menu option 3 for the
steadier, higher-Sharpe profile.

> Educational/research figures only — survivorship-free backtests with modelled
> costs, not live results. Past performance does not guarantee future results.

## Data provenance

The universe data is a mirror of the public
[custom-nse-500-historical-data](https://github.com/javajack/custom-nse-500-historical-data)
project (NSE public bhavcopy + yfinance corporate actions). "Universe update"
(menu 2) refreshes it from NSE's public endpoints — no broker or login.

## Requirements

Python ≥ 3.11. Dependencies install via `./start.sh` (or `pip install -e .`).

## License

MIT — see [LICENSE](LICENSE).
