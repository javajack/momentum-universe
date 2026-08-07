"""Non-equity deny-list guard (ETFs/funds that trade in NSE SERIES='EQ').

These instruments reach bhav_daily alongside equities and must be kept out of
BOTH the v1 turnover rank (rank/monthly) and the v2 quality universe (rank/v2),
so the ranked universe is individual equities only. Regression guard for the
symbols that were observed leaking into v1's top-2000 before the fix.
"""
from __future__ import annotations

import pytest

from nse_universe.rank.deny import is_non_equity


@pytest.mark.parametrize("sym", [
    "LIQUIDPLUS", "LIQUIDBEES", "LIQUIDCASE", "LIQUID1", "LIQUIDADD",   # liquid funds
    "GOLDBEES", "SETFGOLD", "GOLD1", "HDFCGOLD", "TATAGOLD",            # gold funds
    "SILVERBEES", "SILVER", "HDFCSILVER", "SBISILVER", "SILVERCASE",   # silver funds
    "NIFTYBEES", "BANKBEES", "JUNIORBEES", "ITBEES", "PSUBNKBEES",     # index/sector ETFs
    "HDFCSML250", "SETFNIF50", "MON100", "N100",                       # brand index funds
])
def test_known_non_equity_is_denied(sym):
    assert is_non_equity(sym) is True


@pytest.mark.parametrize("sym", [
    # real individual equities that must NOT be filtered (incl. lookalikes)
    "HDFCBANK", "RELIANCE", "IRB", "ANGELONE", "APEX", "MUFIN",
    "KSB", "CEIGALL", "DREDGECORP", "HINDOILEXP", "INFOBEAN",
])
def test_real_equities_pass(sym):
    assert is_non_equity(sym) is False
