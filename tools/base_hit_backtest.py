"""Base-hit compounding backtest — "+2% and rotate", ≤1-week holds.

Validates the idea: compound Rs 20L by taking many small +2% wins on
high-hit-rate short-horizon entries, holding at most a week, and — critically —
optimising the NON-WINNER exit so idle capital recycles fast into the next
winner instead of bleeding on stops.

Design (all point-in-time, survivorship-free):
  * Tradeable universe: any name (cap-agnostic) whose 20-day median rupee
    turnover >= FLOOR — i.e. you can actually deploy Rs 20L without moving it.
  * Entry signal: RSI(2) oversold WHILE above the 200-SMA (uptrend pullback =
    high-probability bounce — the classic base-hit engine). Pick the most
    oversold name(s).
  * Exit: TARGET +2% (limit fill on any day's high). Non-winner exit is the
    optimisation variable:
      - scratch : exit at breakeven the first day close returns to >= entry
                  (no loss leg — recycle fast); time-stop at market on day H.
      - timestop: no scratch; just exit at market on day H.
      - cutloss : scratch + a hard stop at -S% (caps tail losses).
  * Costs: round-trip COST (STT+fees+slippage) charged once per cycle.
  * Serial single-name ("target 1") and a 3-slot variant (concentration hedge).

Metrics per config: trades, win%/scratch%/loss%/time%, avg hold days,
cycles/yr, net CAGR, max DD — plus a WALK-FORWARD across regimes. Run:
    .venv/bin/python tools/base_hit_backtest.py

FINDING (2016-2026, Rs20L, 0.4% cost): NOT WORTHY — net-negative in EVERY
exit model, slot count (1 & 3), and walk-forward window; MaxDD -83% to -95%
(single-name serial compounding = ruin). It fails on EXPECTANCY, not hit-rate:
win rates are healthy (47-71%) but avgWIN is capped at +2.0% by design while
avgLOSS runs -3% to -5% — a truncated-winner / uncapped-loser NEGATIVE SKEW.
Break-even needs a ~73%+ hit rate at these loss sizes, which isn't sustainable
net of cost. The "scratch/breakeven" exit was the best-behaved (removes the
hard-loss leg) but insufficient — names that DON'T recover to breakeven within
the week are exactly the worst losers. Concentration (3 slots) cuts variance,
not the sign. LESSON: capping upside at a fixed small target is the wrong side
of the equity payoff skew; winners must be let run (that's what the momentum
sleeve already does), the OPPOSITE of "+2% and out".
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

CAPITAL = 2_000_000            # Rs 20L
TARGET = 0.02                  # +2% per trade
TURNOVER_FLOOR = 25e7          # Rs 25 cr/day median -> Rs 20L is <1% of ADV
RSI_THR = 10.0                 # oversold entry
HOLD_CAP = 5                   # <= 1 trading week


def _load(start: date, end: date):
    """Build date x symbol matrices for the liquid (ever-v2) universe."""
    from nse_universe.core.db import db
    from fortress.nse_data_loader import load_historical_bulk
    with db(read_only=True) as con:
        syms = [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM universe_v2 WHERE as_of_date >= ? AND passes",
            [start - timedelta(days=400)]).fetchall()]
    print(f"  loading {len(syms)} liquid symbols {start}..{end} ...", flush=True)
    data = load_historical_bulk(start=start - timedelta(days=400), end=end, symbols=syms)
    close, high, low, vol = {}, {}, {}, {}
    for s, df in data.items():
        if df is None or len(df) < 260:
            continue
        close[s], high[s], low[s], vol[s] = df["close"], df["high"], df["low"], df["volume"]
    C = pd.DataFrame(close).sort_index()
    H = pd.DataFrame(high).reindex_like(C)
    L = pd.DataFrame(low).reindex_like(C)
    V = pd.DataFrame(vol).reindex_like(C)
    return C, H, L, V


def _signals(C, V):
    """RSI(2), 200-SMA gate, 20d turnover gate -> a boolean entry matrix + rsi."""
    delta = C.diff()
    gain = delta.clip(lower=0).rolling(2).mean()
    loss = (-delta.clip(upper=0)).rolling(2).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi2 = (100 - 100 / (1 + rs)).where(loss != 0, 100.0)
    sma200 = C.rolling(200).mean()
    turn20 = (C * V).rolling(20).median()
    entry_ok = (rsi2 <= RSI_THR) & (C > sma200) & (turn20 >= TURNOVER_FLOOR)
    return rsi2, entry_ok


def _simulate(C, H, L, rsi2, entry_ok, dates, *, model: str, stop: float,
              cost: float, slots: int) -> dict:
    """Serial compounding sim. `slots` positions run in parallel on equal capital
    thirds; each recycles independently on exit. Returns stats."""
    syms = list(C.columns)
    ci = {s: i for i, s in enumerate(syms)}
    Cv, Hv, Lv, Rv, Ev = C.values, H.values, L.values, rsi2.values, entry_ok.values
    n = len(dates)
    per_slot = CAPITAL / slots
    slot_cap = [per_slot] * slots
    pos = [None] * slots              # (sym_col, entry_price, entry_i)
    equity_curve, held = [], set()
    outcomes = {"win": 0, "scratch": 0, "loss": 0, "time": 0}
    grosssum = {"win": 0.0, "scratch": 0.0, "loss": 0.0, "time": 0.0}
    holds = []

    for i in range(200, n):
        for k in range(slots):
            p = pos[k]
            if p is not None:
                col, ep, ei = p
                if i <= ei:
                    continue
                hi, lo, cl = Hv[i, col], Lv[i, col], Cv[i, col]
                exit_px, kind = None, None
                if not np.isnan(hi) and hi >= ep * (1 + TARGET):
                    exit_px, kind = ep * (1 + TARGET), "win"
                elif model == "cutloss" and not np.isnan(lo) and lo <= ep * (1 - stop):
                    exit_px, kind = ep * (1 - stop), "loss"
                elif model in ("scratch", "cutloss") and not np.isnan(cl) and cl >= ep:
                    exit_px, kind = cl, "scratch"
                elif (i - ei) >= HOLD_CAP and not np.isnan(cl):
                    exit_px, kind = cl, "time"
                elif (i - ei) >= HOLD_CAP + 25:
                    # delisted / stuck (all-NaN bars) — force out at last valid price
                    last = Cv[ei:i, col]
                    last = last[~np.isnan(last)]
                    if len(last):
                        exit_px, kind = float(last[-1]), "time"
                if exit_px is not None:
                    gross = exit_px / ep - 1
                    slot_cap[k] *= (1 + gross - cost)
                    outcomes[kind] += 1
                    grosssum[kind] += gross
                    holds.append(i - ei)
                    held.discard(col)
                    pos[k] = None
        # enter flat slots from the day's most-oversold names not already held
        flat = [k for k in range(slots) if pos[k] is None]
        if flat:
            row = Ev[i]
            cand = np.where(row)[0]
            cand = [c for c in cand if c not in held]
            cand.sort(key=lambda c: Rv[i, c])       # most oversold first
            for k in flat:
                if not cand:
                    break
                col = cand.pop(0)
                pos[k] = (col, Cv[i, col], i)
                held.add(col)
        equity_curve.append(sum(slot_cap) + 0.0)
    eq = np.array(equity_curve)
    yrs = (dates[-1] - dates[200]).days / 365.25 or 1
    cagr = (eq[-1] / CAPITAL) ** (1 / yrs) - 1
    peak = np.maximum.accumulate(eq)
    maxdd = float(((eq - peak) / peak).min())
    trades = sum(outcomes.values())
    awin = grosssum["win"] / outcomes["win"] * 100 if outcomes["win"] else 0
    lose_n = outcomes["loss"] + outcomes["time"]
    aloss = (grosssum["loss"] + grosssum["time"]) / lose_n * 100 if lose_n else 0
    return {"cagr": cagr, "maxdd": maxdd, "final": eq[-1], "trades": trades,
            "cycles_yr": trades / yrs, "avg_hold": float(np.mean(holds)) if holds else 0,
            "awin": awin, "aloss": aloss,
            **{f"{k}%": (v / trades * 100 if trades else 0) for k, v in outcomes.items()}}


def run(C, H, L, rsi2, entry_ok, a: date, b: date, label: str, *, model, stop,
        cost, slots):
    idx = [d for d in C.index.date if a <= d <= b]
    if len(idx) < 220:
        return
    mask = (C.index.date >= a) & (C.index.date <= b)
    sub = lambda X: X.loc[mask]
    st = _simulate(sub(C), sub(H), sub(L), sub(rsi2), sub(entry_ok),
                   [d for d in C.index.date if a <= d <= b],
                   model=model, stop=stop, cost=cost, slots=slots)
    print(f"  {label:16} CAGR {st['cagr']*100:6.1f}%  MaxDD {st['maxdd']*100:6.1f}%  "
          f"n={st['trades']:4} ({st['cycles_yr']:.0f}/yr)  hold {st['avg_hold']:.1f}d  "
          f"win {st['win%']:.0f}%/scr {st['scratch%']:.0f}%/time {st['time%']:.0f}%/loss {st['loss%']:.0f}%  "
          f"avgW +{st['awin']:.1f}% avgL {st['aloss']:.1f}%  Rs{st['final']/1e5:.1f}L", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--cost", type=float, default=0.004)      # 0.4% round-trip
    ap.add_argument("--slots", type=int, default=1)
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date(2026, 8, 5)
    print(f"base-hit backtest — Rs{CAPITAL/1e5:.0f}L · +{TARGET*100:.0f}%/trade · hold<={HOLD_CAP}d · "
          f"RSI2<={RSI_THR:.0f} · turnover>=Rs{TURNOVER_FLOOR/1e7:.0f}cr · cost {args.cost*100:.1f}% · "
          f"slots {args.slots}", flush=True)
    C, H, L, V = _load(start, end)
    rsi2, entry_ok = _signals(C, V)
    print(f"  {C.shape[1]} symbols, {C.shape[0]} days\n", flush=True)

    windows = [(date(2016, 1, 1), date(2019, 12, 31), "2016-2019"),
               (date(2020, 1, 1), date(2022, 12, 31), "2020-2022"),
               (date(2023, 1, 1), end, "2023-2026 (OOS)"),
               (start, end, "FULL")]
    for model, stop in [("scratch", 0.0), ("timestop", 0.0), ("cutloss", 0.03)]:
        print(f"\n=== exit model: {model}{f' (stop -{stop*100:.0f}%)' if model=='cutloss' else ''} ===")
        for a, b, lab in windows:
            run(C, H, L, rsi2, entry_ok, a, b, lab, model=model, stop=stop,
                cost=args.cost, slots=args.slots)


if __name__ == "__main__":
    main()
