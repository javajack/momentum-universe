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
 DATA
1   Universe update          fetch + full sync + data-integrity check
2   Universe query           search · screen by tier · lists · rank climbers
3   Settings                 strategy · universe version · rank band
 RESEARCH
4   Backtest                 historical simulation over a custom window
5   Market phases            per-phase returns vs NIFTY, 2013 -> date
6   Market / regime check    current regime, VIX, defensive allocation
 DISCOVER
7   Momentum scan            established-momentum leaders + metrics
8   Emerging momentum scan   rank-climbing, pre-run names (early stage)
 ALLOCATE
9   Fresh allocation plan    enter ₹ -> momentum + swing buy plan (no holdings)
0   Exit
```

The menu groups into four jobs — **manage data → research → discover names →
size a buy plan**. Allocation is stateless: you never enter existing holdings,
just the ₹ to deploy per sleeve (and how many stocks), and get a combined
momentum+swing breakup with quantities, stops, overlap flags and rotation days.

## Credential-free by design

The repo ships **fully credential-free** — every feature (universe update,
backtest, market/trigger check, allocation planning, swing and emerging scans)
runs entirely on the vendored data with **no broker, no login, no API keys**.
"Universe update" (menu 1) fetches fresh daily bars from NSE's *public* bhavcopy
archive — no account required. There is no live-broker/order-placement path.

## What's inside

- **`fortress/`** — the strategy engine: adaptive dual / emerging momentum,
  regime detection with graduated equity/gold allocation, tiered stops,
  recovery modes, a point-in-time backtester, and a rebalance planner.
  - `fortress/actions/` — a small **pure-function layer** (selection, backtest,
    market phases, market state, rebalance, allocation, universe update) that
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

For quick interactive exploration, use **CLI menu option 2 ("Universe query")**
(members / rank / snapshot / coverage as of any date), run
`examples/explore_universe.py`, or import `Universe` in a REPL / notebook.

### Universe versions: v1 vs v2

The `version=` argument (also `config.yaml → universe.version`) selects *which
ranking table* you query. Both rank every eligible stock by **median daily
turnover over the prior 126 trading days** and keep the top 1,000 — they differ
in *what is allowed into the ranking*:

- **`v1`** (`universe_rank`) — **raw turnover ranking**, index-style. The only
  gate is a full 126-day history (so fresh IPOs can't spike in). Includes
  everything liquid, warts and all: surveillance names, erratic-liquidity and
  circuit-locked stocks, sub-₹50 names.

- **`v2`** (`universe_v2`) — **momentum-grade**: the same turnover ranking, but
  only *after* a quality filter stack that removes names which pollute momentum
  signals. A stock must pass **all** of:

  | Filter | Threshold |
  |---|---|
  | Listing history | ≥ 252 trading days (~1 year) |
  | Traded days (last 60d) | ≥ 95% |
  | Median turnover (60d / 126d) | ≥ ₹50 L / ₹25 L |
  | Close price | ≥ ₹50 (no penny stocks) |
  | Turnover consistency (CV, 126d) | ≤ 3.0 (no erratic liquidity) |
  | Circuit-hit days (last 60d) | ≤ 5% |
  | NSE GSM / ASM surveillance | must be clear |

**Use `v2` for momentum / positional strategies** — it's a pre-cleaned candidate
pool (the built-in strategies default to it). Use `v1` for the unfiltered,
index-style liquidity ranking.

## Strategies

- **`dual_momentum`** (default) — adaptive dual momentum: 12-1 NMS ranking with
  regime-aware allocation, recovery/crash-avoidance state machines, tiered
  stops. The most **out-of-sample-robust** choice (see walk-forward below) and
  the best all-weather / drawdown profile.
- **`emerging_momentum`** — velocity-weighted (1m/3m/6m/12m) scoring with
  breakout + volume-confirmed boosts; catches earlier-stage momentum. Higher
  median return but high-beta (wins bull windows, loses stress windows hardest).
- **`regime_switched_momentum`** — best-of-both switcher (emerging scoring in
  risk-on regimes, dual in stress). Available via menu 3 but **not the default**:
  walk-forward validation showed it overfits — it wins in-sample but mis-times
  the parent handoff out-of-sample. Kept for research; see the 2026-08-05 spec.

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
market-phase timeline (reproduce via menu option 5):

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

**`dual_momentum` is the default** — chosen on **walk-forward validation**, not
a single backtest. Across five non-overlapping 2-year out-of-sample windows
(2016→2026, each a different regime), `dual_momentum` won both stress windows
(least-bad −3.7% in the 2020-22 drawdown; +24.3% CAGR / 3.26 Calmar in the
2022-24 recovery) and generalized cleanly (train→test Calmar gap ≈ 0). It best
matches the project's goal: all-weather behaviour with drawdown control.

The **`regime_switched_momentum`** switcher — which *led the full-13-year
backtest* (22.3% CAGR) — was tried as default and **failed walk-forward**: it
wins in-sample but out-of-sample tracks whichever parent is doing *worse*
(mis-timed handoff), landing last by window win-rate. A cautionary example of
why in-sample CAGR is not a tuning target. `emerging_momentum` has the best
*median* out-of-sample metrics but is high-beta. Both remain selectable from
menu 3. Full analysis:
`docs/superpowers/specs/2026-08-05-fresh-allocation-completeness-validation.md`
and `docs/superpowers/specs/2026-07-06-regime-switched-momentum-design.md`.

> Educational/research figures only — survivorship-free backtests with modelled
> costs, not live results. Past performance does not guarantee future results.

## MCP server — query the system from Claude Code / Codex / any LLM

The repo ships a read-only [MCP](https://modelcontextprotocol.io) server so
LLM agents can pull the system's technical picks and layer their own
diligence (fundamentals, shareholding, news — things this codebase
deliberately does not model) on top:

```bash
.venv/bin/python -m fortress.mcp_server     # stdio transport
```

| Tool | What it returns |
|---|---|
| `swing_allocation_plan` | capital → 3+2 slot split: ticker, qty, ₹ allocation, stop, rotation days |
| `momentum_scan` | top-N ranked stocks under the active strategy |
| `emerging_scan` | stocks EARLY in a move (rank climbing + early momentum) — the pre-run complement |
| `rank_velocity` | deep-tail rank-climb radar (to depth 2000) + price momentum (6m/12m, 200SMA) — early-multibagger research watchlist; `exclude_ipos` + `max_12m_return` runway filters |
| `fresh_allocation` | enter ₹ (momentum + swing) → combined breakup with qty, overlap + ADV flags, no holdings |
| `momentum_allocation` | capital (+ holdings) → target weights, quantities, orders |
| `market_state` | current regime, VIX, stress, equity/gold/cash split |
| `universe_lookup` | PIT turnover rank + scan-band check for a symbol |
| `stock_snapshot` | per-ticker technical context (returns, 200SMA, 52w-high, ATR, turnover) |

**Claude Code**: the committed `.mcp.json` registers the server
automatically when you open Claude Code inside the repo (approve when
prompted). **Codex**: add to `~/.codex/config.toml`:

```toml
[mcp_servers.momentum_universe]
command = "/ABS/PATH/TO/momentum-universe/.venv/bin/python"
args = ["-m", "fortress.mcp_server"]
```

Full setup for a fresh machine, the per-tool reference, and a suggested
diligence workflow for agents live in [`llms.txt`](llms.txt).

## Data provenance

The universe data is a mirror of the public
[custom-nse-500-historical-data](https://github.com/javajack/custom-nse-500-historical-data)
project (NSE public bhavcopy + yfinance corporate actions). "Universe update"
(menu 1) refreshes it from NSE's public endpoints — no broker or login.

## Requirements

Python ≥ 3.11. Dependencies install via `./start.sh` (or `pip install -e .`).

## License

MIT — see [LICENSE](LICENSE).
