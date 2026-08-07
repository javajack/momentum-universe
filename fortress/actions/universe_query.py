"""Universe query — point-in-time membership / rank / snapshot / coverage.

Thin, pure wrappers over `nse_universe.Universe` so the CLI (and your own code)
can look up who was in an index, at what rank, on any date — survivorship-free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Cap-tiers are TURNOVER-rank bands (not market cap) — mirrors config/indices.yml
# (nifty_100 [1,100] · midcap_150 [101,250] · smallcap_250 [251,500]) plus the
# deep tail MICRO [501,1000] and NANO [1001,2000] now that we rank to depth 2000.
TIER_BANDS: List[Tuple[str, int, int]] = [
    ("LARGE", 1, 100),
    ("MID", 101, 250),
    ("SMALL", 251, 500),
    ("MICRO", 501, 1000),
    ("NANO", 1001, 2000),
]
TIER_NAMES = [t[0] for t in TIER_BANDS]


@dataclass
class UniverseRow:
    rank: int
    symbol: str
    turnover: float       # ₹ median daily turnover (raw)
    tier: str             # LARGE / MID / SMALL / MICRO (turnover band)
    sector: str


def _tier(rank: int) -> str:
    for name, lo, hi in TIER_BANDS:
        if lo <= rank <= hi:
            return name
    return "—"


def _universe(version: str):
    from nse_universe import Universe
    return Universe(version=version)


def _sectors() -> dict:
    try:
        return json.load(open("stock-sectors.json"))["symbols"]
    except Exception:
        return {}


def _sec(sectors: dict, sym: str) -> str:
    s = sectors.get(sym)
    return (s.get("sector") if isinstance(s, dict) else s) or "?"


def _rows(df: pd.DataFrame, sectors: dict) -> List[UniverseRow]:
    out = []
    for _, r in df.iterrows():
        rank = int(r["rank"])
        out.append(UniverseRow(rank=rank, symbol=r["symbol"],
                               turnover=float(r["metric_value"]),
                               tier=_tier(rank), sector=_sec(sectors, r["symbol"])))
    return out


def _asof(snap: pd.DataFrame, requested: date) -> date:
    return snap["as_of_date"].iloc[0] if len(snap) else requested


def search(term: str, as_of: date, version: str = "v2", top: int = 50
           ) -> Tuple[List[UniverseRow], date]:
    """Symbol-substring lookup on `as_of`: every ranked ticker containing `term`,
    with rank / turnover / tier / sector, nearest-rank first."""
    snap = _universe(version).universe_at(as_of)
    t = term.upper().strip()
    hit = snap[snap["symbol"].str.upper().str.contains(t, regex=False)] if t else snap.iloc[0:0]
    return _rows(hit.sort_values("rank").head(top), _sectors()), _asof(snap, as_of)


def screen(as_of: date, version: str = "v2", *, rank_lo: int = 1, rank_hi: int = 1000,
           tier: Optional[str] = None, min_turnover_cr: float = 0.0, top: int = 30
           ) -> Tuple[List[UniverseRow], Dict[str, int], date, int]:
    """Filter the ranked snapshot by rank band / tier / min turnover (₹cr/day).
    Returns (top rows, tier breakdown of all matches, as-of date, total match count)."""
    snap = _universe(version).universe_at(as_of)
    df = snap[(snap["rank"] >= rank_lo) & (snap["rank"] <= rank_hi)]
    if min_turnover_cr > 0:
        df = df[df["metric_value"] >= min_turnover_cr * 1e7]
    rows = _rows(df.sort_values("rank"), _sectors())
    if tier and tier.upper() != "ALL":
        rows = [r for r in rows if r.tier == tier.upper()]
    breakdown = {name: sum(1 for r in rows if r.tier == name) for name in TIER_NAMES}
    return rows[:top], breakdown, _asof(snap, as_of), len(rows)


def tier_members(as_of: date, tier: str, version: str = "v2", top: int = 500
                 ) -> Tuple[List[UniverseRow], date, int]:
    """All constituents of a tier list (LARGE/MID/SMALL/MICRO/NANO) on `as_of`."""
    lo, hi = next(((l, h) for n, l, h in TIER_BANDS if n == tier.upper()), (1, 2000))
    rows, _bd, asof, total = screen(as_of, version, rank_lo=lo, rank_hi=hi, top=top)
    return rows, asof, total


# ---------------------------------------------------------------------------
# rank-velocity radar — fast climbers + new entrants from the deep tail
# ---------------------------------------------------------------------------

@dataclass
class ClimberRow:
    symbol: str
    rank_now: int
    rank_past: Optional[int]   # None = new entrant (was beyond the past depth)
    new_entrant: bool
    velocity: int              # rank improvement; for new entrants the implied minimum
    turnover: float
    tier: str
    sector: str


def _climber_velocity(rank_now: int, rank_past: Optional[int], past_max: int
                      ) -> Tuple[bool, int]:
    """(is_new_entrant, velocity). A new entrant came from beyond the past
    ranking depth, so its climb is at least (past_max - rank_now + 1)."""
    if rank_past is None:
        return True, past_max - rank_now + 1
    return False, rank_past - rank_now


def rank_velocity(
    as_of: date, version: str = "v1", *, lookback_months: int = 6,
    top: int = 25, min_turnover_cr: float = 1.0, min_climb: int = 150,
    min_rank_now: int = 251,
) -> Tuple[List[ClimberRow], date, date, int]:
    """Stocks climbing the turnover rank FASTEST over `lookback_months` — fast
    climbers AND new entrants — from the DEEP TAIL. A research radar for 'sudden
    interest' names OUTSIDE the popular cap lists, NOT a buy list.

    Defaults chosen for the intent: version 'v1' (raw turnover rank to depth
    2000 — v2's quality filter would hide emerging names and shallow the past
    depth); `min_rank_now=251` keeps only names currently BELOW LARGE/MID (i.e.
    still under the radar). Returns (rows sorted by velocity, now as-of, past
    as-of, past ranking depth)."""
    u = _universe(version)
    now_snap = u.universe_at(as_of)
    past_snap = u.universe_at(as_of - timedelta(days=int(lookback_months * 30.4)))
    past_rank = dict(zip(past_snap["symbol"], past_snap["rank"]))
    past_max = int(past_snap["rank"].max()) if len(past_snap) else 0
    sectors = _sectors()

    rows: List[ClimberRow] = []
    for _, r in now_snap.iterrows():
        sym, rn, turn = r["symbol"], int(r["rank"]), float(r["metric_value"])
        if rn < min_rank_now:                        # still under the radar (not LARGE/MID)
            continue
        if turn < min_turnover_cr * 1e7:            # researchable-liquidity floor
            continue
        rp = past_rank.get(sym)
        new, vel = _climber_velocity(rn, int(rp) if rp is not None else None, past_max)
        if vel < min_climb:                          # not climbing fast enough
            continue
        rows.append(ClimberRow(symbol=sym, rank_now=rn,
                               rank_past=(int(rp) if rp is not None else None),
                               new_entrant=new, velocity=vel, turnover=turn,
                               tier=_tier(rn), sector=_sec(sectors, sym)))
    rows.sort(key=lambda x: x.velocity, reverse=True)
    return rows[:top], _asof(now_snap, as_of), _asof(past_snap, as_of), past_max


def list_indices(version: str = "v2") -> List[str]:
    return _universe(version).indices()


def members_on(as_of: date, index: str, version: str = "v2") -> List[str]:
    """Point-in-time members of `index` on `as_of` (sorted)."""
    return sorted(_universe(version).members(as_of, index))


def rank_of(symbol: str, as_of: date, version: str = "v2") -> Optional[int]:
    """Rank of `symbol` on `as_of`, or None if it wasn't ranked that day."""
    return _universe(version).rank(symbol.upper(), as_of)


def snapshot_on(as_of: date, version: str = "v2", top: int = 20) -> pd.DataFrame:
    """Top `top` rows of the full ranked snapshot on `as_of`."""
    return _universe(version).universe_at(as_of).head(top)


def coverage(version: str = "v2") -> dict:
    """Data-coverage summary (date span, symbols, rank snapshots, ...)."""
    return _universe(version).health()
