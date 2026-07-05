#!/usr/bin/env python
"""Build your OWN strategy on the universe data — a minimal, complete template.

This is a self-contained monthly-rebalanced top-N momentum backtest that uses
only two ingredients:
  1. `nse_universe.Universe` for point-in-time membership + ranks (who's in the
     [201,600] small/mid band on each date — survivorship-free), and
  2. the repo's price loader for the actual OHLCV series.

Swap in your own signal (the `score()` function) to test any idea. There is no
look-ahead: every decision at date `t` uses only prices up to `t`. Run:

    .venv/bin/python examples/custom_strategy.py
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from nse_universe import Universe
from fortress.nse_data_loader import load_historical_for_backtest

START, END = date(2018, 1, 1), date(2026, 6, 30)
RANK_LO, RANK_HI, TOP_N, LOOKBACK = 201, 600, 15, 126  # 126 trading days ~ 6 months


def score(close_upto_t: pd.Series) -> float | None:
    """YOUR signal. Here: 6-month price momentum. Return None to skip a name."""
    if len(close_upto_t) < LOOKBACK:
        return None
    return close_upto_t.iloc[-1] / close_upto_t.iloc[-LOOKBACK] - 1.0


def main() -> None:
    u = Universe(version="v2")
    # Prices for the union of [201,600] members over the window (split-adjusted).
    prices = load_historical_for_backtest(
        start=START, end=END, rank_range=(RANK_LO, RANK_HI), version="v2"
    )
    closes = {s: df["close"] for s, df in prices.items() if len(df)}

    rebal_dates = pd.bdate_range(START, END, freq="BME")  # business month-ends
    equity, curve = 1.0, []

    for t, t_next in zip(rebal_dates[:-1], rebal_dates[1:]):
        # 1. point-in-time members ranked 201-600 as of t (survivorship-free)
        snap = u.universe_at(t.date())
        members = set(snap[(snap["rank"] >= RANK_LO) & (snap["rank"] <= RANK_HI)]["symbol"])

        # 2. score each member on data available at t; pick the top N
        scored = {}
        for s in members:
            c = closes.get(s)
            if c is None:
                continue
            val = score(c[c.index <= t])
            if val is not None:
                scored[s] = val
        picks = [s for s, _ in sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]]

        # 3. equal-weight forward return t -> t_next
        rets = []
        for s in picks:
            c = closes[s]
            a, b = c[c.index <= t], c[c.index <= t_next]
            if len(a) and len(b) and a.iloc[-1] > 0:
                rets.append(b.iloc[-1] / a.iloc[-1] - 1.0)
        equity *= 1.0 + (sum(rets) / len(rets) if rets else 0.0)
        curve.append((t_next.date(), equity))

    yrs = (curve[-1][0] - curve[0][0]).days / 365.25
    cagr = equity ** (1 / yrs) - 1
    print(f"Custom top-{TOP_N} 6m-momentum, ranks [{RANK_LO},{RANK_HI}], monthly rebalance")
    print(f"  {START} -> {END}:  total {equity - 1:+.0%}   CAGR {cagr:+.1%}   ({yrs:.1f} years)")
    print("  (edit score() to test your own signal — this is just a template)")


if __name__ == "__main__":
    main()
