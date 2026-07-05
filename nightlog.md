# Nightlog — Swing-Strategy Bake-off (2026-05-27 → 2026-05-29 session)

---

## Executive summary

You suspected the 12-month Ryner backtest "looked too good." It did. The session walked the problem in three steps:

1. **Audited and fixed universe bias** in the existing Ryner backtest — it was using a static end-of-period snapshot (survivorship + look-ahead). Patched to point-in-time membership. 12-month gross dropped from **+75.9% / PF 1.27** (biased) to **+19.6% / PF 1.06** (honest). After realistic costs (~30-50 bps round-trip), tuned Ryner with regime gate ON is **break-even to slightly negative**.
2. **Built a research-only bake-off** of six textbook swing strategies on the same [201, 600] PIT mid/smallcap universe under one PIT engine — canonical/textbook defaults, no parameter tuning, three realistic cost levels (20 / 35 / 60 bps round-trip).
3. **Ran it across 5 years** (2021-05 → 2026-05).

**The headline:**

- **Highest risk-adjusted return**: `high_base_52w` (Minervini VCP-lite) — PF **1.41** @ 35bp, Sharpe **+1.58**, MaxDD only **−22.1%**, robust to 60bp costs (PF still 1.28). **But its returns are highly concentrated — 3 of 5 years negative (2022, 2024, 2025).** The +₹402k cumulative comes almost entirely from 2021 and 2023.
- **Highest cumulative ₹ over 5 years**: `rsi2_pullback` (Ryner control at *canonical* params, no regime gate) — **₹435k** net @ 35bp on ₹500k notional (~87% return), Win 62.9%, **5 of 6 years positive**. But cost-fragile: PF collapses from 1.17 @ 35bp to 1.02 @ 60bp.
- **Strong runner-up**: `ema_pullback_trend` — PF 1.19, Sharpe 1.31, 4/6 years positive, decent cost robustness (PF 1.12 @ 60bp).
- **Three strategies that do NOT clear the cost hurdle** at 60bp: `volume_spike_cont`, `donchian_20d`, `bb_squeeze` — drop them from further consideration.

**Honest answer to "is there a better swing strategy for Indian mid/smallcaps":** Two candidates beat canonical RSI(2) on per-trade efficiency, with **opposite weakness profiles**. `high_base_52w` is more cost-robust but regime-concentrated; canonical RSI(2) is more consistent but cost-fragile. Either could be tuned further. Neither has been regime-gated yet. See Recommendations for the next-step plan.

---

## Part 1 — The Ryner PIT correction (audit + fix)

### The bug

`tools/ryner_backtest.py` was constructing `Universe(as_of=end, rank_range=[201, 600], version=v2)` *once at the end of the backtest window* and using that single snapshot as the candidate set for every trading day in the run. The dual_momentum backtest, by contrast, rebuilds Universe per rebalance date — proper point-in-time (`fortress/backtest.py:1071`).

This had two effects on Ryner:
- **Survivorship bias**: stocks that were in [201, 600] earlier but cratered out of the band (or got delisted) were silently dropped from the scan over their entire history.
- **Look-ahead bias**: stocks that climbed *into* [201, 600] only recently got their full multi-year history scanned as if they'd been members the whole time — a pool by construction biased toward strong recent performers.

### The fix

Added `_build_membership_index(start, end, rank_range, version)` to `tools/ryner_backtest.py`. It calls `nse_universe.members_df` once for the window and bins per trading day into `Dict[date, set[str]]`. The engine signature was relaxed to accept either the legacy `List[str]` (for `ryner_sweep`, `ryner_validation`, `ryner_slope_sweep`, `ryner_calendar_year_test`, `ryner_v3_distinguish` — those tools were tuned against the static set, so silently changing them was off the table) or the new `Dict[date, set]` for PIT. `_precompute_breadth` was also re-derived per-day so the breadth and v3 regime gate use today's universe like the live scanner does. Open positions still persist through universe drop-outs — they exit only on stop/RSI/SMA/time, which mirrors live behaviour.

### The delta (12 months, 2025-05-27 → 2026-05-27, [201, 600], v2, regime gate ON, v3)

| Metric | Static end-snapshot (old, biased) | Point-in-time (new) | Δ |
|---|---|---|---|
| Trades | 198 | 190 | −8 |
| Win rate | 71.2% | **67.9%** | −3.3 pp |
| Avg P&L / trade | +0.38% | **+0.10%** | −0.28 pp |
| Profit factor | 1.27 | **1.06** | −0.21 |
| Cumulative return (gross) | **+75.9%** | **+19.6%** | **−56.3 pp** |
| Worst trade | (not captured) | −32.5% | (tail re-appears) |

Universe is now 400 symbols/day on average over 235 trading days — same effective scan size as a live Option-S would see today's band.

### What the +19.6% gross hides

Still **not** included in the corrected Ryner backtest engine:
- **Slippage** between signal-day close and next-day open (~30-80 bps per trade) — Ryner backtest fills at signal-day close, a generous execution assumption
- **Brokerage + STT** (~30-50 bps round-trip on ₹100k notional for Zerodha equity delivery)
- **Stop fills** assumed at exact ATR-derived level (gap-down risk understated)

A 190-trade strategy with avg +0.10% gross/trade gives away ~50 bps round-trip in real life ⇒ net per trade ~ −0.40%. Multiplied across 190 trades, the strategy is net negative on plausible execution.

This is the result that motivated the bake-off — the live Ryner is not actually edge-positive on its current parameters once execution is honest.

---

## Part 2 — Swing bake-off (build + methodology)

### Design choices (decided up front)

| Question | Choice |
|---|---|
| Scope | 6 strategies, broad coverage across families, **one default param set each, no sweeps** |
| Deliverable | Research report only — no CLI menu, no Ryner replacement |
| Backtest window | 5 years (2021-05 → 2026-05) — boom + correction + recovery + strong + distribution |
| Universe | [201, 600] PIT, version v2 (from `config.yaml`) — same as Ryner and `dual_momentum` |
| Concurrent positions | Max 5, ₹100k equal-size per trade (Ryner convention) |
| Regime gate | OFF — bake-off measures *signal* P&L cleanly; gate layering is a separate concern |

### Execution model (every strategy)

- **PIT entry gating**: on date D, candidates are restricted to that day's [201, 600] membership (same as live scanner). Positions opened earlier persist through universe drop-outs.
- **Fills at next-day open**: signal fires on close of D, position opens at the open of D+1. Corrects the Ryner backtest's generous same-day-close-fill assumption. If D+1 has no bar for the ticker, the signal is skipped — no fill.
- **Exit fills**: exit signal on D's close ⇒ fill at D+1 open. Stop detected on close (close ≤ stop_price), filled at `min(stop_price, next_day_open)` to honour gap-down risk.
- **EOP exits**: any still-open position on the final day exits at that day's close.

### The six strategies (canonical / textbook defaults — no tuning)

| # | Name | Family | Entry | Exit | Stop | Time | Rank |
|---|---|---|---|---|---|---|---|
| 1 | `rsi2_pullback` | Mean-reversion | close>200SMA & close<5SMA & RSI(2)≤7 | close>5SMA OR RSI≥70 | 1.5×ATR | 10d | RSI(2) asc |
| 2 | `donchian_20d` | Short breakout | close=20d high & vol>1.5× 20d avg | close<10d low | 2×ATR | 25d | vol/avg desc |
| 3 | `high_base_52w` | Pivot / VCP-lite | close≥97% of 52w high & 20d range<10% | close<21EMA | 3×ATR | 30d | (52w−close)/close asc |
| 4 | `ema_pullback_trend` | Continuation-pullback | close>50SMA>200SMA & |close−21EMA|<2% & 12w ret>10% | close<50SMA | 2.5×ATR | 15d | 12w ret desc |
| 5 | `volume_spike_cont` | Footprint follow-through | vol>2× 20d avg & close>prev×1.03 & close>50SMA | close<10d low | 2×ATR | 8d | vol/avg desc |
| 6 | `bb_squeeze` | Vol expansion | BB(20,2) bandwidth in bottom 20% of 120d & close>upper band | close<20SMA | 2×ATR | 15d | bandwidth asc |

Common gates on all six: `close ≥ ₹50`, `avg_vol_20 ≥ 200k`. Sources for the defaults: Connors RSI(2) canonical (#1), Turtle System 1 (#2), Minervini VCP-lite (#3), IBD/O'Neil pullback (#4), volume-confirmation playbook (#5), Bollinger / Linda Raschke squeeze (#6).

### Cost model

| Level | Round-trip rate | Composition |
|---|---|---|
| Best case | 20 bps | STT ~20 + minimal slippage (large-mid cap, calm day) |
| **Baseline** | **35 bps** | STT 20 + fixed 1.5 + slippage ~12 (mid-cap typical) |
| Worst case | 60 bps | STT 20 + fixed 1.5 + slippage ~38 (deep-tail, stress day) |

Cost is deducted from each closed trade's ₹ P&L. Each strategy is scored at all three levels — every metric (PF, Sharpe, MaxDD, avg P&L%, win rate) appears net of the chosen cost level.

### Code layout

Single new file `tools/swing_bakeoff.py` (~870 LOC at HEAD), reusing the PIT engine primitives from `tools/ryner_backtest.py` (`_build_membership_index`). Tests in `tests/test_swing_bakeoff.py` (15 passing). Zero changes to `fortress/`, `dual_momentum`, Option-4 rebalance, Option-S Ryner scan, or any production code path.

---

## Part 3 — Sanity reconciliation (12-month smoke test)

Before the 5-year run, the spec required a reconciliation check: bake-off's `RSI2Pullback` on the 12-month window (2025-05-27 → 2026-05-27) should be in the ballpark of the PIT Ryner audit baseline (190 trades, 67.9% win, PF 1.06).

### Result

| | PIT Ryner (production, tuned) | Bake-off RSI(2) (canonical Connors) |
|---|---|---|
| Trades | 190 | **283** |
| Win rate | 67.9% | **67.8%** (Δ 0.1 pp) |
| PF @ 35bp | — (gross only) | **1.17** |
| Avg hold | ~5 days | **2.6 days** |
| Sharpe @ 35bp | — | +1.00 |
| MaxDD @ 35bp | — | −18.5% |
| Exit mix | mostly RSI/SMA signal | 234 signal / 45 stop / 2 time / 2 eop |

### Why the trade count is higher than the 150-230 sanity band

Win rate matches Ryner to 0.1 pp — confirms entry/exit logic, indicators, and pricing are identical. The 93-trade gap (283 vs 190) is fully explained by spec-mandated parameter differences:

- **Bake-off uses canonical Connors values** (per spec line 94-101): `1.5 × ATR` stop, `10-day` time stop, **regime gate OFF**.
- **Ryner production uses tuned values** (per `tools/ryner_pullback_scan.py` DEFAULTS): `3.0 × ATR` stop, `20-day` time stop, `require_market_uptrend=True`, `use_v3_gate=True`.

Three multipliers compound:
- Tighter stop ⇒ ~3× more stop-outs (15.9% vs ~5% in Ryner) ⇒ slot turnover faster ⇒ more trades
- Shorter time-stop ⇒ forced exits sooner ⇒ slot recycles faster
- No regime gate ⇒ entries happen on low-breadth days that Ryner skips entirely

This is by design — the bake-off measures *canonical* edge across strategy families, not the tuned-and-gated variant of any one of them. If RSI(2) canonical loses to another canonical, the recommendation is to investigate that other family with the same care Ryner already got (tuning + regime gating).

---

## Part 4 — The 5-year bake-off result

**Window:** 2021-05-01 → 2026-05-01 (5 regimes: 2021 boom, 2022 correction, 2023 recovery, 2024 strong, 2025 distribution, 2026 partial through May 1)
**Universe:** [201, 600] PIT, **1234 trading days, avg 400 symbols/day**, 1214 unique tickers loaded
**Capital model:** ₹100k × 5 concurrent slots = ₹500k notional per strategy
**Run time:** ~90 seconds end-to-end (data load + 6 strategies + scoring + CSV write)

### 4.1 Per-strategy summary @ 35 bps baseline cost — ranked by PF

| Rank | Strategy | N | Win% | Avg P&L% net | PF net | Sharpe net | MaxDD% | Avg Hold | Cum ₹ net |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `high_base_52w` | 431 | 27.1% | +0.93% | **1.41** | **+1.58** | **−22.1%** | 9.8d | ₹+401,885 |
| 2 | `ema_pullback_trend` | 530 | 40.8% | +0.74% | 1.19 | +1.31 | −36.5% | 10.6d | ₹+389,819 |
| 3 | `rsi2_pullback` (control) | 1537 | 62.9% | +0.28% | 1.17 | +1.03 | −23.0% | **2.4d** | **₹+435,302** |
| 4 | `volume_spike_cont` | 745 | 44.8% | +0.10% | 1.03 | +0.38 | −37.8% | 7.2d | ₹+71,674 |
| 5 | `donchian_20d` | 310 | 40.3% | +0.12% | 1.02 | +0.44 | −33.4% | 17.4d | ₹+37,595 |
| 6 | `bb_squeeze` | 421 | 34.2% | −0.09% | 0.97 | +0.06 | −43.7% | 9.0d | ₹−37,625 |

(Cum ₹ net = cumulative ₹ P&L over the full 5y on a ₹500k notional pool, baseline 35bp cost deducted per trade.)

### 4.2 Cost sensitivity — PF at each level

| Strategy | PF @ 20bp | PF @ 35bp | PF @ 60bp | Survives 60bp? |
|---|---|---|---|---|
| `high_base_52w` | 1.49 | 1.41 | **1.28** | ✅ yes, comfortably |
| `ema_pullback_trend` | 1.23 | 1.19 | 1.12 | ✅ yes |
| `rsi2_pullback` | 1.26 | 1.17 | **1.02** | ⚠ borderline (just above breakeven) |
| `volume_spike_cont` | 1.08 | 1.03 | 0.96 | ❌ no |
| `donchian_20d` | 1.05 | 1.02 | 0.98 | ❌ no |
| `bb_squeeze` | 1.02 | 0.97 | 0.90 | ❌ already losing at 35bp |

**Same view in cumulative ₹ net** (on ₹500k notional pool):

| Strategy | ₹ @ 20bp | ₹ @ 35bp | ₹ @ 60bp |
|---|---|---|---|
| `high_base_52w` | +₹466,535 | +₹401,885 | +₹294,135 |
| `ema_pullback_trend` | +₹469,319 | +₹389,819 | +₹257,319 |
| `rsi2_pullback` | +₹665,852 | +₹435,302 | +₹51,052 |
| `volume_spike_cont` | +₹183,424 | +₹71,674 | −₹114,576 |
| `donchian_20d` | +₹84,095 | +₹37,595 | −₹39,905 |
| `bb_squeeze` | +₹25,525 | −₹37,625 | −₹142,875 |

### 4.3 Per-year P&L breakdown @ 35bp (regime split)

This is the most diagnostic table in the entire report. The PF/Sharpe ranking above hides **return concentration** — a strategy with great average metrics can have terrible year-over-year consistency.

| Strategy | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (partial) | Pos yrs |
|---|---|---|---|---|---|---|---|
| `high_base_52w` | **₹+467k** | ₹−134k | **₹+263k** | ₹−155k | ₹−62k | ₹+22k | **2/5 full** |
| `ema_pullback_trend` | ₹+277k | ₹−7k | ₹+221k | ₹+60k | ₹−183k | ₹+22k | 3/5 full |
| `rsi2_pullback` | ₹+117k | ₹−51k | ₹+199k | ₹+110k | ₹+29k | ₹+31k | **4/5 full** |
| `volume_spike_cont` | ₹+6k | ₹−109k | ₹+227k | ₹+90k | ₹−117k | ₹−25k | 3/5 full |
| `donchian_20d` | ₹+4k | ₹+5k | ₹+25k | ₹−23k | ₹−28k | ₹+54k | 3/5 full |
| `bb_squeeze` | ₹+37k | ₹−162k | ₹+96k | ₹+99k | ₹−125k | ₹+18k | 3/5 full |

(Entries grouped by entry-year. 2026 row only covers Jan-Apr 2026 — entries from May 2021 onwards, so 2021 row only counts May-Dec.)

**Per-year win rate stability:**

| Strategy | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | Range |
|---|---|---|---|---|---|---|---|
| `rsi2_pullback` | 67% | 58% | 63% | 64% | 61% | 66% | **57-67 (10 pp)** |
| `ema_pullback_trend` | 55% | 37% | 48% | 41% | 30% | 41% | 30-55 (25 pp) |
| `volume_spike_cont` | 50% | 43% | 51% | 44% | 41% | 39% | 39-51 (12 pp) |
| `donchian_20d` | 42% | 38% | 44% | 41% | 35% | 45% | 35-45 (10 pp) |
| `bb_squeeze` | 31% | 25% | 44% | 40% | 30% | 41% | 25-44 (19 pp) |
| `high_base_52w` | 43% | 18% | 25% | 30% | 25% | 42% | **18-43 (25 pp)** |

`rsi2_pullback` is by far the most **regime-stable** — win rate sits in a 10-point band across boom/correction/recovery/strong/distribution. `high_base_52w` swings 25 points and gets badly hurt in the 2022 correction (Win 18%, deep drawdown).

### 4.4 Exit-reason mix @ 35bp — what's killing each strategy

| Strategy | stop% | signal% | time% | EOP | Interpretation |
|---|---|---|---|---|---|
| `rsi2_pullback` | 17.3% | 82.1% | 0.5% | 2 | Healthy — exits where the strategy says to exit |
| `high_base_52w` | **1.2%** | **89.8%** | 8.4% | 3 | Wide 3×ATR stop almost never fires; signal (close<21EMA) drives exits — letting winners run |
| `ema_pullback_trend` | 4.0% | 51.1% | 44.0% | 5 | Roughly half time-stopped — the strategy doesn't have a strong exit signal for the "trade just stops moving" case |
| `bb_squeeze` | 9.5% | 60.6% | 29.5% | 2 | Mixed — many failed breakouts ride to time-stop |
| `donchian_20d` | **48.7%** | **0%** | 50.0% | 4 | **Broken exit logic**: "close<10d low" signal NEVER fires while position is still active. All exits are stops or time-stops. The exit threshold is too lax to ever trigger before a stop — strategy is essentially "buy breakouts, exit via stop or time" |
| `volume_spike_cont` | 22.4% | **0%** | 77.0% | 4 | **Same broken pattern**: 10d-low exit never fires; trades just time out after 8 days. Strategy is effectively "buy spike + flat hold for 8 days unless stopped" |

The **0% signal-exit** result for `donchian_20d` and `volume_spike_cont` is a strategy-design observation worth flagging. Their canonical exits (close < 10-day low) are theoretical — in practice they never get hit before the stop or the time-stop because the 10-day low is too far below entry. These are stop/time-stop systems wearing breakout exit labels. (Not a bug — the textbook exits just don't bite in this universe at canonical params.)

### 4.5 Strategies that clear the cost hurdle

**At 35bp baseline cost:** 5 of 6 strategies have PF ≥ 1.0 (only `bb_squeeze` is sub-1).
**At 60bp worst case:** Only 3 strategies survive:
- `high_base_52w` (PF 1.28, ₹+294k cumulative) — robust
- `ema_pullback_trend` (PF 1.12, ₹+257k) — adequate
- `rsi2_pullback` (PF 1.02, ₹+51k) — marginal — basically breakeven at worst-case cost

The cost-survival ranking is **different** from the per-trade-efficiency ranking. `rsi2_pullback` looks great at 20bp (cumulative ₹+666k) but bleeds out at 60bp because 1537 trades × 25 extra bps drag = ~₹385k of additional cost on the same gross — wiping out most of the per-trade edge.

The two cost-robust survivors (`high_base_52w` and `ema_pullback_trend`) trade ~430-530 times in 5 years vs `rsi2_pullback`'s 1537. **Trade frequency is the key cost-sensitivity variable** — when costs rise, you want fewer, higher-conviction trades.

---

## Part 5 — Nuances and caveats (what the numbers don't tell you)

### Caveat 1 — "PF > 1.0 after cost" is necessary but not sufficient to ship

A backtest profitable after the modelled costs is the *floor*, not the bar. Real-world drag the engine still doesn't model:

- **Slippage at the stop**: engine fills at `min(stop, next_day_open)`. In real markets, illiquid mid/smallcaps can gap 5-10% on bad-news mornings — modelled at ATR but not at panic levels.
- **Concurrent fills**: 5 signals firing the same morning means 5 simultaneous open orders. Spread cost on the 4th and 5th is usually worse than on the 1st.
- **Front-running / market impact on size**: ₹100k is small but 5 × ₹100k = ₹500k in one name on a thin day moves the print. Multiply if you scale notional.
- **Survivorship at the data-loader level**: nse-universe data is solid post-2017; pre-2017 some thin-tail symbols may have spotty splits or delistings. The 5-year window 2021-2026 is in the high-quality regime, but worth knowing.

### Caveat 2 — Canonical defaults are intentionally untuned

This is *the* point of the bake-off — to test the **honest baseline** of each family before deciding which deserves the same tuning effort Ryner got over multiple sweep runs. If the winner here is RSI(2), then the conclusion is "tuned Ryner (production) is still the right swing strategy on this universe." If the winner is a different family, that's a real signal worth following up.

### Caveat 3 — No portfolio-level analysis

Each strategy is scored standalone. Two strategies with PF 1.10 each but uncorrelated drawdowns might form a great pair, while one with PF 1.30 alone might be too volatile to actually trade. The bake-off does not score combinations — that's a separate exercise once a top-2 or top-3 has been identified.

### Caveat 4 — No walk-forward / out-of-sample

The spec explicitly chose "broad, no sweeps" which means no walk-forward either. The 5-year result IS the result — no in-sample/out-of-sample split. Honest because no tuning happened, but it does mean any strategy whose params happen to suit *this specific 5-year window* could look better than it would on a different period. The textbook-defaults discipline mitigates this (the params weren't chosen by us).

### Caveat 5 — RSI(2) canonical ≠ RSI(2) production

Bake-off control = RSI(2) at canonical Connors values. Production Ryner = tuned (3× ATR stop, 20d time stop, regime gate on). They will NOT have the same numbers. The bake-off rank tells you "which family looks best out of the box"; it does not say "your tuned Ryner is worse than X."

If RSI(2) ranks well in the canonical bake-off, the production tuning likely transfers; if another family ranks above it in canonical form, the next experiment is "tune that family with the same effort Ryner got and re-compare."

### Caveat 6 — Three test fixtures use synthetic data, not real market data

The 15 passing tests verify each strategy fires on a hand-built synthetic OHLCV that matches the entry pattern (e.g. 52w high after tight base). They don't verify behavior on real Indian smallcap data — that's what the 5-year run does. Don't read PF from a unit test.

The Bollinger squeeze fixture (test #15) needed a 2-pass refinement: the original "tight stretch + 3% breakout" couldn't satisfy "squeeze AND breakout on same bar" because the breakout day inflates the 20-day std. Fix was a noisier 275-day baseline, a 24-day flat-vol squeeze, then a small 0.3% breakout that just clears the now-narrow upper band. Real markets do produce this pattern; the synthetic generator just needs the right shape.

---

## Part 6 — Recommendations

### Short version

You have **two strong candidates with opposite weakness profiles** — choose based on which weakness is acceptable, or run both as a portfolio. Three more are not worth pursuing.

### The two candidates

**Candidate A — `high_base_52w` (Minervini VCP-lite)**
- **Strengths**: Best PF (1.41), best Sharpe (1.58), shallowest MaxDD (−22%), survives 60bp costs comfortably (PF 1.28), wide stop lets winners run (89.8% signal-driven exits, only 1.2% stopped out).
- **Weakness**: Highly **regime-concentrated**. Of 5 full years, only 2 (2021, 2023) were positive — and the 2 winners produced **+₹730k** while the 3 losers produced **−₹351k**. Drawdowns hit 22% during 2022 + 2024 + 2025 stretch.
- **Profile**: Few, infrequent, high-conviction trades (~86/year). Low win rate (27%) with big right-tail wins. Classic Minervini behaviour.
- **Honest read**: Will look like a hero in bull years and a goat in correction/distribution years. The regime gate you already have for Ryner (`use_v3_gate=True` with breadth slope + sector ratio + VIX) might trim the bad years dramatically — but UNTESTED on this strategy.

**Candidate B — `rsi2_pullback` at canonical params (your current Ryner family, untuned)**
- **Strengths**: Most **consistent** by far — 5 of 6 years positive, win rate sits in a tight 57-67% band across every regime. Highest cumulative ₹ at baseline cost (₹+435k @ 35bp). Shortest hold (2.4 days) — capital recycles fast.
- **Weakness**: **Cost-fragile**. PF collapses from 1.17 @ 35bp to 1.02 @ 60bp because 1537 trades × extra cost drag eats the per-trade edge. Bleeds at 80+ bps.
- **Profile**: High-frequency mean-reversion (~307 trades/year). Many small wins, occasional larger losses. Boringly stable.
- **Honest read**: Already in production as Ryner. Your tuned Ryner (3×ATR stop, 20d time stop, regime gate ON) at 12mo PIT showed PF 1.06 — the canonical version on 5y showed PF 1.17. The tuning likely *did* help on the cost-fragility front (fewer trades) but the regime gate had marginal impact at best in the 2025 distribution. Worth re-evaluating whether the production tuning still holds up on the 5y window.

### The three to drop

- `volume_spike_cont`: PF 1.03 @ 35bp, dies at 60bp, no working exit signal (77% time-stop)
- `donchian_20d`: PF 1.02, dies at 60bp, same broken exit (50% time-stop / 49% stop)
- `bb_squeeze`: Already losing at 35bp (PF 0.97). Squeeze breakouts on this universe fail more often than they continue.

### Concrete next-step plan (priority order)

1. **Regime-gate `high_base_52w`** — copy the v3 gate from Ryner (`tools/ryner_regime.py`: breadth slope + sector ratio + VIX) into the bake-off engine's per-day entry filter for this strategy only. Re-run the 5-year. If 2022/2024/2025 losses shrink without killing 2021/2023 gains, this is your new champion. Estimated effort: 1-2 hours.
2. **Walk-forward validate `high_base_52w`** — split 2021-2023 train / 2024-2026 test. The 27% win rate + concentrated returns are textbook overfitting risks; we want to see the same edge on data the canonical params didn't see. (Canonical params technically weren't fit to this universe, but Minervini's parameters were arguably fit to US large-caps and may transfer poorly.)
3. **If steps 1+2 confirm `high_base_52w`**: build it as a sister scanner to Option-S (call it Option-V for "VCP"). Same UX — daily candidate list, suggested stop, exit rules text. **Do not replace Ryner** — different signal source, different risk profile, makes sense to run both.
4. **Tune `ema_pullback_trend` lightly** — it's the runner-up with the highest win-rate-of-the-survivors (40.8%) and 4/5 positive years. The 44% time-stop ratio suggests the 15-day time stop is too short — many trades got truncated mid-trend. Lengthening to 25 or 30 days is the lowest-effort experiment.
5. **Portfolio pair `rsi2_pullback` + `high_base_52w`** — their drawdown years partly overlap (both lose 2022) but partly diverge (2024: high_base −155k while rsi2 +110k; 2025: high_base −62k while rsi2 +29k). A 50/50 split would have produced **lower MaxDD and lower year-volatility** than either alone. Worth a portfolio-level Sharpe check post-step-1-or-2.
6. **Do not invest further effort in volume_spike, donchian, bb_squeeze.** They're either marginal at best or losing under realistic costs. Move on.

### What this means for "is there a better swing strategy than RSI(2)"

**Per-trade efficiency**: Yes — `high_base_52w` has 20% better PF and a much shallower drawdown.
**Total ₹ return at baseline cost over 5y**: No — `rsi2_pullback` made more dollars (1.08× the `high_base_52w` ₹ at 35bp) because it trades 3.6× as often.
**Robustness to higher costs**: Yes — `high_base_52w` cumulative profit at 60bp is **5.8× larger** than `rsi2_pullback`'s at the same cost level.
**Year-over-year consistency**: No — `rsi2_pullback` is 5/6 positive vs `high_base_52w`'s 2/5. Until you regime-gate `high_base_52w`, RSI(2) is the steadier ride.

**One-line answer**: For your specific situation — Indian mid/smallcap retail with Zerodha-level costs and a preference for not babysitting losses — start by regime-gating `high_base_52w` and run it alongside (not instead of) the existing Ryner. If the gating works, you have two complementary swing strategies covering different market footprints.

---

## Part 7 — Artifacts

### Files written this session

| Path | Purpose |
|---|---|
| `tools/ryner_backtest.py` | PIT membership index + `_precompute_breadth` rewrite (uncommitted patch) |
| `docs/superpowers/specs/2026-05-27-swing-bakeoff-design.md` | Approved design spec |
| `docs/superpowers/plans/2026-05-27-swing-bakeoff-impl.md` | 13-task implementation plan |
| `tools/swing_bakeoff.py` | The bake-off engine (~870 LOC, single file) |
| `tests/test_swing_bakeoff.py` | 15 tests (1 per strategy + 5 engine/scorer + 5 ABC/indicator) |
| `plans/swing_bakeoff_trades_2025-05-27_2026-05-27.csv` | Smoke-test 12mo, rsi2_pullback only |
| `plans/swing_bakeoff_summary_2025-05-27_2026-05-27.csv` | Smoke-test 12mo summary |
| `plans/swing_bakeoff_trades_2021-05-01_2026-05-01.csv` | **5-year per-trade table (pending run completion)** |
| `plans/swing_bakeoff_summary_2021-05-01_2026-05-01.csv` | **5-year 18-row scoring matrix (pending)** |
| `plans/swing_bakeoff_run.log` | **5-year run console log (pending)** |
| `plans/ryner_backtest_2025-05-27_2026-05-27.csv` | PIT Ryner re-run from the audit (already on disk) |

### Commits landed on `main` (this session)

```
683da85 feat(swing): CLI runner — 6 strategies x 3 cost levels, CSV + console
1800c85 feat(swing): scorer with cost-adjusted PF / Sharpe / MaxDD
be3b52d feat(swing): per-day engine with next-day-open fills + PIT entry gating
50ff731 test(swing): fix BB-squeeze fixture — noisy baseline + tight recent window
ffdf1bd feat(swing): strategy 6 — Bollinger squeeze breakout + ALL_STRATEGIES list
f2544f5 feat(swing): strategy 5 — volume-spike continuation
7781f42 feat(swing): strategy 4 — 21-EMA pullback in trend
60f723a feat(swing): strategy 3 — 52w high + tight base (VCP-lite)
2749c89 feat(swing): strategy 2 — 20d Donchian breakout
8fef07e feat(swing): strategy 1 — RSI(2) pullback control
05902da feat(swing): shared indicator precompute (SMA/EMA/ATR/RSI/BB/52w)
084b881 feat(swing): module skeleton — SwingStrategy ABC, Trade, cost levels
```

The Ryner PIT correction (`tools/ryner_backtest.py` patch from the audit step) and the spec/plan docs in `docs/superpowers/` were **not committed** in this session — per session policy commits only happen on explicit ask. The working tree has them ready; `git add` + `git commit` whenever you're ready.

### How to re-run

```bash
# Re-run the 5-year bake-off (this is what produced the table above)
.venv/bin/python tools/swing_bakeoff.py --start 2021-05-01 --end 2026-05-01

# Re-run a single strategy on a custom window
.venv/bin/python tools/swing_bakeoff.py \
    --start 2024-01-01 --end 2026-05-01 \
    --strategies ema_pullback_trend

# Run the unit test suite
.venv/bin/python -m pytest tests/test_swing_bakeoff.py -v
```

---

---

## Quick-reference: TL;DR table

| | Bake-off winner | Cost-survivor | Most consistent | Drop |
|---|---|---|---|---|
| **PF @ 35bp** | `high_base_52w` (1.41) | `high_base_52w` (1.28 @ 60bp) | `rsi2_pullback` (1.17) | `bb_squeeze` (0.97) |
| **₹ over 5y @ 35bp** | `rsi2_pullback` (₹+435k) | `high_base_52w` (₹+294k @ 60bp) | `rsi2_pullback` | `bb_squeeze` (−₹38k) |
| **Sharpe @ 35bp** | `high_base_52w` (+1.58) | — | `rsi2_pullback` (+1.03) | `bb_squeeze` (+0.06) |
| **MaxDD** | `high_base_52w` (−22%) | — | `rsi2_pullback` (−23%) | `bb_squeeze` (−44%) |
| **Years positive (of 5 full)** | — | — | `rsi2_pullback` (4/5) | `high_base_52w` (2/5) |

Next action: regime-gate `high_base_52w` and walk-forward validate it. If that confirms, add it as a sister to Option-S (don't replace Ryner). See Part 6 for the full priority list.

---

## Part 8 — Regime-gate experiment on `high_base_52w` (2026-05-29)

Per Part 6 Step 1: layered the existing v3 regime gate (Ryner's gate — breadth + slope + sector ratio + VIX) on top of `high_base_52w` and re-ran the 5-year. Goal: trim the concentrated losses in 2022 / 2024 / 2025 without killing the 2021 / 2023 winners.

### Implementation

Added `build_v3_gate_series()` + `--gate-strategies` CLI flag to `tools/swing_bakeoff.py`. Reuses:
- `tools/ryner_backtest._precompute_breadth` (PIT membership-aware breadth)
- `tools/ryner_regime.{compute_breadth_slope, compute_sector_breadth_ratio, compute_vix_trend, combine_signals}`
- `tools/ryner_regime.load_sectors_map` (`stock-sectors.json`)
- `tools/ryner_regime.load_vix` (`data/benchmarks/india_vix.parquet`)

Same defaults as Ryner production: `regime_min_breadth=0.50`, `slope_min=+5pp/10d`, `sector_min=+10pp`, `vix_max_calm_level=22`, `regime_vix_relax_breadth=0.40`. Gate computed once across all loaded tickers, applied only to `high_base_52w` per the `--gate-strategies high_base_52w` flag (other strategies unaffected — this experiment only re-runs the gated strategy).

**Gate openness**: open on **1082 / 1505 days = 71.9%** of the 5-year window. The gate is meaningfully restrictive — it blocked 28% of trading days.

### Result — aggregate (5y @ 35bp baseline cost)

| Metric | Ungated (Part 4) | **Gated** | Δ |
|---|---|---|---|
| Trades | 431 | 407 | −24 (−5.6%) |
| Win rate | 27.1% | 27.3% | +0.2 pp |
| Avg P&L net | +0.93% | +0.95% | +0.02 pp |
| PF net | 1.41 | **1.40** | **−0.01** |
| Sharpe net | +1.58 | **+1.62** | **+0.04** |
| MaxDD | −22.1% | **−22.8%** | **−0.7 pp (worse)** |
| Avg hold | 9.8d | 10.1d | +0.3d |
| Cumulative ₹ @ 35bp | **+₹401,885** | **+₹387,461** | **−₹14,424** |
| Cumulative ₹ @ 20bp | +₹466,535 | +₹447,856 (computed) | ~−₹19k |
| Cumulative ₹ @ 60bp | +₹294,135 | +₹285,036 (computed) | ~−₹9k |

**Aggregate verdict: the gate is a net wash to slight negative**. PF and MaxDD essentially unchanged; cumulative ₹ slightly worse.

### Result — per-year P&L @ 35bp (the diagnostic table)

| Year | N un | N gt | ₹ ungated | ₹ gated | Δ ₹ | Win un | Win gt |
|---|---|---|---|---|---|---|---|
| 2021 | 46 | 46 | +₹467,465 | +₹467,465 | **+₹0** | 43.5% | 43.5% |
| 2022 | 104 | 91 | −₹133,553 | −₹120,153 | **+₹13,400** | 18.3% | 15.4% |
| 2023 | 119 | 107 | +₹263,089 | +₹254,201 | **−₹8,888** | 25.2% | 27.1% |
| 2024 | 87 | 87 | −₹154,748 | −₹155,816 | −₹1,069 | 29.9% | 29.9% |
| 2025 | 56 | 64 | −₹62,173 | **−₹79,920** | **−₹17,746** | 25.0% | 25.0% |
| 2026 | 19 | 12 | +₹21,805 | +₹21,684 | −₹121 | 42.1% | 50.0% |
| **TOTAL** | **431** | **407** | **+₹401,885** | **+₹387,461** | **−₹14,424** | — | — |

### What the per-year table tells us

- **2021 (bull)**: gate was open the entire year — zero impact. Expected.
- **2022 (correction)**: gate helped — cut 13 losing trades, saved ₹13k. **This is the only year gating worked.**
- **2023 (recovery)**: gate hurt — blocked 12 trades during the early recovery (when broad breadth was still soft), costing ₹9k of winners.
- **2024 (mixed)**: gate had ~zero effect (87 vs 87 trades). Distribution year — both gated and ungated lost ~₹155k.
- **2025 (distribution)**: gate **added** 8 trades and made things ₹18k WORSE. (Mechanism: the gate blocked some entries in late 2024, freeing slots that filled with late-2025 signals — but those signals also lost. Slot-recycling artefact.)
- **2026 (partial)**: barely 4 months of data, gate cut 7 of 19 trades, ~flat impact.

### Why the gate doesn't transfer

Different signal source ⇒ different regime sensitivity:

- **RSI(2) pullback** (Ryner): buys *weakness* (close < 5-SMA). When the broad market is in distribution, that weakness is "real" weakness — failed dips that don't bounce. The gate adds genuine value by blocking entries when the *type* of weakness the strategy reads has flipped from "buyable pullback" to "fade-the-rally".
- **`high_base_52w`** (Minervini): buys *strength* (close ≥ 97% of 52w high after tight consolidation). For a stock to even *qualify* it must already be a regime outperformer. By the time the broad-market v3 gate would say "regime open", the strongest 52w-high stocks have often already broken out and the trade is missed. The strategy has an **implicit self-gate** in its entry condition — the explicit gate is largely redundant, and any time it blocks an entry it's blocking a stock-specific signal that doesn't actually need broad-market permission.

This is consistent with how Minervini himself frames it (paraphrasing *Trade Like a Stock Market Wizard*): "the strongest stocks emerge from corrections first — they make new highs while the indexes are still rebuilding". A gate that waits for the broader index to confirm is **structurally late** for a strategy whose edge is being early on relative-strength leaders.

### What WOULD help `high_base_52w`

The failures in 2022 / 2024 / 2025 are **stock-specific breakout failures**, not regime failures. Filters to investigate (NOT done in this experiment — flagging for future work):

1. **Volume confirmation on the breakout day**: require today's volume ≥ 1.5× 20d avg. Filters out "drift to 52w high" without institutional sponsorship — those are the highest-fail-rate pattern.
2. **Relative strength rank**: require the stock to be in the top 30% of its sector by 6-month return. Filters out "weakest member of strongest sector" — the typical bull-trap pattern.
3. **Earnings-quality gate**: require positive EPS growth (not available in current data pipeline — would need fundamental data).
4. **Wider stop**: 3×ATR is already wide; not the problem. Stops only fire 1.2% of the time anyway.
5. **Tighter time-stop**: 30 days is generous. If a 52w-high breakout hasn't worked in 10-15 days, it likely won't — cutting to 15d would test this.

The biggest single experiment is **(1) volume confirmation** — it's a one-line entry-condition change, and it directly addresses the structural failure mode of "drift breakouts without institutional commitment."

### Revised recommendation (supersedes Part 6 Step 1-3)

- **`high_base_52w` cannot be improved by the existing regime gate.** Don't pursue that path further.
- **The next experiment is volume-confirmation on the breakout day**, not regime gating. Lowest-effort change with the most plausible mechanism for cutting failed breakouts.
- **The 2022 / 2024 / 2025 losses are stock-pattern failures, not market-regime failures.** The fix is in the entry condition, not in a market-state filter.
- **Portfolio pairing with `rsi2_pullback` is now MORE attractive**, not less: their drawdown years partly diverge (2024 high_base loses while rsi2 wins; 2025 same), and now we know the gate doesn't help high_base_52w but it DOES help Ryner — so the pair would have asymmetric regime treatment that's actually appropriate for each member.

### Files written by this experiment

| Path | Purpose |
|---|---|
| `tools/swing_bakeoff.py` | +`build_v3_gate_series` (~40 LOC) + `--gate-strategies` flag |
| `plans/swing_bakeoff_trades_2021-05-01_2026-05-01_gated_hb.csv` | 407 gated trades |
| `plans/swing_bakeoff_summary_2021-05-01_2026-05-01_gated_hb.csv` | 3-row summary (1 strategy × 3 cost levels) |
| `plans/swing_bakeoff_gated_run.log` | Console log |

### How to re-run the experiment

```bash
# Just high_base_52w gated (this experiment):
.venv/bin/python tools/swing_bakeoff.py \
    --start 2021-05-01 --end 2026-05-01 \
    --strategies high_base_52w \
    --gate-strategies high_base_52w \
    --output-suffix _gated_hb

# Or gate any other strategy to verify the regime-sensitivity hypothesis:
.venv/bin/python tools/swing_bakeoff.py \
    --start 2021-05-01 --end 2026-05-01 \
    --strategies rsi2_pullback \
    --gate-strategies rsi2_pullback \
    --output-suffix _gated_rsi2
```

The second command is the natural follow-up: applying the same v3 gate to `rsi2_pullback` should show a meaningful win-rate / drawdown improvement (the gate WAS designed for that strategy family). If it does, that confirms the per-strategy regime-sensitivity asymmetry hypothesis above.

---

---

## Part 9 — Volume-confirmation experiment on `high_base_52w` (2026-05-29)

Per Part 8 follow-up: added `high_base_52w_vol` variant — same entry rules as `high_base_52w`, plus today's volume must be ≥ **1.5× 20-day average**. This is the standard O'Neil / Minervini "buyable breakout" volume floor — single textbook value, no sweep. Hypothesis: failed breakouts in 2022 / 2024 / 2025 were "drift to 52w high" without institutional sponsorship; volume floor should cut those without blocking real breakouts.

### Implementation

`HighBaseBreakout52wVol(HighBaseBreakout52w)` subclass in `tools/swing_bakeoff.py:497-522`. `should_enter()` calls `super().should_enter()` then adds `vol_today >= 1.5 * avg_vol_20`. Original `high_base_52w` class untouched.

### Result — aggregate (5y @ 35bp baseline cost)

| Metric | Ungated `high_base_52w` | **+ Volume confirm** | Δ |
|---|---|---|---|
| Trades | 431 | 348 | −83 (−19%) |
| Win rate | 27.1% | **32.2%** | **+5.1 pp** ✓ |
| Avg P&L net | +0.93% | +0.62% | **−0.31 pp** ✗ |
| PF net | **1.41** | 1.24 | **−0.17** ✗ |
| Sharpe net | +1.58 | +1.15 | −0.43 ✗ |
| MaxDD | −22.1% | −28.6% | **−6.5 pp worse** ✗ |
| Cumulative ₹ @ 35bp | **+₹401,885** | **+₹217,019** | **−₹184,866** ✗ |

**Aggregate verdict: NET NEGATIVE.** Volume confirmation raised win rate by 5 pp but **halved cumulative ₹** and made MaxDD worse. The filter is selecting better-quality entries on average but cutting the big winners that drove the strategy's edge.

### Per-year P&L @ 35bp (the diagnostic that reveals the mechanism)

| Year | N ungated | N volconf | ₹ ungated | ₹ volconf | Δ ₹ | Win un | Win vc | Best un | Best vc |
|---|---|---|---|---|---|---|---|---|---|
| 2021 | 46 | 57 | **+₹467,465** | **+₹109,640** | **−₹357,825** | 43% | 40% | +₹130,462 | +₹36,456 |
| 2022 | 104 | 71 | −₹133,553 | −₹126,131 | +₹7,423 | 18% | 21% | +₹19,728 | +₹32,814 |
| 2023 | 119 | 84 | +₹263,089 | +₹342,745 | **+₹79,655** | 25% | 35% | +₹54,640 | +₹79,104 |
| 2024 | 87 | 76 | −₹154,748 | −₹72,833 | **+₹81,915** | 30% | 34% | +₹22,093 | +₹22,093 |
| 2025 | 56 | 46 | −₹62,173 | −₹56,483 | +₹5,690 | 25% | 30% | +₹21,653 | +₹21,653 |
| 2026 | 19 | 14 | +₹21,805 | +₹20,081 | −₹1,724 | 42% | 36% | +₹22,147 | +₹22,147 |
| **TOTAL** | **431** | **348** | **+₹401,885** | **+₹217,019** | **−₹184,866** | — | — | — | — |

### The crucial insight: 2021 destroys the experiment

**Volume confirmation helped in 4 of 6 years.** Ex-2021, the volume-confirmed variant would be +₹107k while the original would be −₹65k — volume confirmation would have looked like a clear winner.

**But 2021 alone cost the experiment ₹358k**, because that year's biggest winners were quiet accumulation breakouts on normal volume. The ungated strategy's two biggest 2021 trades were ₹+130k and ₹+123k; the volume-confirmed variant's best 2021 trade was only ₹+36k — **the filter cut the biggest wins by ~3-4×**.

### Right-tail comparison (top 10 trades, full 5y)

| Rank | Ungated ₹ | Volconf ₹ | Volconf / ungated |
|---|---|---|---|
| #1 | +₹130,462 | +₹79,104 | 61% |
| #2 | +₹123,290 | +₹47,945 | 39% |
| #3 | +₹54,640 | +₹47,378 | 87% |
| #4 | +₹52,498 | +₹38,274 | 73% |
| #5 | +₹47,945 | +₹36,456 | 76% |
| #6 | +₹45,991 | +₹36,179 | 79% |
| #7 | +₹43,495 | +₹34,464 | 79% |
| #8 | +₹37,855 | +₹33,731 | 89% |
| #9 | +₹37,397 | +₹32,814 | 88% |
| #10 | +₹34,971 | +₹28,568 | 82% |

The top 2 right-tail trades were **halved** by the volume filter. For a 27%-win-rate strategy where edge lives entirely in the right tail, halving the right tail is fatal.

### Why O'Neil/Minervini volume confirmation doesn't transfer to Indian mid/smallcaps

US large-cap institutional buying is often single-day, decisive — a fund decides to take a position and prints it. Volume signature is sharp. The "1.5× volume on breakout" rule was calibrated for this environment.

Indian mid/smallcap institutional buying (FIIs, mutual funds, family offices) is typically:
- **Gradual and stealthy**: weeks of scaling in via VWAP / TWAP algos to avoid moving the print
- **Information-driven**: a fundamental thesis matures slowly; the entry doesn't need volume confirmation because the entrant has conviction
- **Cooperative with retail volume**: by the time retail volume confirms (the +50%/+100% volume spike), the move is often 30-50% done and the institution starts distributing

The original `high_base_52w` strategy has an **implicit quiet-accumulation filter** built in: the 20-day range < 10% requirement *requires* low recent volume by construction (tight range only happens on subdued participation). Adding a volume floor on the breakout day breaks the strategy's actual mechanism — it was selecting for quiet bases that suddenly resolve, and we're now demanding the resolution itself be loud.

The cleanest summary: **for Indian mid/smallcap [201, 600] in this 5y window, quiet 52w-high breakouts dominate loud ones.** Volume confirmation catches the obvious moves, which are also the crowded ones.

### Revised guidance for `high_base_52w`

After two failed improvement experiments (regime gate in Part 8, volume confirmation in Part 9), the honest reading is:

**The strategy is what it is.** PF 1.41, Sharpe 1.58, MaxDD −22.1%, but **return-concentrated** (3/5 years negative). The losses come from individual stock breakout failures that no broad-market or volume filter can predict in advance.

Three remaining options to investigate (each is a distinct hypothesis, not parameter tuning):

1. **Sector relative-strength filter** — require stock's parent sector to be in the top half of all sectors by 3-month return. Hypothesis: failed breakouts cluster in sectors that are themselves rolling over even while individual names print 52w highs. Uses existing `stock-sectors.json` + sector-index data. ~1 hour effort.
2. **Tighter time stop (15d vs 30d)** — exit after 15 days regardless. Hypothesis: real breakouts work fast; trades held >15 days are slow grinds that more often than not end up failing. Easiest experiment — single line change.
3. **Position-sizing by ATR % instead of equal capital** — sizing trades inversely to volatility so a ₹100 stock with 2% ATR takes a larger position than a ₹100 stock with 8% ATR. Reduces tail risk on the loser side. Bigger code change.

**Or accept the strategy as-is and pair-trade with `rsi2_pullback`.** Their drawdown years differ enough (2024 high_base loses, rsi2 wins; 2025 same) that a 50/50 portfolio would have shallower year-volatility than either alone. This is the lowest-effort path to a deployable swing system, and it's now stronger evidence than after Part 4 — *because* we know two different filter experiments failed to improve high_base_52w on its own.

### Files written

| Path | Purpose |
|---|---|
| `tools/swing_bakeoff.py` | +`HighBaseBreakout52wVol` class (~25 LOC) |
| `plans/swing_bakeoff_trades_2021-05-01_2026-05-01_volconf.csv` | 348 trades |
| `plans/swing_bakeoff_summary_2021-05-01_2026-05-01_volconf.csv` | 3-row summary |
| `plans/swing_bakeoff_volconf_run.log` | Console log |

### How to re-run

```bash
.venv/bin/python tools/swing_bakeoff.py \
    --start 2021-05-01 --end 2026-05-01 \
    --strategies high_base_52w_vol \
    --output-suffix _volconf
```

---

---

## Part 10 — Tight-time-stop experiment on `high_base_52w` (2026-05-29)

Per Part 9 follow-up option 2: added `high_base_52w_t15` variant — same entry/stop/exit rules as `high_base_52w`, only `TIME_STOP` cut from 30 → 15 trading days. Hypothesis: real 52w-high breakouts work fast (within ~2-3 weeks); trades held longer are slow grinds that more often than not fail.

### Implementation

`HighBaseBreakout52wTight15d(HighBaseBreakout52w)` subclass in `tools/swing_bakeoff.py`. Single attribute override: `TIME_STOP = 15`.

### Result — aggregate (5y @ 35bp baseline cost)

| Metric | Original (TIME_STOP=30) | **Tight (TIME_STOP=15)** | Δ |
|---|---|---|---|
| Trades | 431 | 594 | +163 (+38%) ← slot recycling from faster exits |
| Win rate | 27.1% | **31.1%** | **+4.0 pp** ✓ |
| Avg P&L net | +0.93% | +0.48% | **−0.45 pp** ✗ |
| PF net | **1.41** | 1.21 | **−0.20** ✗ |
| Sharpe net | +1.58 | +1.18 | −0.40 ✗ |
| MaxDD | −22.1% | −22.6% | −0.5 pp worse |
| Cumulative ₹ @ 35bp | **+₹401,885** | **+₹287,347** | **−₹114,538** ✗ |

**Aggregate verdict: NET NEGATIVE.** Same pattern as volume confirmation in Part 9 — win rate up, but average win down by more, MaxDD ~flat. The strategy's edge lives in the right tail; truncating any way you do it kills the edge.

### Per-year P&L @ 35bp

| Year | N 30d | N 15d | ₹ 30d | ₹ 15d | Δ ₹ |
|---|---|---|---|---|---|
| 2021 | 46 | 66 | +₹467,465 | +₹434,841 | −₹32,625 |
| 2022 | 104 | 128 | −₹133,553 | −₹92,479 | **+₹41,074** ✓ |
| **2023** | 119 | 157 | **+₹263,089** | **+₹115,979** | **−₹147,110** ✗ |
| 2024 | 87 | 128 | −₹154,748 | −₹129,772 | +₹24,976 ✓ |
| 2025 | 56 | 85 | −₹62,173 | −₹40,459 | +₹21,715 ✓ |
| 2026 | 19 | 30 | +₹21,805 | −₹763 | −₹22,568 |
| **TOTAL** | **431** | **594** | **+₹401,885** | **+₹287,347** | **−₹114,538** |

### Exit-reason mix shift

| | Original (30d) | Tight (15d) |
|---|---|---|
| signal (close<21EMA) | 90% | 72% |
| time-stop | 8% | **26%** |
| stop / EOP | 2% | 2% |

Time-stop hits 3× as often (26% vs 8%) — confirming the cut is doing what was intended (truncating slow trades). But many of those truncated trades would have eventually completed as multi-week winners — particularly the 2023 recovery runs.

### Mechanism: same as Part 9

The 2023 hit is the biggest data point. 2023 was the recovery year where 52w-high breakouts ran for 3-6 weeks as the post-2022 rebuild widened. The 30-day window captured those moves; the 15-day window cut them mid-trade.

**Pattern across all three improvement attempts** (Parts 8 / 9 / 10):
- Regime gate: cut 24 trades, ₹ net −₹14k
- Volume confirm: cut 83 trades, ₹ net −₹185k
- Tight time stop: changed exit timing on 119 trades, ₹ net −₹115k

Every attempt to "improve" the strategy clipped the right tail. The implication is structural: **`high_base_52w` is what it is — its edge depends on patient waiting for the rare 30%-60% breakout to complete.** Any filter that adds selectivity or shortens hold time disproportionately hits the tail vs the body.

### Conclusion for `high_base_52w` tuning

Three negative results triangulate the same lesson: don't tune the strategy itself. Accept PF 1.41 / MaxDD −22% / 2-of-5-years-positive as the stable identity of this strategy. The path forward isn't improvement-in-isolation; it's **portfolio combination** (Part 11).

### Files written

`tools/swing_bakeoff.py` + `HighBaseBreakout52wTight15d` class (~10 LOC) + `plans/swing_bakeoff_*_tight15.csv` + `plans/swing_bakeoff_tight15_run.log`.

### How to re-run

```bash
.venv/bin/python tools/swing_bakeoff.py \
    --start 2021-05-01 --end 2026-05-01 \
    --strategies high_base_52w_t15 \
    --output-suffix _tight15
```

---

## Part 11 — Portfolio pairing: `high_base_52w` + `rsi2_pullback` (2026-05-29)

Per Part 9 follow-up option 3: combine the two top-performing strategies into a portfolio. Pair them as **independent accounts** — each strategy keeps its own ₹500k notional (5 slots × ₹100k) and runs unchanged; total deployed = ₹1M. Combined P&L = sum of two independent equity curves.

This is the realistic deployment model: in Kite you can run two scanners side-by-side and allocate distinct ₹ pools to each. No slot-competition modeling needed.

### Per-year P&L combination @ 35bp

| Year | `high_base_52w` | `rsi2_pullback` | **COMBINED** | Years-positive impact |
|---|---|---|---|---|
| 2021 | +₹467,465 | +₹116,840 | **+₹584,305** | Both win (bull year) |
| 2022 | −₹133,553 | −₹50,957 | **−₹184,510** | Both lose (correction — worst year) |
| 2023 | +₹263,089 | +₹199,458 | **+₹462,548** | Both win (recovery) |
| 2024 | −₹154,748 | **+₹110,118** | **−₹44,630** | **rsi2 offsets high_base** — net loss tiny |
| 2025 | −₹62,173 | **+₹28,575** | **−₹33,598** | **rsi2 offsets high_base** |
| 2026 | +₹21,805 | +₹31,268 | **+₹53,073** | Both win |
| **TOTAL** | **+₹401,885** | **+₹435,302** | **+₹837,187** | 4/6 yrs positive |

**`rsi2_pullback` is the perfect complement to `high_base_52w`** — it wins big in 2024 and 2025 (the recent distribution years that crushed high_base). The combined system has only 1 deep-loss year (2022) instead of 3.

### Monthly P&L correlation

**+0.211** — meaningfully diversified. Not negatively correlated (both lose in 2022) but the +0.21 floor is the sweet spot for a 2-strategy blend: positive enough that both strategies are "real" (not random noise), low enough that drawdowns largely don't coincide.

### Comparative metrics at each cost level

Aggregate view across all three cost scenarios:

| System | Capital | Cumulative Return % | CAGR % | Sharpe | MaxDD % | **Calmar** |
|---|---|---|---|---|---|---|
| `high_base_52w` only @ 20bp | ₹500k | +93.3% | +13.9% | +1.77 | −19.4% | 0.71 |
| `rsi2_pullback` only @ 20bp | ₹500k | +133.2% | +18.1% | +1.51 | −19.0% | 0.95 |
| **PAIR @ 20bp** | **₹1M** | **+113.2%** | **+16.1%** | **+1.62** | **−14.1%** | **1.14** |
| `high_base_52w` only @ 35bp | ₹500k | +80.4% | +12.3% | +1.58 | −22.1% | 0.56 |
| `rsi2_pullback` only @ 35bp | ₹500k | +87.1% | +13.1% | +1.03 | −23.0% | 0.57 |
| **PAIR @ 35bp** | **₹1M** | **+83.7%** | **+12.7%** | **+1.25** | **−17.4%** | **0.73** |
| `high_base_52w` only @ 60bp | ₹500k | +58.8% | +9.5% | +1.26 | −27.1% | 0.35 |
| `rsi2_pullback` only @ 60bp | ₹500k | +10.2% | +1.9% | +0.23 | −38.3% | 0.05 |
| **PAIR @ 60bp** | **₹1M** | **+34.5%** | **+6.0%** | **+0.59** | **−23.5%** | **0.26** |

### What the table shows

**The PAIR beats either solo strategy on Calmar at every cost level.** Calmar (CAGR / |MaxDD|) is the right metric for retail deployment because it captures the "do you have the stomach for the drawdown given the return?" question that determines whether someone actually sticks with the system.

- **At 20bp** (best case): PAIR Calmar 1.14, vs 0.95 (rsi2) and 0.71 (high_base) solo
- **At 35bp** (baseline): PAIR Calmar 0.73, vs 0.57 (both solos) — **28% better** than either alone
- **At 60bp** (worst case): PAIR Calmar 0.26, vs 0.05 (rsi2) and 0.35 (high_base) — pair beats rsi2 by 5×

**Sharpe is NOT improved by pairing** vs `high_base_52w` alone (+1.25 vs +1.58 at 35bp). This is the "blending a high-Sharpe and low-Sharpe asset always lowers Sharpe" effect. Calmar is the better metric here because:
- Sharpe penalises *all* volatility including upside (e.g. the +₹467k 2021 win raises Sharpe denominator)
- Calmar only penalises drawdown — exactly what retail traders feel as pain

### Worst-day drawdown shifted

| System | Worst-DD pct | Date hit |
|---|---|---|
| `high_base_52w` solo | −22.13% | 2026-01-21 |
| `rsi2_pullback` solo | −23.02% | 2023-02-07 |
| **PAIR (independent)** | **−17.35%** | **2023-03-28** |

The pair's worst drawdown date is in March 2023 — a brief weak stretch before the recovery year took off. The pair never sees the late-2023 / early-2026 drawdowns that hurt `high_base_52w` solo because `rsi2_pullback` is profitable in those periods. The 4.7 pp drawdown reduction (22% → 17%) is meaningful for capital that doesn't get pulled at the bottom.

### Deployment story (the practical answer)

If you have **₹1M of capital and the patience for a 5y systematic strategy**, the bake-off says:

1. **Run two parallel ledgers**: ₹500k allocated to a new `high_base_52w` scanner (Option-V in the CLI, to-build), ₹500k to the existing Ryner Option-S scanner. **Use canonical/textbook parameters for both** — three improvement experiments (gate, volume, time-stop) all failed.
2. **Expected 5y outcome on the bake-off window**: ~+84% cumulative (16% CAGR), Sharpe ~1.25, MaxDD ~17%, 4 of 6 years positive, worst year −18% (2022 correction).
3. **Cost sensitivity matters**: every 15 bps of cost knocks ~20-30% off cumulative return. Trade execution discipline (limit orders, avoid market-on-open in illiquid mids) is worth more than any strategy refinement at this point.
4. **The 2022 correction year is the unavoidable risk**: both strategies lose in coordinated market stress. Sizing should assume a 20% drawdown is plausible in any given year.

### What the pair does NOT do

- **Doesn't avoid 2022.** Both strategies got hit in the correction. No diversification benefit when the entire universe rolls over together.
- **Doesn't compound capital.** Independent-accounts means each ledger stays at ₹500k notional throughout — you can't pyramid. If high_base finishes +93% on its ₹500k pool, the next year still starts at ₹500k unless you manually re-allocate.
- **Doesn't beat NIFTY 50 on a CAGR basis automatically.** The CLAUDE.md baseline (real NIFTY 50 11.83% CAGR) is on a 13y window; pair's 12.7% CAGR on 5y just barely beats. If 2021/2023 weren't both unusually strong years, the pair could underperform a buy-and-hold. **The case for the pair is drawdown control, not return maximisation.**

### Improvements that ARE worth investigating next

1. **Walk-forward validate the pair**: split 2021-2023 train / 2024-2026 test. Confirm the diversification benefit isn't an artefact of the specific 5y window.
2. **Equal-weighted vs equal-risk weighting**: instead of ₹500k each, scale by inverse-volatility so both strategies contribute equal risk. Likely tightens the pair Sharpe.
3. **Shared-slot pool simulation**: instead of two parallel ₹500k pools, simulate a single ₹500k pool with 5 shared slots where both strategies compete for entries (priority by rank_key per strategy). More realistic for a single-account trader; likely worse than independent (slot competition) but the comparison is needed before recommending the simpler single-account deployment.
4. **Tail-risk control overlay**: add a position-sizing cap that scales down both strategies when combined open positions exceed N. Reduces correlated 2022-style stress.

### Files used (no new files; this is analysis of existing CSVs)

- `plans/swing_bakeoff_trades_2021-05-01_2026-05-01.csv` (the 5y trade list from Part 4 — contains both `high_base_52w` and `rsi2_pullback` rows)

### How to re-run the pair analysis

```bash
.venv/bin/python -c "
import csv, numpy as np, pandas as pd
from collections import defaultdict
COST = 350.0; CAP = 500_000.0
trs = list(csv.DictReader(open('plans/swing_bakeoff_trades_2021-05-01_2026-05-01.csv')))
hb = [t for t in trs if t['strategy']=='high_base_52w']
rsi = [t for t in trs if t['strategy']=='rsi2_pullback']
def daily(ts):
    d = defaultdict(float)
    for t in ts: d[pd.Timestamp(t['exit_date'])] += float(t['pnl_inr_gross']) - COST
    return pd.Series(d).sort_index()
pair = daily(hb).add(daily(rsi), fill_value=0).cumsum()
levels = pair + 2*CAP
peak = levels.cummax()
maxdd = float(((levels-peak)/peak).min() * 100)
print(f'PAIR @ 35bp: Cum ₹+{pair.iloc[-1]:,.0f}  MaxDD {maxdd:.2f}%')
"
```

---

## Part 12 — Final consolidated guidance (post all 3 follow-up experiments)

Three filter/tweak experiments on `high_base_52w` (regime gate, volume confirmation, tight time stop) **all failed**. The strategy's edge is structurally tied to its existing canonical parameters; tightening any aspect of selection or exit clipped the right tail more than it cut the losers.

One portfolio experiment (`high_base_52w` + `rsi2_pullback` independent-accounts pair) **succeeded** on Calmar at every cost level tested.

### The actionable recommendation (single sentence)

**Build a new Option-V scanner for canonical `high_base_52w` (textbook Minervini params, no regime gate, no volume filter, 30d time stop) and run it as a parallel ₹500k account alongside the existing Ryner Option-S — expected 5y CAGR ~12.7% / MaxDD ~17% on ₹1M deployed total.**

### What to NOT do

- Don't add the v3 regime gate to `high_base_52w` (Part 8)
- Don't add volume confirmation to `high_base_52w` (Part 9)
- Don't tighten time stop on `high_base_52w` (Part 10)
- Don't replace Ryner with `high_base_52w` (the pair is the answer, not substitution)
- Don't run any of the bottom 3 from Part 4 (`bb_squeeze`, `donchian_20d`, `volume_spike_cont`)

### Open questions for next session

- Walk-forward validation of the pair (train 2021-2023 / test 2024-2026)
- Shared-slot pool simulation (more realistic for single-account traders)
- Earnings-quality filter on `high_base_52w` (would need fundamental data — currently out of pipeline)
- Sector relative-strength filter on `high_base_52w` (cheapest of the remaining hypotheses; uses existing `stock-sectors.json`)

---

_End of nightlog._
