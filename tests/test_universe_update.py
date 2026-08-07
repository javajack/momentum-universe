"""Tests for the universe-update pure helpers (integrity flag, pending window)."""
from __future__ import annotations

from datetime import date

from fortress.actions.universe_update import (
    _count_weekdays, _pending_start, _ratio_suspicious,
)


# ---------- corporate-action integrity flag ----------
# NSE circuit limits cap overnight moves near +/-20%; a raw close/prev_close far
# from 1 without an adj_events record is an unrecorded split/bonus.

def test_normal_move_not_suspicious():
    assert _ratio_suspicious(close=105.0, prev_close=100.0) is False   # +5%
    assert _ratio_suspicious(close=88.0, prev_close=100.0) is False    # -12%


def test_split_like_drop_is_suspicious():
    assert _ratio_suspicious(close=10.0, prev_close=100.0) is True     # 1:10 split


def test_bonus_like_jump_is_suspicious():
    assert _ratio_suspicious(close=200.0, prev_close=100.0) is True    # 1:1 bonus


def test_zero_prev_close_never_suspicious():
    assert _ratio_suspicious(close=50.0, prev_close=0.0) is False


def test_custom_thresholds():
    # tighten to +/-10% -> a 15% move now flags
    assert _ratio_suspicious(close=115.0, prev_close=100.0, lo=0.9, hi=1.1) is True


# ---------- pending sync window ----------

def test_pending_start_is_day_after_last_data():
    assert _pending_start(date(2026, 7, 3)) == date(2026, 7, 4)


def test_count_weekdays_excludes_weekends():
    # Mon 2026-08-03 .. Fri 2026-08-07 = 5 weekdays
    assert _count_weekdays(date(2026, 8, 3), date(2026, 8, 7)) == 5
    # include the weekend 08-08/08-09 -> still 5
    assert _count_weekdays(date(2026, 8, 3), date(2026, 8, 9)) == 5


def test_count_weekdays_empty_when_start_after_end():
    assert _count_weekdays(date(2026, 8, 10), date(2026, 8, 3)) == 0
