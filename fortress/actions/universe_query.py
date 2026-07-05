"""Universe query — point-in-time membership / rank / snapshot / coverage.

Thin, pure wrappers over `nse_universe.Universe` so the CLI (and your own code)
can look up who was in an index, at what rank, on any date — survivorship-free.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

import pandas as pd


def _universe(version: str):
    from nse_universe import Universe
    return Universe(version=version)


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
