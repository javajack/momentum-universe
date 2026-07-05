#!/usr/bin/env python
"""Fetch India VIX history from Yahoo Finance and cache to parquet.

The Ryner regime gate uses VIX trend as a third signal alongside breadth
and Nifty 50 200-SMA. Rising VIX = fear = distribution likely; calm VIX
= safe to trade even at lower breadth.

Run once at setup, or quarterly to keep current:

    .venv/bin/python tools/build_vix.py

Writes data/benchmarks/india_vix.parquet (~50 KB, ~17 years of daily OHLC).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "benchmarks" / "india_vix.parquet"


def fetch_vix(start: str = "2009-01-01") -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError(
            "yfinance not installed — pip install yfinance"
        ) from e

    ticker = yf.Ticker("^INDIAVIX")
    df = ticker.history(start=start, end=str(date.today()), auto_adjust=False)
    if df.empty:
        raise RuntimeError("yfinance returned empty for ^INDIAVIX — retry later")

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "date"
    df = df.rename(
        columns={"Open": "open", "High": "high", "Low": "low",
                 "Close": "close", "Volume": "volume"}
    )
    df = df[["open", "high", "low", "close"]].dropna(subset=["close"])
    return df


def build() -> None:
    df = fetch_vix()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT, compression="snappy")

    print(f"India VIX → {OUTPUT}")
    print(f"  rows:         {len(df):,}")
    print(f"  date range:   {df.index[0].date()} → {df.index[-1].date()}")
    print(f"  recent VIX:   {df.iloc[-1]['close']:.2f}")
    print(f"  median VIX:   {df['close'].median():.2f}")
    print(f"  max VIX:      {df['close'].max():.2f}")
    print(f"  min VIX:      {df['close'].min():.2f}")


if __name__ == "__main__":
    build()
