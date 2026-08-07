"""Tests for universe-query pure helpers (tier = turnover-rank band)."""
from __future__ import annotations

import pytest

from fortress.actions.universe_query import TIER_BANDS, _tier


@pytest.mark.parametrize("rank,tier", [
    (1, "LARGE"), (100, "LARGE"),
    (101, "MID"), (250, "MID"),
    (251, "SMALL"), (500, "SMALL"),
    (501, "MICRO"), (1000, "MICRO"),
    (1001, "—"), (0, "—"),
])
def test_tier_from_rank(rank, tier):
    assert _tier(rank) == tier


def test_tier_bands_are_contiguous_and_cover_1_to_1000():
    # bands stitch together with no gaps/overlaps across 1..1000
    prev_hi = 0
    for _name, lo, hi in TIER_BANDS:
        assert lo == prev_hi + 1
        prev_hi = hi
    assert prev_hi == 1000
