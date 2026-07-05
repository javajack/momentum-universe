"""Universe update — bridge to the vendored nse_universe pipeline.

Two modes, both credential-free (NSE public data, no broker):
  * offline rebuild (default): rebuild the DuckDB from the committed parquet +
    derived snapshots. Always safe, no network.
  * fetch: pull the latest NSE bhavcopy (sync -> ingest -> rank) so the data
    extends to today, then rebuild.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Optional


@dataclass
class UpdateResult:
    fetched: bool
    steps: Dict[str, str] = field(default_factory=dict)   # step -> short status
    symbols: int = 0
    rows: int = 0


def update_universe(
    *,
    fetch: bool = False,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> UpdateResult:
    """Refresh the local universe database.

    fetch=False → offline rebuild from committed data (no network).
    fetch=True  → sync latest bhavcopy from NSE (public), ingest, recompute
                  ranks, then rebuild. `start`/`end` bound the fetch window
                  (default: from the last synced date to today).
    """
    from nse_universe.core.db import rebuild_from_parquet
    from nse_universe.core.export import import_all_if_missing

    res = UpdateResult(fetched=fetch)

    if fetch:
        from nse_universe.fetch.bhav import sync_range
        from nse_universe.ingest.bhav import ingest_all_pending
        from nse_universe.rank.monthly import recompute_all

        end = end or date.today()
        synced = sync_range(start, end) if start else sync_range(end, end)
        res.steps["sync"] = f"{sum(synced.values()) if isinstance(synced, dict) else synced} files"
        ingested = ingest_all_pending()
        res.steps["ingest"] = f"{sum(ingested.values()) if isinstance(ingested, dict) else ingested} rows"
        recompute_all()
        res.steps["rank"] = "recomputed"

    stats = rebuild_from_parquet()
    import_all_if_missing()
    res.steps["rebuild"] = "ok"
    res.symbols = int(stats.get("symbols", 0))
    res.rows = int(stats.get("rows", 0))
    return res
