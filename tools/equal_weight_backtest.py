"""Standalone equal-weight top-N momentum backtest — a SAFE comparison sleeve.

The production dual_momentum builds a momentum-weighted portfolio with sector
caps, regime scaling and a defensive overlay (menu 9 / the engine backtest).
This asks a simpler question, in isolation and WITHOUT touching that engine:
if you just equal-weight the top-N momentum names in the same v2 [201,600]
universe, rebalanced monthly, what do the returns / drawdowns look like?

  * universe : v2 rank band [201,600] (same as dual_momentum), point-in-time
  * signal   : 6-month (126-bar) price return = the dominant momentum factor
  * portfolio: top-N by momentum, EQUAL weight, reset to equal each rebalance
  * cadence  : monthly (v2 rebalance dates)
  * capital  : Rs 20L (config.portfolio.initial_capital) · cost 0.3%/side turnover
  * N        : swept 5 / 8 / 10 / 12 (<=12 per the ask)

Reports CAGR / MaxDD / Sharpe / Calmar / final + walk-forward across regimes, so
you can compare against the sector-capped dual_momentum (~16% CAGR / -31% DD).
Run:  .venv/bin/python tools/equal_weight_backtest.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

CAPITAL = 2_000_000
COST = 0.003                 # 0.3% per side on turnover
RANK_LO, RANK_HI = 201, 600
MOM_BARS = 126               # 6-month momentum
NS = [5, 8, 10, 12]


def _load(start: date, end: date):
    """Monthly v2 [201,600] membership + prices for every ever-member."""
    from nse_universe.core.db import db
    from fortress.nse_data_loader import load_historical_bulk
    with db(read_only=True) as con:
        rows = con.execute(
            "SELECT as_of_date, symbol FROM universe_v2 "
            "WHERE as_of_date >= ? AND passes AND rank BETWEEN ? AND ?",
            [start - timedelta(days=40), RANK_LO, RANK_HI]).fetchall()
    members: dict[date, list[str]] = {}
    for d, s in rows:
        members.setdefault(d, []).append(s)
    rebal_dates = sorted(members)
    syms = sorted({s for v in members.values() for s in v})
    print(f"  {len(rebal_dates)} monthly rebalances · {len(syms)} distinct members", flush=True)
    prices = load_historical_bulk(start=start - timedelta(days=400), end=end, symbols=syms)
    close = {s: df["close"] for s, df in prices.items() if df is not None and len(df) > MOM_BARS}
    C = pd.DataFrame(close).sort_index()
    return C, members, rebal_dates


def _simulate(C, members, rebal_dates, N, start, end):
    dates = [d for d in C.index.date if start <= d <= end]
    if len(dates) < 60:
        return {}
    di = {d: i for i, d in enumerate(C.index.date)}
    ret = C.pct_change()
    # map each trading day to its active monthly membership (most recent as_of <= day)
    rd = [d for d in rebal_dates if d <= end]
    weights: dict[str, float] = {}
    cash_frac = 1.0
    eq, cur_val = [], float(CAPITAL)
    last_rebal = None
    for d in dates:
        i = di[d]
        # daily mark-to-market of held weights
        if weights:
            r = sum(w * (ret[s].iloc[i] if s in ret.columns and not np.isnan(ret[s].iloc[i]) else 0.0)
                    for s, w in weights.items())
            cur_val *= (1 + r)
        # rebalance if a new monthly as_of has taken effect
        active = max((x for x in rd if x <= d), default=None)
        if active is not None and active != last_rebal:
            last_rebal = active
            elig = members.get(active, [])
            mom = {}
            for s in elig:
                if s not in C.columns:
                    continue
                px = C[s]
                sub = px[px.index <= pd.Timestamp(d)].dropna()
                if len(sub) > MOM_BARS:
                    mom[s] = sub.iloc[-1] / sub.iloc[-1 - MOM_BARS] - 1
            top = [s for s, _ in sorted(mom.items(), key=lambda kv: kv[1], reverse=True)[:N]]
            if top:
                new_w = {s: 1.0 / len(top) for s in top}
                turnover = sum(abs(new_w.get(s, 0) - weights.get(s, 0))
                               for s in set(new_w) | set(weights))
                cur_val *= (1 - COST * turnover)
                weights = new_w
        eq.append(cur_val)
    eq = np.array(eq)
    yrs = (dates[-1] - dates[0]).days / 365.25 or 1
    cagr = (eq[-1] / CAPITAL) ** (1 / yrs) - 1
    peak = np.maximum.accumulate(eq)
    maxdd = float(((eq - peak) / peak).min())
    r = pd.Series(eq).pct_change().dropna()
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() else 0.0
    return {"cagr": cagr, "maxdd": maxdd, "sharpe": sharpe,
            "calmar": cagr / abs(maxdd) if maxdd else 0, "final": eq[-1]}


def line(tag, s):
    if not s:
        print(f"  {tag:16} (insufficient data)"); return
    print(f"  {tag:16} CAGR {s['cagr']*100:6.2f}%  MaxDD {s['maxdd']*100:6.1f}%  "
          f"Sharpe {s['sharpe']:5.2f}  Calmar {s['calmar']:4.2f}  Rs{s['final']/1e5:.1f}L", flush=True)


def main():
    start, end = date(2016, 1, 1), date(2026, 8, 5)
    print(f"equal-weight top-N momentum — v2[{RANK_LO},{RANK_HI}] · Rs{CAPITAL/1e5:.0f}L · "
          f"monthly · 6m-mom · cost {COST*100:.1f}%/side\n", flush=True)
    C, members, rebal = _load(start, end)

    print("FULL 2016-2026:")
    for N in NS:
        line(f"N={N}", _simulate(C, members, rebal, N, start, end))
    print("\nWALK-FORWARD (N=10):")
    for a, b, lab in [(date(2016,1,1),date(2019,12,31),"2016-2019"),
                      (date(2020,1,1),date(2022,12,31),"2020-2022"),
                      (date(2023,1,1),end,"2023-2026 OOS")]:
        line(lab, _simulate(C, members, rebal, 10, a, b))
    print("\nreference: sector-capped dual_momentum full-period ~16.0% CAGR / -31.7% MaxDD")


if __name__ == "__main__":
    main()
