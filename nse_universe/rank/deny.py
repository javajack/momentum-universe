"""Non-equity symbol filter for the v2 ranker.

NSE bhavcopy classifies ETFs, gold/silver funds, debt funds, and index funds
under SctySrs='EQ' — same as equities. So the ingester's SERIES=='EQ' filter
admits all of them into bhav_daily. The v2 momentum-universe ranker, however,
is meant to score *individual equities* only — ETFs and debt funds shouldn't
appear in the rank list at all.

This module supplies the canonical deny-list. Two-layer design:

  1. Suffix patterns (``_NON_EQUITY_SUFFIXES``) catch the regular families:
     *BEES (Goldman/Nippon ETF brand), *ETF (generic), *IETF (index ETF).
     ~75% of known non-equity instruments end with one of these.

  2. Explicit set (``_NON_EQUITY_EXPLICIT``) catches the remaining brand-named
     instruments where the suffix is ambiguous or absent — chiefly gold,
     silver, liquid-fund, and a few index funds with custom names.

Add new symbols to the explicit set when you encounter an ETF that slips
through. To audit which symbols currently pass the filter, run
``recompute_v2_all`` and inspect rows with ``exclude_reason='non_equity'``
in ``universe_v2``.
"""
from __future__ import annotations

# Suffixes that unambiguously denote NSE ETF / fund families. No NSE equity
# ends in any of these across 13 years of bhavcopy history — the suffixes are
# reserved by AMCs for their fund products:
#   BEES/ETF/IETF  — exchange-traded funds
#   ADD            — ETF "additional units" creation tickers (GOLDADD, NIFTYADD…)
#   CASE           — basket / smallcase-style fund products (GOLDCASE, LTGILTCASE…)
# Verified: SELECT DISTINCT symbol … WHERE symbol LIKE '%ADD'/'%CASE' returns
# only fund tickers, never a real company.
_NON_EQUITY_SUFFIXES: tuple[str, ...] = ("BEES", "ETF", "IETF", "ADD", "CASE")

# Brand-named gold, silver, liquid, and index funds where the symbol does
# not end in one of the suffixes above. Curated from 13 years of universe_v2
# history (every symbol whose fortress sector tag is
# DEFENSIVE/COMMODITIES/DEBT/INTERNATIONAL that does not match a suffix).
_NON_EQUITY_EXPLICIT: frozenset[str] = frozenset({
    # Liquid / money-market funds
    "ABSLLIQUID", "AONELIQUID", "ELIQUID", "HDFCLIQUID",
    "LIQUID", "LIQUID1", "LIQUIDADD", "LIQUIDCASE", "LIQUIDPLUS",
    # Gold funds (non-ETF suffix)
    "AXISGOLD", "GOLD1", "GOLDCASE", "GOLDSHARE",
    "GROWWGOLD", "HDFCGOLD", "LICMFGOLD",
    "MOGOLD", "SETFGOLD", "SKYGOLD", "TATAGOLD",
    # Silver funds (non-ETF suffix)
    "AONESILVER", "AXISILVER", "ESILVER", "GROWWSLVR",
    "HDFCSILVER", "MASILVER", "MOSILVER", "NETFSILVER",
    "SBISILVER", "SILVER", "SILVER1", "SILVERADD",
    "SILVERCASE", "TATSILV",
    # Index funds / index ETFs with brand-specific names (no ETF/BEES suffix)
    "HDFCSML250", "MON100", "N100", "SETFNIF50",
    "NIFMID150", "UTINEXT50", "UTISXN50",
})


# Index / ETF / factor-fund products that trade in SERIES='EQ' and slipped the
# suffix rules above (verified NON_EQUITY: absent from StockEdge's equity
# securities master + fund-named). Evicts them from the ranked universe.
_NON_EQUITY_INDEX_ETF: frozenset[str] = frozenset({
    'ECAPINSURE', 'GROWWMETAL', 'GROWWPOWER', 'GROWWRAIL', 'HDFCNIFIT', 'LICNFNHGP', 'LICNMID100', 'MAHKTECH', 'MIDSMALL', 'MOCAPITAL', 'MODEFENCE', 'MOENERGY', 'MOHEALTH', 'MOINFRA', 'MOREALTY', 'MOSMALL250', 'MOTOUR', 'SMALL250', 'SMALLCAP',
    'ABSLNN50ET', 'ABSLPSE', 'AONEGOLD', 'AONENIFTY', 'AONETMMQ50', 'AONETOTAL',
    'AXISNIFTY', 'AXISVALUE', 'AXSENSEX', 'BANKBETA', 'BANKNIFTY1', 'BANKPSU', 'BBETF0432',
    'BBNPPGOLD', 'BFSI', 'BSLNIFTY', 'BSLSENETFG', 'CHOICEGOLD', 'DIVIDEND', 'EBANK',
    'EBANKNIFTY', 'EBBETF0423', 'EBBETF0425', 'EBBETF0430', 'EBBETF0431', 'EBBETF0433',
    'EBIXFOREX', 'EGOLD', 'ELM250', 'EMULTIMQ', 'ENIFTY', 'EQUAL200', 'EQUAL50', 'ESG',
    'EVINDIA', 'GOLD360', 'GOLDBETA', 'GOLDBND', 'GOLDINFRA', 'GOLDIWIN', 'GOLDTELE',
    'GROWWCAPM', 'GROWWDEFNC', 'GROWWEV', 'GROWWLIQID', 'GROWWLOVOL', 'GROWWMC150',
    'GROWWMOM50', 'GROWWN200', 'GROWWNET', 'GROWWNIFTY', 'GROWWNXT50', 'GROWWRLTY',
    'GROWWSC250', 'GSEC10ABSL', 'GSEC10YEAR', 'HDFCBSE500', 'HDFCGROWTH', 'HDFCLOWVOL',
    'HDFCMID150', 'HDFCMOMENT', 'HDFCNEXT50', 'HDFCNIF100', 'HDFCNIFBAN', 'HDFCNIFTY',
    'HDFCPSUBK', 'HDFCPVTBAN', 'HDFCQUAL', 'HDFCSENSEX', 'HDFCVALUE', 'IBMFNIFTY',
    'ICICIB22', 'ICICIGOLD', 'ICICINIFTY', 'ICICINV20', 'IDBIGOLD', 'IDFNIFTYET', 'IGOLD',
    'IIFLNIFTY', 'INIFTY', 'ITBETA', 'IVZINGOLD', 'IVZINNIFTY', 'IWEL', 'KOTAKALPHA',
    'KOTAKGOLD', 'KOTAKNIFTY', 'KOTAKNV20', 'LICNETFGSC', 'LICNETFN50', 'LICNETFSEN',
    'LIQUIDSBI', 'LIQUIDSHRI', 'LOWVOL', 'LOWVOL1', 'MAFANG', 'MAKEINDIA', 'MASPTOP50',
    'MGOLD', 'MID150', 'MIDCAP', 'MIDCAPBETA', 'MNC', 'MOALPHA50', 'MOGSEC', 'MOLOWVOL',
    'MOM100', 'MOM50', 'MOMENTUM', 'MOMENTUM30', 'MOMENTUM50', 'MOMGF', 'MOMIDMTM',
    'MOMOMENTUM', 'MON50EQUAL', 'MONEXT50', 'MONIFTY100', 'MONIFTY500', 'MONQ50', 'MOPSE',
    'MOQUALITY', 'MOSERVICE', 'MOVALUE', 'MSCIINDIA', 'MULTICAP', 'NAVINIFTY', 'NCPSESDL24',
    'NETFCONSUM', 'NETFDIVOPP', 'NETFGILT5Y', 'NETFINCO', 'NETFIT', 'NETFLTGILT',
    'NETFMID150', 'NETFNIF100', 'NETFNV20', 'NETFPHARMA', 'NETFSDL26', 'NEXT50',
    'NEXT50BETA', 'NIFTY1', 'NIFTY100EW', 'NIFTYBETA', 'NIFTYEES', 'NIFTYIWIN',
    'NIFTYQLITY', 'NPBET', 'NV20', 'NV20IWIN', 'PSUBANK', 'PSUBANKICI', 'QGOLDHALF',
    'QNIFTY', 'QUALITY30', 'RELGOLD', 'RELGRNIFTY', 'RELNIFTY', 'RELNV20', 'RETFMID150',
    'SBIBPB', 'SBIETFCON', 'SBIETFIT', 'SBIETFPB', 'SBIETFQLTY', 'SELECTIPO', 'SENSEXBETA',
    'SENSEXIWIN', 'SETF10GILT', 'SETFBANK', 'SETFNIFBK', 'SETFNN50', 'SILVER360',
    'SILVERAG', 'SILVERBETA', 'SILVERBND', 'SNXT50BETA', 'SQSBFSI', 'TOP20', 'UNIONGOLD',
    'UTTAMVALUE', 'VALUEIND'
})


def is_non_equity(symbol: str) -> bool:
    """Return True if ``symbol`` is a known NSE ETF / fund instrument.

    Identifies symbols by either matching one of the standard ETF suffixes
    (*BEES, *ETF, *IETF) or appearing in the curated explicit deny-list.
    """
    s = symbol.strip().upper()
    if s in _NON_EQUITY_EXPLICIT or s in _NON_EQUITY_INDEX_ETF:
        return True
    return s.endswith(_NON_EQUITY_SUFFIXES)
