"""Tests for the emerging-momentum scan (pure logic: metrics, filters, score)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from fortress.actions.emerging_scan import (
    DEFAULT_THRESHOLDS, EmergingRow, _passes_early_filters, _price_metrics,
    _climb_signal,
)


def _df(n=300, daily=0.0, start=100.0, vol_base=1e6):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = pd.Series(start * (1 + daily) ** np.arange(n), index=idx)
    return pd.DataFrame({
        "open": close.values, "high": close.values * 1.01,
        "low": close.values * 0.99, "close": close.values,
        "volume": np.full(n, vol_base),
    }, index=idx)


# ---------- price metrics ----------

def test_metrics_uptrend():
    m = _price_metrics(_df(300, 0.002))   # ~+0.2%/day compounding
    assert m is not None
    assert m["above200"] is True
    assert m["prox"] == 1.0                # steady uptrend -> at its own 52w high
    assert m["r3"] > 0 and m["r12"] > 0
    assert m["turnover"] > 0


def test_metrics_insufficient_history_returns_none():
    assert _price_metrics(_df(150)) is None   # < 200 bars


# ---------- early-momentum filter (exclude already-run + junk) ----------

def _base_metrics(**over):
    m = {"above200": True, "prox": 0.95, "r3": 0.20, "r6": 0.30, "r12": 0.40,
         "vol": 0.45, "turnover": 1e8, "dist200_pct": 30.0}
    m.update(over)
    return m


def test_filter_accepts_clean_early_mover():
    assert _passes_early_filters(_base_metrics(), DEFAULT_THRESHOLDS) is True


def test_filter_rejects_already_parabolic():
    # 12m return above the early cap -> not "emerging"
    assert _passes_early_filters(_base_metrics(r12=1.50), DEFAULT_THRESHOLDS) is False


def test_filter_rejects_below_200sma():
    assert _passes_early_filters(_base_metrics(above200=False), DEFAULT_THRESHOLDS) is False


def test_filter_rejects_far_below_high():
    assert _passes_early_filters(_base_metrics(prox=0.70), DEFAULT_THRESHOLDS) is False


def test_filter_rejects_illiquid():
    assert _passes_early_filters(_base_metrics(turnover=1e6), DEFAULT_THRESHOLDS) is False


def test_filter_rejects_data_artifact():
    # implausible level vs its own 200d mean = bad-tick artifact
    assert _passes_early_filters(_base_metrics(dist200_pct=4000.0), DEFAULT_THRESHOLDS) is False


def test_filter_rejects_falling_recent_leg():
    assert _passes_early_filters(_base_metrics(r3=-0.05), DEFAULT_THRESHOLDS) is False


# ---------- climb signal (liquidity-rank trajectory) ----------

def test_climb_prefers_bigger_rank_improvement():
    # rank fell (improved) from 800 -> 300 over 2y = strong climb
    strong = _climb_signal(rank_now=300, rank_y1=500, rank_y2=800)
    weak = _climb_signal(rank_now=300, rank_y1=330, rank_y2=360)
    assert strong > weak


def test_climb_handles_new_entrant():
    # no 2y rank (recent listing) but climbing over 1y -> positive climb
    c = _climb_signal(rank_now=300, rank_y1=500, rank_y2=None)
    assert c > 0


def test_climb_brand_new_entrant_neutral():
    c = _climb_signal(rank_now=400, rank_y1=None, rank_y2=None)
    assert c > 0   # a fresh entrant still counts as emerging, but modestly
