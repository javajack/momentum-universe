"""Tests for the MCP server layer (pure parts: serialization, snapshot math,
tool registration). Live-data tools are exercised end-to-end manually, not here."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from fortress.mcp_server import _snapshot_from_df, _to_jsonable, build_server


# ---------- serialization ----------

@dataclass
class _Inner:
    d: date
    x: float


@dataclass
class _Outer:
    name: str
    when: date
    items: list


def test_to_jsonable_handles_dataclasses_and_dates():
    obj = _Outer(name="plan", when=date(2026, 7, 7),
                 items=[_Inner(d=date(2026, 1, 2), x=1.5)])
    out = _to_jsonable(obj)
    assert out == {
        "name": "plan", "when": "2026-07-07",
        "items": [{"d": "2026-01-02", "x": 1.5}],
    }


def test_to_jsonable_handles_numpy_scalars():
    out = _to_jsonable({"a": np.float64(2.5), "b": np.int64(7)})
    assert out == {"a": 2.5, "b": 7}


# ---------- snapshot math ----------

def _make_df(n=300, daily=0.001, start=100.0):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = pd.Series(start * (1 + daily) ** np.arange(n), index=idx)
    df = pd.DataFrame({
        "open": close.values, "high": close.values * 1.01,
        "low": close.values * 0.99, "close": close.values,
        "volume": np.full(n, 50_000.0),
    }, index=idx)
    return df


def test_snapshot_uptrend_metrics():
    df = _make_df()
    s = _snapshot_from_df("TEST", df)
    assert s["symbol"] == "TEST"
    assert s["close"] > 0
    assert s["above_200sma"] is True
    assert s["ret_1m_pct"] > 0 and s["ret_12m_pct"] > 0
    # steady uptrend: today's close IS the 52w high
    assert s["prox_52w_high"] == 1.0
    assert s["atr14"] > 0
    assert s["avg_turnover_20d"] > 0


def test_snapshot_insufficient_history_flags_missing_metrics():
    df = _make_df(n=40)
    s = _snapshot_from_df("YOUNG", df)
    assert s["ret_12m_pct"] is None
    assert s["above_200sma"] is None


# ---------- tool registration ----------

def test_server_exposes_expected_tools():
    server = build_server()
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert tools == {
        "swing_allocation_plan", "momentum_scan", "emerging_scan",
        "momentum_allocation", "market_state", "universe_lookup", "stock_snapshot",
    }
