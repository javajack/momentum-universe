"""Tests for the regime_switched_momentum strategy (hard regime switch)."""
from __future__ import annotations

from datetime import datetime

import pytest

from fortress.indicators import MarketRegime, RegimeResult
from fortress.strategy.adaptive_dual_momentum import AdaptiveDualMomentumStrategy
from fortress.strategy.emerging_momentum import EmergingMomentumStrategy
from fortress.strategy.regime_switched_momentum import RegimeSwitchedMomentumStrategy
from fortress.strategy.registry import StrategyRegistry


def _make_regime(regime: MarketRegime) -> RegimeResult:
    return RegimeResult(
        regime=regime,
        nifty_52w_position=0.8 if regime == MarketRegime.BULLISH else 0.4,
        vix_level=14.0,
        nifty_3m_return=0.05,
        equity_weight=1.0,
        gold_weight=0.0,
        cash_weight=0.0,
        primary_regime=regime,
        vix_upgrade=False,
        return_upgrade=False,
    )


# ---------- registration ----------

def test_strategy_registered():
    assert StrategyRegistry.is_registered("regime_switched_momentum")
    assert StrategyRegistry.is_registered("switcher")  # alias


def test_strategy_metadata():
    s = RegimeSwitchedMomentumStrategy()
    assert s.name == "regime_switched_momentum"
    assert "regime" in s.description.lower()


# ---------- scorer selection ----------

@pytest.mark.parametrize(
    "regime",
    [MarketRegime.BULLISH, MarketRegime.NORMAL],
)
def test_risk_on_regimes_select_emerging_scorer(regime):
    s = RegimeSwitchedMomentumStrategy()
    s.set_regime(_make_regime(regime))
    assert s._active_scorer() == "emerging"


@pytest.mark.parametrize(
    "regime",
    [MarketRegime.CAUTION, MarketRegime.DEFENSIVE],
)
def test_risk_off_regimes_select_dual_scorer(regime):
    s = RegimeSwitchedMomentumStrategy()
    s.set_regime(_make_regime(regime))
    assert s._active_scorer() == "dual"


def test_no_regime_falls_back_to_dual():
    s = RegimeSwitchedMomentumStrategy()
    assert s._active_scorer() == "dual"


# ---------- rank_stocks delegation ----------

def test_rank_stocks_delegates_to_emerging_in_bull(monkeypatch):
    s = RegimeSwitchedMomentumStrategy()
    s.set_regime(_make_regime(MarketRegime.BULLISH))
    monkeypatch.setattr(
        EmergingMomentumStrategy, "rank_stocks", lambda self, **kw: ["EMERGING"]
    )
    monkeypatch.setattr(
        AdaptiveDualMomentumStrategy, "rank_stocks", lambda self, **kw: ["DUAL"]
    )
    result = s.rank_stocks(
        as_of_date=datetime(2024, 1, 15), universe=None, market_data=None
    )
    assert result == ["EMERGING"]


def test_rank_stocks_delegates_to_dual_in_caution(monkeypatch):
    s = RegimeSwitchedMomentumStrategy()
    s.set_regime(_make_regime(MarketRegime.CAUTION))
    monkeypatch.setattr(
        EmergingMomentumStrategy, "rank_stocks", lambda self, **kw: ["EMERGING"]
    )
    monkeypatch.setattr(
        AdaptiveDualMomentumStrategy, "rank_stocks", lambda self, **kw: ["DUAL"]
    )
    result = s.rank_stocks(
        as_of_date=datetime(2024, 1, 15), universe=None, market_data=None
    )
    assert result == ["DUAL"]


# ---------- exit delegation (real behavior, no mocks) ----------

def test_time_decay_exit_applies_in_bull_regime():
    """Under BULLISH the emerging exit ladder governs → time-decay fires."""
    s = RegimeSwitchedMomentumStrategy()
    s.set_regime(_make_regime(MarketRegime.BULLISH))
    sig = s.check_exit_triggers(
        ticker="STAGNANT",
        entry_price=100.0,
        current_price=104.0,  # 4% gain, below 10% target
        peak_price=104.0,
        days_held=46,  # past emerging's 45-day threshold
        stock_score=None,
        nms_percentile=60.0,
    )
    assert sig.should_exit is True
    assert sig.exit_type == "time_decay"


def test_time_decay_exit_absent_in_caution_regime():
    """Under CAUTION the dual exit ladder governs → no time-decay rule."""
    s = RegimeSwitchedMomentumStrategy()
    s.set_regime(_make_regime(MarketRegime.CAUTION))
    sig = s.check_exit_triggers(
        ticker="STAGNANT",
        entry_price=100.0,
        current_price=104.0,
        peak_price=104.0,
        days_held=46,
        stock_score=None,
        nms_percentile=60.0,
    )
    assert sig.exit_type != "time_decay"


def test_hard_stop_fires_in_every_regime():
    """The shared hard stop must fire regardless of the active scorer."""
    for regime in (MarketRegime.BULLISH, MarketRegime.CAUTION):
        s = RegimeSwitchedMomentumStrategy()
        s.set_regime(_make_regime(regime))
        sig = s.check_exit_triggers(
            ticker="LOSER",
            entry_price=100.0,
            current_price=75.0,
            peak_price=100.0,
            days_held=46,
            stock_score=None,
            nms_percentile=60.0,
        )
        assert sig.should_exit is True
        assert sig.exit_type == "stop_loss"
