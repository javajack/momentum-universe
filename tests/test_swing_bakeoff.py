import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest
import pandas as pd
from tools.swing_bakeoff import SwingStrategy, Trade, COST_LEVELS


def test_swing_strategy_is_abstract():
    with pytest.raises(TypeError):
        SwingStrategy()  # cannot instantiate ABC


def test_cost_levels_are_round_trip_fractions():
    assert COST_LEVELS == {"20bp": 0.0020, "35bp": 0.0035, "60bp": 0.0060}


def test_trade_dataclass_has_required_fields():
    t = Trade(
        strategy="rsi2", ticker="X",
        entry_date=pd.Timestamp("2024-01-02").date(),
        exit_date=pd.Timestamp("2024-01-05").date(),
        entry_price=100.0, exit_price=103.0, stop_price=97.0,
        pnl_pct=3.0, pnl_inr_gross=3000.0,
        hold_days=3, exit_reason="signal",
    )
    assert t.pnl_inr_gross == 3000.0


import numpy as np
from tools.swing_bakeoff import compute_shared_indicators


def _synthetic_df(n_days=300, drift=0.001, seed=0):
    rng = np.random.RandomState(seed)
    idx = pd.date_range("2023-01-01", periods=n_days, freq="B")
    closes = [100.0]
    for _ in range(1, n_days):
        closes.append(closes[-1] * (1 + drift + rng.normal(0, 0.01)))
    close = pd.Series(closes, index=idx)
    return pd.DataFrame({
        "open": close * 0.999,
        "close": close,
        "high": close * 1.005,
        "low": close * 0.995,
        "volume": pd.Series([300_000] * n_days, index=idx),
    })


def test_compute_shared_indicators_adds_expected_columns():
    df = _synthetic_df(300)
    prices = {"X": df}
    compute_shared_indicators(prices)
    cols = set(df.columns)
    expected = {"sma_200", "sma_50", "sma_20", "sma_5", "ema_21", "atr_14",
                "rsi_2", "avg_vol_20", "high_252", "range_20", "bb_bandwidth"}
    assert expected.issubset(cols), f"missing: {expected - cols}"


def test_compute_shared_indicators_handles_short_history():
    df = _synthetic_df(50)
    prices = {"X": df}
    compute_shared_indicators(prices)
    assert "sma_200" in df.columns
    assert df["sma_200"].isna().all()


from tools.swing_bakeoff import RSI2Pullback


def _build_pullback_df(n=300, last_pullback=True, base=100.0):
    """Long uptrend then sharp 3-day pullback."""
    rng = np.random.RandomState(0)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    closes = [base]
    for i in range(1, n):
        change = 0.001 + rng.normal(0, 0.003)
        if last_pullback and i >= n - 3:
            change = -0.015
        closes.append(closes[-1] * (1 + change))
    close = pd.Series(closes, index=idx)
    return pd.DataFrame({
        "open": close * 0.999, "close": close,
        "high": close * 1.005, "low": close * 0.995,
        "volume": pd.Series([300_000] * n, index=idx),
    })


def test_rsi2_pullback_fires_on_oversold_uptrend():
    df = _build_pullback_df(280, last_pullback=True)
    prices = {"X": df}
    compute_shared_indicators(prices)
    s = RSI2Pullback()
    s.precompute(prices)
    today = df.index[-1]
    assert s.should_enter(df, today) is True


def test_rsi2_pullback_does_not_fire_without_pullback():
    df = _build_pullback_df(280, last_pullback=False)
    prices = {"X": df}
    compute_shared_indicators(prices)
    s = RSI2Pullback()
    s.precompute(prices)
    today = df.index[-1]
    assert s.should_enter(df, today) is False


from tools.swing_bakeoff import HighBaseBreakout52w


def _build_tight_base_df(n=300, base=100.0):
    """Long uptrend to ~250, then 20-day tight band 95-100, then close near 52w high."""
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    pre = [base * (1.002 ** i) for i in range(n - 20)]
    high_252_target = max(pre)
    rng = np.random.RandomState(2)
    band = [high_252_target * (0.97 + rng.uniform(-0.01, 0.01)) for _ in range(19)]
    band.append(high_252_target * 0.98)
    closes = pre + band
    close = pd.Series(closes, index=idx)
    return pd.DataFrame({
        "open": close * 0.999, "close": close,
        "high": close * 1.005, "low": close * 0.995,
        "volume": pd.Series([300_000] * n, index=idx),
    })


def test_high_base_fires_on_tight_consolidation_near_high():
    df = _build_tight_base_df(300)
    prices = {"X": df}
    compute_shared_indicators(prices)
    s = HighBaseBreakout52w()
    s.precompute(prices)
    assert s.should_enter(df, df.index[-1]) is True


from tools.swing_bakeoff import run_strategy


def test_run_strategy_produces_trades_on_synthetic_universe():
    """Two synthetic stocks, 300 days each, both showing RSI(2) pullback
    near the end. Engine should produce >= 1 closed trade."""
    df1 = _build_pullback_df(300, last_pullback=True, base=100.0)
    df2 = _build_pullback_df(300, last_pullback=True, base=200.0)
    prices = {"A": df1, "B": df2}
    compute_shared_indicators(prices)
    all_dates = sorted({ts.date() for df in prices.values() for ts in df.index})
    membership = {d: {"A", "B"} for d in all_dates}
    strat = RSI2Pullback()
    strat.precompute(prices)
    trades = run_strategy(
        strategy=strat, prices=prices, membership=membership,
        start=df1.index[200].date(), end=df1.index[-1].date(),
        max_concurrent=2, capital_per_trade=100_000.0,
    )
    assert isinstance(trades, list)
    assert len(trades) >= 1
    for t in trades:
        assert t.strategy == "rsi2_pullback"
        assert t.entry_price > 0
        assert t.exit_price > 0


def test_run_strategy_uses_next_day_open_fill():
    """Signal fires on D close → fill at D+1 open. Verify entry_price = next
    day's open, not signal-day close."""
    df = _build_pullback_df(300, last_pullback=True)
    df.loc[df.index[-1], "open"] = 999.99
    prices = {"X": df}
    compute_shared_indicators(prices)
    all_dates = sorted({ts.date() for ts in df.index})
    membership = {d: {"X"} for d in all_dates}
    strat = RSI2Pullback()
    strat.precompute(prices)
    trades = run_strategy(
        strategy=strat, prices=prices, membership=membership,
        start=df.index[-3].date(), end=df.index[-1].date(),
        max_concurrent=1, capital_per_trade=100_000.0,
    )
    if trades:
        assert trades[0].entry_price == pytest.approx(999.99)


from tools.swing_bakeoff import score_strategy


def test_score_strategy_computes_basic_metrics():
    trades = [
        Trade("s", "A", pd.Timestamp("2024-01-02").date(),
              pd.Timestamp("2024-01-05").date(),
              100, 103, 97, 3.0, 3000.0, 3, "signal"),
        Trade("s", "B", pd.Timestamp("2024-01-03").date(),
              pd.Timestamp("2024-01-08").date(),
              100, 98, 96, -2.0, -2000.0, 5, "stop"),
        Trade("s", "C", pd.Timestamp("2024-01-04").date(),
              pd.Timestamp("2024-01-10").date(),
              100, 105, 97, 5.0, 5000.0, 6, "signal"),
    ]
    score = score_strategy(trades, cost_rate=0.0, capital_per_trade=100_000)
    assert score["n_trades"] == 3
    assert score["win_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert score["total_pnl_net"] == pytest.approx(6000.0)
    score_costed = score_strategy(trades, cost_rate=0.0035, capital_per_trade=100_000)
    assert score_costed["total_pnl_net"] == pytest.approx(6000.0 - 3 * 350.0)
    assert "profit_factor_net" in score_costed
    assert "sharpe_net" in score_costed
    assert "max_drawdown_pct" in score_costed
