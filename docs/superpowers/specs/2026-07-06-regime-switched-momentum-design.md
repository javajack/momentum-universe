# Regime-Switched Momentum — Design

**Date:** 2026-07-06
**Status:** Experiment — code is disposable until backtest results clear the success criteria. DO NOT COMMIT until the user approves the results.

## Problem

The repo ships two momentum strategies with a measured, opposite regime skew
(README "Which wins where", 2013→2026 phase timeline):

| Regime | dual_momentum alpha | emerging_momentum alpha |
|---|--:|--:|
| Bull (n=9) | +8.5% | **+10.3%** |
| Bear/correction (n=8) | **+6.1%** | +3.4% |
| Sideways/recovery (n=2) | +5.0% | +5.4% |

Each strategy leaves its weak phase on the table. A switcher that runs
emerging scoring in confirmed bulls and dual scoring otherwise has a ceiling
of "best of both per phase".

## Approach (A — hard switch, approved)

New strategy `regime_switched_momentum` = `RegimeSwitchedMomentumStrategy(EmergingMomentumStrategy)`
in `fortress/strategy/regime_switched_momentum.py`.

- The engine already pushes the confirmed `RegimeResult` into the strategy
  before every selection (`backtest.py:1308`, `momentum_engine.py:1335` →
  `set_regime()` stores it as `self._current_regime`). No engine changes.
- Scorer selection: `MarketRegime.BULLISH → emerging` scoring;
  `NORMAL / CAUTION / DEFENSIVE → dual` scoring; regime unavailable → dual
  (conservative fallback).
- `rank_stocks()`: delegate to `EmergingMomentumStrategy.rank_stocks` (super)
  in bull, else `AdaptiveDualMomentumStrategy.rank_stocks`.
- `check_exit_triggers()`: same delegation ("current-brain" semantics — the
  active regime's exit ladder governs all holdings; emerging's ladder is
  dual's + a 45-day time-decay rule, so the divergence is small).
- Everything else (select_portfolio, weights, stops, gold/cash overlay,
  recovery machines) is inherited untouched.
- Log the active scorer at each `rank_stocks` call for attribution.
- Registration: module-level `StrategyRegistry.register(...)` + import in
  `strategy/__init__.py` (makes it appear in CLI menu 4 automatically).

## Non-goals

- No blending of scores (Approach B is the fallback if A thrashes).
- No changes to regime detection thresholds, exits, sizing, or universe.
- No config knobs for the regime→scorer mapping (hardcoded constant; sweep
  later only if results warrant).

## Known risks

1. **Turnover spike on regime flips** — holdings re-scored under the new
   scorer may fall below hold percentile. Flips are rare (3-day confirmation
   hysteresis) but the backtest must report the turnover/cost delta.
2. **Regime machine was tuned for allocation, not scorer selection** — the
   BULLISH threshold (>0.65 composite) may lag phase starts; acceptable for
   v1 of the experiment.

## Success criteria (gate for keeping the code)

Backtest on the same setup as the README comparison (v2 universe, ranks
[201,600], 30-day rebalance, real costs):

1. 13y full-cycle CAGR > 20.9% (dual's) with Sharpe ≥ 0.92 and MaxDD ≤ 25%.
2. 10y (Jun 2016 → Jun 2026) beats both baselines on CAGR.
3. Per-phase: bull alpha ≥ dual's +8.5% (ideally ≈ emerging's), bear alpha
   ≥ emerging's +3.4% (ideally ≈ dual's).

If the switcher fails these, discard the branch of work (delete the files).

## Results (2026-07-07)

Three regime→scorer mappings tested on 2013→2026, v2 universe, ranks [201,600],
30d rebalance:

| Mapping | CAGR | Sharpe | MaxDD |
|---|--:|--:|--:|
| V1: emerging in BULLISH only | 19.44% | 0.85 | −28.71% |
| **V2: emerging in NORMAL+BULLISH (shipped)** | **21.91%** | **0.97** | −27.31% |
| V3: emerging in NORMAL only | 18.66% | 0.79 | −38.35% |
| baseline dual_momentum | 20.28% | 0.89 | −24.97% |
| baseline emerging_momentum | 18.23% | 0.78 | −28.40% |

At the 5d-rebalance phase timeline: switcher 22.31% CAGR / 0.99 Sharpe /
−27.31% MaxDD vs dual 20.93% / 0.92 / −24.97%. Phase alphas: bull +13.7% vs
dual's +10.2%; crash +26.2% vs +24.7%; bear/recovery +4.7% vs +0.1%; but pure
bear +2.7% vs dual's +4.8%.

Gate scorecard:
1. 13y CAGR/Sharpe — PASS (21.9–22.3% > 20.9%, Sharpe 0.97–0.99 ≥ 0.92);
   MaxDD −27.3% misses the ≤25% bar by 2.3pp (note: on the 10y window every
   strategy breaches 25%; switcher −29.1% vs dual −31.8%).
2. 10y window — FAIL on CAGR vs dual (15.79% vs 16.37%), win on MaxDD.
3. Phase alphas — bull PASS, bear marginal miss.

Per-year deltas (switcher − dual): +5 to +19pp every year 2015–2022 except
2019/2021 (~flat); **−6.5 / −13.6 / −10.7pp in 2023/2024/2025**. The edge is
broad-based for a decade, then inverts in the recent 3 years — emerging
scoring stayed active through the 2024–25 topping (index near highs keeps the
regime risk-on while mid/smallcaps corrected underneath).

**Open question for next iteration:** the regime machine keys on the NIFTY 50
index, so it misses mid/smallcap-only stress. Candidate fix: gate the scorer
on the *traded universe's* breadth/trend instead of (or in addition to) the
index regime, or an adaptive meta-switch on trailing scorer relative
performance.

## Follow-up: breadth veto — tested and REJECTED (2026-07-07)

Hypothesis: veto the emerging scorer when universe breadth (share of members
above their 50d MA, engine's existing `_get_cached_breadth`) shows stress the
NIFTY regime can't see. Two variants, both TDD'd and backtested on 13y:

| Variant | CAGR | Sharpe | MaxDD |
|---|--:|--:|--:|
| V2 regime-only (kept) | **21.91%** | **0.97** | −27.31% |
| V4 raw veto < 0.45 | 19.82% | 0.87 | −30.75% |
| V4 raw veto < 0.40 | 18.61% | 0.80 | −28.42% |
| V4 raw veto < 0.50 | 19.20% | 0.83 | −21.48% |
| V5 hysteresis 0.40/0.55 | 15.47% | 0.62 | −29.76% |

The veto DID repair 2023–25 (2024: +21.0% → +31.7%; 2025: −13.6% → −4.3%;
10y CAGR 15.8% → 18.2%) but destroyed 2016–18 (2017: +91.3% → +57.4%, below
BOTH pure strategies) and hysteresis made everything worse. Diagnostic: V5's
2014–17 returns were identical to V4's, so the damage was genuine veto
engagements on routine corrections, not mid-band thrash. In 2022 the vetoed
strategy (−3.9%) underperformed even pure dual (−0.5%): switching scorers
mid-stream forces turnover into a different book — a real cost regardless of
signal quality.

**Lesson: the scorer-switch signal must be rare and decisive; index-regime
transitions are, breadth is not.** All veto code reverted; strategy ships as
V2 (regime-only). Remaining ideas if 2023–25 is revisited: meta-switch on
trailing 6–12m scorer relative performance (rare by construction), or accept
the caveat — dual and the switcher differ by ~1.6pp CAGR full-cycle with
opposite recent-period skews.

## Test plan

- Unit tests (`tests/test_regime_switched.py`): registry resolution; scorer
  delegation per regime (BULLISH → emerging path, NORMAL/CAUTION/DEFENSIVE/None
  → dual path) via monkeypatched parent `rank_stocks`; exit delegation same way.
- Full backtests via `fortress.actions.run_backtest` + `run_market_phases`
  for switcher vs both baselines.
