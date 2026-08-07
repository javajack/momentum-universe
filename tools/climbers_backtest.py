"""Backtest + alpha harness for the accumulation-climbers strategy.

The rank-velocity radar (menu 2 "climbers") surfaces deep-tail turnover-rank
climbers; the ACCUMULATION preset keeps only names still basing (above 200SMA,
low 6m/12m return) — candidate early multibaggers. This harness answers "does
it make money" the honest way, all point-in-time / survivorship-free:

  * backtest one config (band / top-N / cadence) on Rs 10L, equal-weight.
  * walk-forward it across sub-periods (regime robustness, out-of-sample).
  * alpha test — regress the strategy's period returns on an EQUAL-WEIGHT
    return of the SAME rank band, to separate stock-SELECTION alpha from
    small-cap BETA (are you skilled, or just long small-caps in a bull?).

Findings (2016-2026): quarterly rebalance >> monthly (32% vs 9% CAGR); holding
is signal-limited to ~4-5 names (N=8==N=12); it's a regime-dependent satellite
edge, not all-weather. Run:  .venv/bin/python tools/climbers_backtest.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

CAPITAL = 1_000_000
COST_PER_REBAL = 0.005          # ~0.5% round-trip on full turnover
MIN_TURNOVER_CR = 10.0
MIN_CLIMB = 150
LOOKBACK_SNAPS = 6              # ~6-month rank-velocity lookback (monthly snaps)


# ---------------------------------------------------------------------------
# pure metric helper (unit-tested)
# ---------------------------------------------------------------------------

def alpha_beta(strat: List[float], bench: List[float]) -> Tuple[float, float, float]:
    """OLS of strategy period-returns on benchmark period-returns:
    strat = alpha + beta*bench. Returns (alpha_per_period, beta, r2).
    alpha > 0 = selection skill beyond the band's beta."""
    x, y = np.asarray(bench, float), np.asarray(strat, float)
    if len(x) < 3 or x.std() == 0:
        return 0.0, 0.0, 0.0
    beta, alpha = np.polyfit(x, y, 1)
    pred = alpha + beta * x
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return float(alpha), float(beta), float(r2)


# ---------------------------------------------------------------------------
# data (loaded once)
# ---------------------------------------------------------------------------

class _Data:
    def __init__(self, start: date, rank_ceiling: int = 700):
        from nse_universe.core.db import db
        from fortress.nse_data_loader import load_historical_bulk
        with db(read_only=True) as con:
            self.dates = [r[0] for r in con.execute(
                "SELECT DISTINCT as_of_date FROM universe_rank WHERE as_of_date >= ? "
                "ORDER BY as_of_date", [start]).fetchall()]
            rows = con.execute(
                "SELECT as_of_date, symbol, rank, metric_value FROM universe_rank "
                "WHERE as_of_date >= ? AND rank <= ?", [start, rank_ceiling]).fetchall()
        self.snaps: Dict[date, Dict[str, Tuple[int, float]]] = {}
        for d, sym, rk, mv in rows:
            self.snaps.setdefault(d, {})[sym] = (int(rk), float(mv))
        syms = sorted({s for d in self.snaps for s in self.snaps[d]})
        print(f"  {len(self.dates)} snapshots, loading {len(syms)} symbols ...", flush=True)
        prices = load_historical_bulk(start=start - timedelta(days=460),
                                      end=self.dates[-1], symbols=syms)
        self.closes = {s: df["close"] for s, df in prices.items()
                       if df is not None and len(df) > 60}

    def px(self, sym: str, d: date) -> Optional[float]:
        c = self.closes.get(sym)
        if c is None:
            return None
        sub = c[c.index <= pd.Timestamp(d)]
        return float(sub.iloc[-1]) if len(sub) else None

    def metrics(self, sym: str, d: date):
        c = self.closes.get(sym)
        if c is None:
            return None
        sub = c[c.index <= pd.Timestamp(d)]
        n = len(sub)
        if n < 130:
            return None
        p = float(sub.iloc[-1])
        r6 = (p / float(sub.iloc[-127]) - 1) * 100 if n > 126 else None
        r12 = (p / float(sub.iloc[-253]) - 1) * 100 if n > 252 else None
        sma = float(sub.iloc[-200:].mean()) if n >= 200 else None
        return r6, r12, (p > sma if sma else None)

    def first_seen(self, sym: str) -> Optional[date]:
        c = self.closes.get(sym)
        return c.index[0].date() if c is not None and len(c) else None


# ---------------------------------------------------------------------------
# strategy + benchmark
# ---------------------------------------------------------------------------

def _picks(dat: _Data, d: date, d_past: date, lo: int, hi: int, n: int, accumulation: bool
           ) -> List[str]:
    now, past = dat.snaps.get(d, {}), dat.snaps.get(d_past, {})
    pmax = max((r for r, _ in past.values()), default=0)
    out = []
    for s, (rk, mv) in now.items():
        if rk < lo or rk > hi or mv < MIN_TURNOVER_CR * 1e7:
            continue
        rp = past.get(s)
        vel = (rp[0] - rk) if rp else (pmax - rk + 1)
        if vel < MIN_CLIMB:
            continue
        m = dat.metrics(s, d)
        if m is None:
            continue
        r6, r12, above = m
        fs = dat.first_seen(s)
        if fs is not None and fs >= d - timedelta(days=365):        # exclude IPOs
            continue
        if accumulation:
            if above is not True or (r6 is not None and r6 > 30) or (r12 is not None and r12 > 60):
                continue
        out.append((vel, s))
    out.sort(reverse=True)
    return [s for _v, s in out][:n]


def _period_returns(dat: _Data, lo: int, hi: int, n: int, hold: int, accumulation: bool,
                    win_start: date, win_end: date, benchmark: bool = False
                    ) -> Tuple[List[float], List[date]]:
    idx = [i for i, d in enumerate(dat.dates)
           if win_start <= d <= win_end and i >= LOOKBACK_SNAPS and i + hold < len(dat.dates)]
    idx = idx[::hold]
    rets, dates = [], []
    for i in idx:
        d, dn = dat.dates[i], dat.dates[i + hold]
        if benchmark:                                # equal-weight the WHOLE band
            names = [s for s, (rk, mv) in dat.snaps.get(d, {}).items()
                     if lo <= rk <= hi and mv >= MIN_TURNOVER_CR * 1e7]
        else:
            names = _picks(dat, d, dat.dates[i - LOOKBACK_SNAPS], lo, hi, n, accumulation)
        rr = [dat.px(s, dn) / dat.px(s, d) - 1 for s in names
              if dat.px(s, d) and dat.px(s, dn) and dat.px(s, d) > 0]
        rets.append((float(np.mean(rr)) if rr else 0.0) - (0.0 if benchmark else COST_PER_REBAL))
        dates.append(d)
    return rets, idx and [dat.dates[i] for i in idx] or []


def _stats(dat: _Data, rets: List[float], idx_dates: List[date], hold: int) -> dict:
    if len(rets) < 2:
        return {}
    eq = CAPITAL * np.cumprod([1 + r for r in rets])
    yrs = (idx_dates[-1] - idx_dates[0]).days / 365.25 or 1
    cagr = (eq[-1] / CAPITAL) ** (1 / yrs) - 1
    peak = np.maximum.accumulate(eq)
    maxdd = float(((eq - peak) / peak).min())
    r = np.array(rets)
    sharpe = float(r.mean() / r.std() * np.sqrt(len(r) / yrs)) if r.std() else 0.0
    return {"cagr": cagr, "maxdd": maxdd, "sharpe": sharpe, "final": float(eq[-1]),
            "periods": len(rets)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Accumulation-climbers backtest + alpha")
    ap.add_argument("--lo", type=int, default=1)
    ap.add_argument("--hi", type=int, default=500)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--hold", type=int, default=3, help="rebalance in snapshots (1=monthly, 3=quarterly)")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--broad", action="store_true", help="disable the accumulation filter")
    args = ap.parse_args()
    accum = not args.broad
    start = date.fromisoformat(args.start)
    end = date(2026, 8, 5)

    print(f"climbers backtest — ranks {args.lo}-{args.hi} · top {args.top} · "
          f"{'quarterly' if args.hold == 3 else f'{args.hold}-snap'} · "
          f"{'accumulation' if accum else 'broad'} · Rs{CAPITAL/1e5:.0f}L", flush=True)
    dat = _Data(start, rank_ceiling=max(700, args.hi + 200))

    def run(a, b, label):
        rets, dts = _period_returns(dat, args.lo, args.hi, args.top, args.hold, accum, a, b)
        s = _stats(dat, rets, dts, args.hold)
        if s:
            print(f"  {label:20} CAGR {s['cagr']*100:6.1f}%  MaxDD {s['maxdd']*100:6.1f}%  "
                  f"Sharpe {s['sharpe']:5.2f}  n={s['periods']:2}  Rs{s['final']/1e5:6.1f}L", flush=True)
        return rets, dts

    print("\nWALK-FORWARD (regime robustness):")
    run(date(2016, 1, 1), date(2019, 12, 31), "2016-2019")
    run(date(2020, 1, 1), date(2022, 12, 31), "2020-2022")
    run(date(2023, 1, 1), end, "2023-2026 (OOS)")
    strat_rets, strat_dts = run(start, end, "FULL")

    print("\nALPHA TEST (strategy vs EQUAL-WEIGHT of the same band = selection alpha vs beta):")
    bench_rets, bench_dts = _period_returns(dat, args.lo, args.hi, args.top, args.hold,
                                            accum, start, end, benchmark=True)
    bs = _stats(dat, bench_rets, bench_dts, args.hold)
    print(f"  {'BAND equal-weight':20} CAGR {bs['cagr']*100:6.1f}%  MaxDD {bs['maxdd']*100:6.1f}%  "
          f"Sharpe {bs['sharpe']:5.2f}", flush=True)
    m = min(len(strat_rets), len(bench_rets))
    a, beta, r2 = alpha_beta(strat_rets[:m], bench_rets[:m])
    ss = _stats(dat, strat_rets, strat_dts, args.hold)
    per_yr = len(strat_rets) / ((strat_dts[-1] - strat_dts[0]).days / 365.25)
    alpha_ann = (1 + a) ** per_yr - 1
    print(f"  strategy − band CAGR gap: {(ss['cagr']-bs['cagr'])*100:+.1f} pp/yr")
    print(f"  regression: beta {beta:.2f} (market exposure) · alpha {alpha_ann*100:+.1f}%/yr "
          f"(selection skill) · R² {r2:.2f}")
    if alpha_ann > 0.03 and (ss['cagr'] - bs['cagr']) > 0.03:
        print("  -> GENUINE selection alpha beyond small-cap beta.")
    elif beta > 0.7 and alpha_ann <= 0.03:
        print("  -> mostly BETA — the return is being long the band, not stock-picking.")
    else:
        print("  -> mixed: some selection edge over the band, but beta-heavy.")


if __name__ == "__main__":
    main()
