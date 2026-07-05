"""Tests for the emerging_momentum strategy + score function."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from fortress.strategy.emerging_momentum import (
    EmergingMomentumStrategy, _compute_emerging_score,
)
from fortress.strategy.registry import StrategyRegistry


# ---------- registration ----------

def test_strategy_registered():
    # Canonical name + short alias both resolve
    assert StrategyRegistry.is_registered("emerging_momentum")
    assert StrategyRegistry.is_registered("vanguard")  # alias


def test_strategy_metadata():
    s = EmergingMomentumStrategy()
    assert s.name == "emerging_momentum"
    assert "velocity" in s.description.lower() or "momentum" in s.description.lower()


def test_alias_resolves_to_canonical():
    s_alias = StrategyRegistry.get("vanguard")
    s_canonical = StrategyRegistry.get("emerging_momentum")
    assert s_alias.name == s_canonical.name == "emerging_momentum"


# ---------- score function helpers ----------

DEFAULT_EM_CFG = {
    "weight_1m": 0.20, "weight_3m": 0.30, "weight_6m": 0.30, "weight_12m": 0.20,
    "skip_recent_days_12m": 5,
    "lookback_1m": 21, "lookback_3m": 63, "lookback_6m": 126, "lookback_12m": 252,
    "lookback_volatility": 126,
    "min_volatility_floor": 0.10,
    "breakout_proximity_min": 0.95,
    "breakout_max_days_since_high": 10,
    "breakout_score_multiplier": 1.20,
    "volume_ratio_20_50_min": 1.5,
    "volume_score_multiplier": 1.10,
}


def _make_prices(n_days: int, daily_pct: float, start: float = 100.0,
                 start_date: datetime = datetime(2023, 1, 2)) -> pd.Series:
    """Build a price series with constant daily compound return."""
    dates = pd.date_range(start=start_date, periods=n_days, freq="B")
    rets = np.full(n_days - 1, daily_pct)
    closes = [start]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    return pd.Series(closes, index=dates)


def _make_volumes(n_days: int, base: float = 1000.0,
                  surge_last_20: float = 1.0,
                  start_date: datetime = datetime(2023, 1, 2)) -> pd.Series:
    """Build a volume series, with optional surge in last 20 days."""
    dates = pd.date_range(start=start_date, periods=n_days, freq="B")
    vols = np.full(n_days, base)
    if surge_last_20 != 1.0 and n_days >= 20:
        vols[-20:] = base * surge_last_20
    return pd.Series(vols, index=dates)


# ---------- score correctness ----------

def test_score_insufficient_history_returns_none():
    prices = _make_prices(100, 0.0)
    volumes = _make_volumes(100)
    assert _compute_emerging_score(prices, volumes, DEFAULT_EM_CFG) is None


def test_score_steady_uptrend_positive():
    """A clean +0.1% daily uptrend should produce positive emerging score."""
    prices = _make_prices(300, 0.001)
    volumes = _make_volumes(300)
    result = _compute_emerging_score(prices, volumes, DEFAULT_EM_CFG)
    assert result is not None
    assert result["score"] > 0
    assert result["ret_12m"] > 0


def test_score_flat_trend_near_zero():
    """A flat price series should produce near-zero score (small float noise)."""
    prices = _make_prices(300, 0.0)
    volumes = _make_volumes(300)
    result = _compute_emerging_score(prices, volumes, DEFAULT_EM_CFG)
    assert result is not None
    assert abs(result["score"]) < 0.01


def test_breakout_boost_fires_for_fresh_high():
    """Stock at fresh 52w high should get the breakout multiplier."""
    prices = _make_prices(300, 0.001)
    volumes = _make_volumes(300)
    r = _compute_emerging_score(prices, volumes, DEFAULT_EM_CFG)
    assert r is not None
    # In a steady uptrend, today's price IS the 52w high → days_since_high = 0
    assert r["days_since_52w_high"] == 0
    assert r["high_52w_proximity"] >= 0.95
    assert r["breakout_boost"] is True


def test_breakout_boost_does_not_fire_for_stale_high():
    """If 52w high was 60 days ago, breakout boost should NOT apply."""
    # Build series that rises for 200 days then drifts down for 60
    rise = _make_prices(240, 0.002, start=100.0).tolist()
    last = rise[-1]
    dates = pd.date_range(start=datetime(2023, 1, 2), periods=300, freq="B")
    flat_drift = [last * (0.999 ** i) for i in range(1, 61)]
    closes = rise + flat_drift
    prices = pd.Series(closes, index=dates)
    volumes = _make_volumes(300)
    r = _compute_emerging_score(prices, volumes, DEFAULT_EM_CFG)
    assert r is not None
    # 52w high was at index 239; today is index 299 → days_since_high > 10
    assert r["days_since_52w_high"] > 10
    assert r["breakout_boost"] is False


def test_volume_boost_fires_on_surge_with_breakout():
    """Volume surge above the threshold with price near 50d high → volume boost."""
    prices = _make_prices(300, 0.001)
    volumes = _make_volumes(300, base=1000.0, surge_last_20=3.0)  # 2x surge
    r = _compute_emerging_score(prices, volumes, DEFAULT_EM_CFG)
    assert r is not None
    assert r["volume_surge"] >= 1.5
    assert r["volume_boost"] is True


def test_volume_boost_does_not_fire_without_surge():
    prices = _make_prices(300, 0.001)
    volumes = _make_volumes(300, base=1000.0, surge_last_20=1.0)  # no surge
    r = _compute_emerging_score(prices, volumes, DEFAULT_EM_CFG)
    assert r is not None
    assert r["volume_surge"] < 1.5
    assert r["volume_boost"] is False


def test_score_with_both_boosts_is_higher_than_without():
    """Same return, one with vol surge, one without — boosted should win."""
    prices = _make_prices(300, 0.001)
    volumes_quiet = _make_volumes(300, base=1000.0, surge_last_20=1.0)
    volumes_surge = _make_volumes(300, base=1000.0, surge_last_20=3.0)

    r_quiet = _compute_emerging_score(prices, volumes_quiet, DEFAULT_EM_CFG)
    r_surge = _compute_emerging_score(prices, volumes_surge, DEFAULT_EM_CFG)

    assert r_surge["score"] > r_quiet["score"]
    # Boost is 1.10x — score ratio should match
    assert r_surge["score"] / r_quiet["score"] == pytest.approx(1.10, abs=0.001)


def test_score_responds_to_weight_changes():
    """Front-loaded weights (more 1m) should boost a sharply-recent winner."""
    # Stock that's been flat for 12m, then surged 30% in last month
    dates = pd.date_range(start=datetime(2023, 1, 2), periods=280, freq="B")
    closes = [100.0] * 259 + list(np.linspace(100.0, 130.0, 21))
    prices = pd.Series(closes, index=dates)
    volumes = _make_volumes(280)

    cfg_default = dict(DEFAULT_EM_CFG)
    cfg_front_loaded = dict(DEFAULT_EM_CFG)
    cfg_front_loaded.update({
        "weight_1m": 0.50, "weight_3m": 0.30, "weight_6m": 0.15, "weight_12m": 0.05,
    })

    r_default = _compute_emerging_score(prices, volumes, cfg_default)
    r_front = _compute_emerging_score(prices, volumes, cfg_front_loaded)

    assert r_front["score"] > r_default["score"]


def test_zero_volatility_floor_prevents_div_by_zero():
    """A perfectly flat stock should not div-zero — vol floor kicks in."""
    prices = _make_prices(300, 0.0)
    volumes = _make_volumes(300)
    r = _compute_emerging_score(prices, volumes, DEFAULT_EM_CFG)
    assert r is not None
    assert r["volatility"] >= DEFAULT_EM_CFG["min_volatility_floor"]


# ---------- config integration ----------

def test_strategy_picks_up_config_values():
    from fortress.config import Config, EmergingMomentumConfig
    cfg = Config(emerging_momentum=EmergingMomentumConfig(
        weight_1m=0.40, weight_3m=0.30, weight_6m=0.20, weight_12m=0.10,
        breakout_score_multiplier=1.50,
    ))
    strat = EmergingMomentumStrategy(cfg)
    em_cfg = strat._get_emerging_config_values()
    assert em_cfg["weight_1m"] == 0.40
    assert em_cfg["breakout_score_multiplier"] == 1.50


# ---------- time-decay exit ----------

def test_time_decay_exit_fires_after_max_days_without_gain():
    """Holding a position 45+ days with <10% gain should trigger time-decay exit."""
    strat = EmergingMomentumStrategy()
    sig = strat.check_exit_triggers(
        ticker="STAGNANT",
        entry_price=100.0,
        current_price=104.0,   # 4% gain — below 10% target
        peak_price=104.0,
        days_held=46,           # past the 45-day threshold
        stock_score=None,
        nms_percentile=60.0,
    )
    assert sig.should_exit is True
    assert sig.exit_type == "time_decay"
    assert "Time decay" in sig.reason


def test_time_decay_exit_holds_below_threshold_days():
    """Same scenario but only 30 days held — should NOT exit (hold)."""
    strat = EmergingMomentumStrategy()
    sig = strat.check_exit_triggers(
        ticker="EARLY",
        entry_price=100.0,
        current_price=104.0,
        peak_price=104.0,
        days_held=30,
        stock_score=None,
        nms_percentile=60.0,
    )
    assert sig.should_exit is False


def test_time_decay_exit_holds_when_gain_above_target():
    """46d held with 15% gain — exceeds target, should NOT trigger time-decay."""
    strat = EmergingMomentumStrategy()
    sig = strat.check_exit_triggers(
        ticker="WINNER",
        entry_price=100.0,
        current_price=115.0,
        peak_price=115.0,
        days_held=46,
        stock_score=None,
        nms_percentile=60.0,
    )
    # Could still trigger trailing stop or other exits, but not time-decay
    assert sig.exit_type != "time_decay"


def test_hard_stop_still_takes_precedence_over_time_decay():
    """Hard stop fires before time-decay check."""
    strat = EmergingMomentumStrategy()
    sig = strat.check_exit_triggers(
        ticker="LOSER",
        entry_price=100.0,
        current_price=75.0,    # -25% loss — well below hard stop threshold
        peak_price=100.0,
        days_held=46,
        stock_score=None,
        nms_percentile=60.0,
    )
    assert sig.should_exit is True
    # Hard stop (-15% threshold from dual_momentum) should fire
    assert sig.exit_type == "stop_loss"
