#!/usr/bin/env python
"""Tour of the nse_universe point-in-time universe oracle.

Everything here is survivorship-free: each query answers "who was in this index,
at what rank, on this date" using the committed data. Run:

    .venv/bin/python examples/explore_universe.py
"""
from __future__ import annotations

from datetime import date

from nse_universe import Universe

# version "v2" = momentum-grade universe (liquidity / listing-age / surveillance
# filters applied); "v1" = raw turnover ranking.
u = Universe(version="v2")

print("Named indices you can query:")
print("  ", ", ".join(u.indices()))
# -> nifty_50, nifty_100, nifty_200, nifty_500, nifty_1000, midcap_150,
#    smallcap_250, largecap_100  (defined in config/indices.yml — add your own)

d = date(2024, 1, 15)

# Point-in-time membership of a named index on a date.
mid = u.members(d, "midcap_150")
print(f"\nNIFTY Midcap 150 members on {d}: {len(mid)} names, e.g. {sorted(mid)[:5]}")

# Rank of a single symbol on a date (by the index's ranking metric).
for sym in ("RATEGAIN", "SANSERA", "RELIANCE"):
    print(f"  rank({sym!r}, {d}) = {u.rank(sym, d)}   in midcap_150? {u.is_member(sym, d, 'midcap_150')}")

# The full ranked table as of a date (rank, symbol, metric_value, as_of_date).
snap = u.universe_at(d)
print(f"\nFull ranked snapshot on {d}: {len(snap)} rows; top 3:")
print(snap.head(3).to_string(index=False))

# The primary backtest-join surface: per-day membership over a window.
mdf = u.members_df(date(2023, 1, 1), date(2023, 3, 31), "nifty_1000")
print(f"\nmembers_df(nifty_1000, Q1-2023): {len(mdf)} (date, symbol, rank) rows")
# A custom rank window (e.g. small/mid ranks 201-600) is just a filter:
band = mdf[(mdf["rank"] >= 201) & (mdf["rank"] <= 600)]
print(f"  -> ranks 201-600 slice: {band['symbol'].nunique()} distinct symbols")

# Walk membership forward in time (freq='M' = one snapshot per month).
print("\nWalking midcap_150 membership monthly through 2024:")
for dt, members in list(u.walk(date(2024, 1, 1), date(2024, 6, 30), "midcap_150", freq="M")):
    print(f"  {dt}: {len(members)} members")

# Sector classification: the rich source is the repo's stock-sectors.json
# (~4,200 symbols, built by tools/build_sectors.py). Load it directly:
import json
sectors = json.load(open("stock-sectors.json"))["symbols"]
print(f"\nsector('RATEGAIN') from stock-sectors.json: {sectors.get('RATEGAIN')}")

# Data coverage.
print(f"\nhealth(): {u.health()}")
