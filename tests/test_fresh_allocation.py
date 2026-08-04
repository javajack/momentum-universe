"""Tests for the fresh-allocation planner (stateless: enter cash -> breakup).

Pure logic only (overlap, ADV flag, regime hint, plan assembly). The live
legs (plan_rebalance / swing_allocation_plan / market_state) are integration-
tested via a real run, not here.
"""
from __future__ import annotations

from datetime import date

from fortress.actions.fresh_allocation import (
    FreshAllocRow, _adv_warn, _overlap, _regime_hint, build_fresh_allocation,
)


# ---------- overlap ----------

def test_overlap_finds_common_tickers():
    assert _overlap(["AAA", "BBB", "CCC"], ["CCC", "DDD"]) == {"CCC"}


def test_overlap_empty_when_disjoint():
    assert _overlap(["AAA"], ["BBB"]) == set()


# ---------- ADV fill-risk flag ----------

def test_adv_warn_below_threshold_ok():
    pct, warn = _adv_warn(value=100_000, adv=2_000_000, max_frac=0.10)
    assert pct == 0.05 and warn is False


def test_adv_warn_above_threshold_flags():
    pct, warn = _adv_warn(value=100_000, adv=500_000, max_frac=0.10)
    assert pct == 0.20 and warn is True


def test_adv_warn_missing_adv_no_flag():
    pct, warn = _adv_warn(value=100_000, adv=0.0, max_frac=0.10)
    assert pct is None and warn is False


# ---------- regime hint ----------

def test_regime_hint_defensive_tilts_to_swing():
    assert "swing" in _regime_hint("defensive").lower()


def test_regime_hint_bullish_favours_momentum():
    h = _regime_hint("bullish").lower()
    assert "momentum" in h


# ---------- plan assembly (overlap + adv flags) ----------

def _mrow(sym, value):
    return FreshAllocRow(sleeve="momentum", symbol=sym, detail="10%",
                         quantity=int(value // 100), value=value)


def _srow(sym, value):
    return FreshAllocRow(sleeve="swing", symbol=sym, detail="high_base_52w",
                         quantity=int(value // 100), value=value,
                         stop=90.0, rotation_days=30)


def test_build_flags_overlap_across_sleeves():
    mom = [_mrow("AAA", 100_000), _mrow("SHARED", 100_000)]
    sw = [_srow("SHARED", 100_000), _srow("EEE", 100_000)]
    plan = build_fresh_allocation(
        momentum_rows=mom, swing_rows=sw, adv_map={},
        as_of=date(2026, 8, 5), regime="bullish",
        momentum_capital=1_000_000, swing_capital=500_000,
        momentum_cash=0.0, swing_cash=57.0,
    )
    assert plan.overlaps == ["SHARED"]
    flagged = [r for r in plan.momentum_rows + plan.swing_rows if r.overlap]
    assert {r.symbol for r in flagged} == {"SHARED"}


def test_build_sets_adv_flags_from_map():
    mom = [_mrow("AAA", 100_000)]           # value 100k
    sw = [_srow("EEE", 100_000)]
    adv = {"AAA": 500_000, "EEE": 5_000_000}  # AAA is 20% of ADV -> warn
    plan = build_fresh_allocation(
        momentum_rows=mom, swing_rows=sw, adv_map=adv,
        as_of=date(2026, 8, 5), regime="normal",
        momentum_capital=100_000, swing_capital=100_000,
        momentum_cash=0.0, swing_cash=0.0,
    )
    aaa = next(r for r in plan.momentum_rows if r.symbol == "AAA")
    eee = next(r for r in plan.swing_rows if r.symbol == "EEE")
    assert aaa.adv_warn is True and abs(aaa.adv_pct - 0.20) < 1e-9
    assert eee.adv_warn is False


def test_build_carries_defensive_rows_passthrough():
    defensive = [FreshAllocRow(sleeve="defensive", symbol="GOLDBEES", detail="12%",
                               quantity=0, value=120_000)]
    plan = build_fresh_allocation(
        momentum_rows=[_mrow("AAA", 100_000)], swing_rows=[], adv_map={},
        defensive_rows=defensive, as_of=date(2026, 8, 5), regime="bullish",
        momentum_capital=1_000_000, swing_capital=0.0,
        momentum_cash=0.0, swing_cash=0.0,
    )
    assert [r.symbol for r in plan.defensive_rows] == ["GOLDBEES"]
    # defensive ETFs are NOT momentum buy-rows
    assert "GOLDBEES" not in {r.symbol for r in plan.momentum_rows}


def test_build_carries_cash_and_regime_hint():
    plan = build_fresh_allocation(
        momentum_rows=[_mrow("AAA", 100_000)], swing_rows=[], adv_map={},
        as_of=date(2026, 8, 5), regime="defensive",
        momentum_capital=100_000, swing_capital=0.0,
        momentum_cash=123.0, swing_cash=0.0,
    )
    assert plan.momentum_cash == 123.0
    assert "swing" in plan.regime_hint.lower()
