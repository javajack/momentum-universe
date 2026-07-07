"""Tests for the swing allocation plan builder (menu 11: 3+2 slot split)."""
from __future__ import annotations

from datetime import date

from fortress.actions.swing_allocation import build_swing_allocation


def _cand(ticker, close, stop=None):
    return {
        "ticker": ticker,
        "close": close,
        "suggested_stop": stop if stop is not None else close * 0.92,
        "stop_pct": 8.0,
    }


HB = [_cand("AAA", 500.0), _cand("BBB", 1200.0), _cand("CCC", 90.0), _cand("DDD", 40.0)]
RS = [_cand("EEE", 250.0), _cand("FFF", 75.0), _cand("GGG", 60.0)]


def test_partition_math_500k_five_slots():
    plan = build_swing_allocation(
        hb_candidates=HB, rsi_candidates=RS, capital=500_000,
        hb_slots=3, rsi_slots=2, as_of=date(2026, 7, 7),
    )
    assert plan.per_trade == 100_000
    assert plan.capital == 500_000
    filled = [s for s in plan.slots if s.ticker]
    assert len(filled) == 5
    hb_rows = [s for s in plan.slots if s.strategy == "high_base_52w"]
    rs_rows = [s for s in plan.slots if s.strategy == "rsi2_pullback"]
    assert len(hb_rows) == 3 and len(rs_rows) == 2


def test_quantity_is_floor_of_per_trade_over_close():
    plan = build_swing_allocation(
        hb_candidates=HB, rsi_candidates=RS, capital=500_000,
        hb_slots=3, rsi_slots=2, as_of=date(2026, 7, 7),
    )
    aaa = next(s for s in plan.slots if s.ticker == "AAA")
    assert aaa.quantity == 200          # floor(100000 / 500)
    assert aaa.allocation == 100_000.0  # 200 * 500
    bbb = next(s for s in plan.slots if s.ticker == "BBB")
    assert bbb.quantity == 83           # floor(100000 / 1200)
    assert bbb.allocation == 83 * 1200.0


def test_cash_reserve_is_unallocated_remainder():
    plan = build_swing_allocation(
        hb_candidates=HB, rsi_candidates=RS, capital=500_000,
        hb_slots=3, rsi_slots=2, as_of=date(2026, 7, 7),
    )
    total = sum(s.allocation for s in plan.slots)
    assert plan.total_allocated == total
    assert plan.cash_reserve == 500_000 - total
    assert plan.cash_reserve >= 0


def test_short_candidate_list_leaves_cash_slots():
    plan = build_swing_allocation(
        hb_candidates=HB[:1], rsi_candidates=[], capital=500_000,
        hb_slots=3, rsi_slots=2, as_of=date(2026, 7, 7),
    )
    filled = [s for s in plan.slots if s.ticker]
    empty = [s for s in plan.slots if s.ticker is None]
    assert len(filled) == 1 and len(empty) == 4
    for s in empty:
        assert s.quantity == 0 and s.allocation == 0.0


def test_unaffordable_candidate_is_skipped_for_next():
    """close > per_trade -> qty 0 -> skip it, take the next candidate."""
    pricey = [_cand("XXL", 150_000.0)] + HB
    plan = build_swing_allocation(
        hb_candidates=pricey, rsi_candidates=RS, capital=500_000,
        hb_slots=3, rsi_slots=2, as_of=date(2026, 7, 7),
    )
    tickers = {s.ticker for s in plan.slots}
    assert "XXL" not in tickers
    assert {"AAA", "BBB", "CCC"} <= tickers


def test_rotation_days_attributed_per_strategy():
    plan = build_swing_allocation(
        hb_candidates=HB, rsi_candidates=RS, capital=500_000,
        hb_slots=3, rsi_slots=2, as_of=date(2026, 7, 7),
        hb_time_stop=30, rsi_time_stop=20,
    )
    for s in plan.slots:
        if s.strategy == "high_base_52w":
            assert s.time_stop_days == 30
        else:
            assert s.time_stop_days == 20
