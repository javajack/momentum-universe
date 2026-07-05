"""Tests for the Ryner Teo / RSI(2) pullback scanner."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.ryner_pullback_scan import DEFAULTS, atr, evaluate_one, rsi, scan


# ---- RSI ----

def test_rsi_falls_on_downtrend():
    """A monotonic downtrend should drive RSI(2) toward 0."""
    n = 50
    prices = pd.Series([100 - i * 1.0 for i in range(n)],
                        index=pd.date_range("2024-01-01", periods=n, freq="B"))
    r = rsi(prices, period=2).iloc[-1]
    assert r < 5.0, f"expected ~0 RSI on monotonic downtrend, got {r}"


def test_rsi_rises_on_uptrend():
    """A monotonic uptrend should drive RSI(2) toward 100."""
    n = 50
    prices = pd.Series([100 + i * 1.0 for i in range(n)],
                        index=pd.date_range("2024-01-01", periods=n, freq="B"))
    r = rsi(prices, period=2).iloc[-1]
    assert r > 95.0


def test_rsi_handles_flat():
    """Flat prices → RSI is undefined (NaN). Avoid div by zero."""
    n = 50
    prices = pd.Series([100.0] * n,
                        index=pd.date_range("2024-01-01", periods=n, freq="B"))
    r = rsi(prices, period=2).iloc[-1]
    assert pd.isna(r) or r == 50  # either is acceptable


# ---- ATR ----

def test_atr_positive_for_volatile_series():
    n = 50
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series(100 + np.random.RandomState(42).normal(0, 2, n), index=idx)
    high = close + 1.5
    low = close - 1.5
    a = atr(high, low, close, period=14).iloc[-1]
    assert a > 0


# ---- evaluate_one ----

def _build_df(n_days: int, daily_drift: float = 0.001, last_pullback: bool = True,
               base: float = 100.0, vol: float = 1.0) -> pd.DataFrame:
    """Build a synthetic OHLCV df: long uptrend then small pullback near end."""
    rng = np.random.RandomState(0)
    idx = pd.date_range("2023-01-01", periods=n_days, freq="B")
    closes = [base]
    for i in range(1, n_days):
        change = daily_drift + rng.normal(0, 0.003)
        # End with a sharp 3-day pullback if requested
        if last_pullback and i >= n_days - 3:
            change = -0.015
        closes.append(closes[-1] * (1 + change))
    close = pd.Series(closes, index=idx)
    high = close * (1 + 0.005)
    low = close * (1 - 0.005)
    volume = pd.Series([300_000] * n_days, index=idx)
    return pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume})


def test_evaluate_one_returns_none_for_too_short_history():
    df = _build_df(100, last_pullback=False)
    assert evaluate_one(df, DEFAULTS) is None


def test_evaluate_one_returns_none_when_below_200sma():
    """A clear downtrend stock should not qualify."""
    n = 280
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    close = pd.Series([100 * (0.999 ** i) for i in range(n)], index=idx)
    df = pd.DataFrame({
        "close": close,
        "high": close * 1.005, "low": close * 0.995,
        "volume": pd.Series([300_000] * n, index=idx),
    })
    assert evaluate_one(df, DEFAULTS) is None


def test_evaluate_one_returns_none_when_no_pullback():
    """Uptrend with current close ABOVE 5-SMA → no pullback signal."""
    n = 280
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    close = pd.Series([100 * (1.001 ** i) for i in range(n)], index=idx)
    df = pd.DataFrame({
        "close": close,
        "high": close * 1.005, "low": close * 0.995,
        "volume": pd.Series([300_000] * n, index=idx),
    })
    assert evaluate_one(df, DEFAULTS) is None


def test_evaluate_one_qualifies_on_pullback_in_uptrend():
    """Long uptrend + sharp 3-day pullback → should fire."""
    df = _build_df(280, daily_drift=0.001, last_pullback=True)
    result = evaluate_one(df, DEFAULTS)
    assert result is not None
    assert result["rsi2"] <= DEFAULTS["rsi_entry_max"]
    assert result["above_200sma_pct"] > 0
    assert result["below_5sma_pct"] < 0
    assert result["suggested_stop"] < result["close"]


def test_evaluate_one_respects_min_price():
    """Penny names should be filtered out by min_price."""
    df = _build_df(280, base=10.0)
    cfg = dict(DEFAULTS)
    cfg["min_price"] = 50.0
    assert evaluate_one(df, cfg) is None


def test_evaluate_one_respects_volume_floor():
    """Thin-volume names should be filtered out."""
    df = _build_df(280)
    df["volume"] = 10_000  # well below 200k floor
    assert evaluate_one(df, DEFAULTS) is None


def test_evaluate_one_skips_extended_names():
    """Stocks > 30% above 200-SMA are too extended for a pullback play."""
    n = 280
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    # Big run-up then a tiny pullback — current price still ~50% above 200-SMA
    rise = [100 * (1.005 ** i) for i in range(n - 3)]
    pullback = [rise[-1] * (0.99 ** i) for i in range(1, 4)]
    close = pd.Series(rise + pullback, index=idx)
    df = pd.DataFrame({
        "close": close,
        "high": close * 1.005, "low": close * 0.995,
        "volume": pd.Series([300_000] * n, index=idx),
    })
    cfg = dict(DEFAULTS)
    cfg["max_dist_above_200sma"] = 0.30
    # SMA-200 will be well below current → distance > 30%
    assert evaluate_one(df, cfg) is None


# ---- scan ----

def test_scan_sorts_by_rsi_ascending():
    """Returned list should be deepest-oversold first."""
    df1 = _build_df(280, last_pullback=True)
    df2 = _build_df(280, last_pullback=True, base=110.0)
    # Doctor df2's last 3 closes to be deeper pullback
    df2_closes = df2["close"].values.copy()
    df2_closes[-3:] = df2_closes[-4] * np.array([0.97, 0.95, 0.93])
    df2 = df2.assign(close=df2_closes,
                      high=df2_closes * 1.005, low=df2_closes * 0.995)
    prices = {"A": df1, "B": df2}
    out = scan(["A", "B"], prices, DEFAULTS)
    if len(out) >= 2:
        # Deeper pullback should have lower RSI
        assert out[0]["rsi2"] <= out[1]["rsi2"]


def test_scan_skips_missing_data():
    """Missing prices for a ticker should not crash the scan."""
    prices = {"A": None, "B": _build_df(280)}
    out = scan(["A", "B"], prices, DEFAULTS)
    assert all(c["ticker"] in {"B"} for c in out)
