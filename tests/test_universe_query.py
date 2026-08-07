"""Tests for universe-query pure helpers (tier = turnover-rank band)."""
from __future__ import annotations

import pytest

from datetime import date

from fortress.actions.universe_query import (
    TIER_BANDS, _climber_velocity, _is_recent_entrant, _tier,
)


@pytest.mark.parametrize("rank,tier", [
    (1, "LARGE"), (100, "LARGE"),
    (101, "MID"), (250, "MID"),
    (251, "SMALL"), (500, "SMALL"),
    (501, "MICRO"), (1000, "MICRO"),
    (1001, "NANO"), (2000, "NANO"),
    (2001, "—"), (0, "—"),
])
def test_tier_from_rank(rank, tier):
    assert _tier(rank) == tier


def test_tier_bands_are_contiguous_and_cover_1_to_2000():
    # bands stitch together with no gaps/overlaps across 1..2000
    prev_hi = 0
    for _name, lo, hi in TIER_BANDS:
        assert lo == prev_hi + 1
        prev_hi = hi
    assert prev_hi == 2000


# ---------- rank-velocity classifier ----------

def test_velocity_of_existing_climber():
    # was rank 800, now 400 -> climbed 400, not new
    assert _climber_velocity(400, 800, past_max=2000) == (False, 400)


def test_velocity_of_decliner_is_negative():
    assert _climber_velocity(600, 400, past_max=2000) == (False, -200)


def test_new_entrant_implied_minimum_climb():
    # wasn't ranked before; past depth was 2000; now at 500 -> climbed >= 1501
    assert _climber_velocity(500, None, past_max=2000) == (True, 1501)
    # a new entrant deep in the tail implies a smaller climb
    assert _climber_velocity(1900, None, past_max=2000) == (True, 101)


# ---------- IPO (recent-entrant) detection ----------

def test_recent_entrant_is_ipo():
    # first ranked 3 months before as_of, window 12m -> recent listing
    assert _is_recent_entrant(date(2026, 5, 1), date(2026, 8, 5), window_months=12) is True


def test_long_listed_is_not_ipo():
    assert _is_recent_entrant(date(2019, 1, 1), date(2026, 8, 5), window_months=12) is False


def test_never_ranked_is_not_ipo():
    assert _is_recent_entrant(None, date(2026, 8, 5)) is False
