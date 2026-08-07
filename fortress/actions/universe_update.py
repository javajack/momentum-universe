"""Universe update — bridge to the vendored nse_universe pipeline.

Three levels, all credential-free (NSE public data + yfinance, no broker):
  * offline rebuild (default): rebuild the DuckDB from committed parquet +
    derived snapshots. No network.
  * fetch: pull the pending NSE bhavcopy days (last-synced -> today), ingest,
    recompute ranks, rebuild — so prices extend to today.
  * fetch + full: additionally refresh CORPORATE ACTIONS (yfinance splits/
    dividends) and detect RENAMES / delistings, so the data stays *correct*,
    not just current. Without this a split in the new days has no adjustment
    factor and pollutes every adjusted-price consumer.

Every run ends with a data-integrity check that flags unrecorded corporate
actions (a raw overnight move beyond NSE circuit limits with no adj_events
record = a split/bonus the adjustment layer hasn't captured yet).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple


@dataclass
class IntegritySuspect:
    symbol: str
    date: str
    ratio: float          # close / prev_close on the flagged day


@dataclass
class UpdateResult:
    fetched: bool
    full: bool = False
    window: Optional[Tuple[str, str]] = None     # (start, end) synced, if fetched
    pending_days: int = 0                         # weekdays in the window
    steps: Dict[str, str] = field(default_factory=dict)
    symbols: int = 0
    rows: int = 0
    integrity_suspects: List[IntegritySuspect] = field(default_factory=list)


# ---------------------------------------------------------------------------
# pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def _ratio_suspicious(close: float, prev_close: float,
                      lo: float = 0.6, hi: float = 1.6) -> bool:
    """True if an overnight raw move is too large to be a normal trading day
    (NSE circuits cap ~+/-20%) — i.e. an unrecorded split/bonus candidate."""
    if not prev_close or prev_close <= 0:
        return False
    ratio = close / prev_close
    return ratio < lo or ratio > hi


def _pending_start(last_data_date: date) -> date:
    """First day to sync = the day after the newest bar already stored."""
    return last_data_date + timedelta(days=1)


def _count_weekdays(start: date, end: date) -> int:
    """Mon-Fri count in [start, end] (rough pending-trading-day estimate)."""
    if start > end:
        return 0
    n, d = 0, start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


# ---------------------------------------------------------------------------
# data-integrity check (reads the DuckDB; no network)
# ---------------------------------------------------------------------------

def check_data_integrity(
    lookback_days: int = 120, lo: float = 0.6, hi: float = 1.6, limit: int = 50,
) -> List[IntegritySuspect]:
    """Flag recent raw overnight moves beyond circuit limits that have NO
    adj_events record within +/-7 days — unrecorded splits/bonuses that would
    corrupt the adjusted series. Empty list = adjustments are in step."""
    from nse_universe.core.db import db

    with db(read_only=True) as con:
        last = con.execute("SELECT MAX(date) FROM bhav_daily").fetchone()[0]
        if last is None:
            return []
        since = last - timedelta(days=lookback_days)
        rows = con.execute(
            """
            SELECT b.symbol, b.date, b.close / b.prev_close AS ratio
              FROM bhav_daily b
             WHERE b.date >= ?
               AND b.prev_close > 0
               AND (b.close < ? * b.prev_close OR b.close > ? * b.prev_close)
               AND NOT EXISTS (
                     SELECT 1 FROM adj_events a
                      WHERE a.symbol = b.symbol
                        AND a.event_date BETWEEN b.date - INTERVAL 7 DAY
                                             AND b.date + INTERVAL 7 DAY)
             ORDER BY b.date DESC, b.symbol
             LIMIT ?
            """,
            [since, lo, hi, limit],
        ).fetchall()
    return [IntegritySuspect(symbol=r[0], date=str(r[1]), ratio=round(float(r[2]), 3))
            for r in rows]


def pending_window(version: str = "v2", end: Optional[date] = None
                   ) -> Tuple[Optional[date], date, int]:
    """(start, end, weekday_count) that a fetch would sync — for a preview
    before the (possibly slow) network call. start None => nothing pending."""
    from nse_universe import Universe

    end = end or date.today()
    last = Universe(version=version).health()["last_date"]
    start = _pending_start(last)
    if start > end:
        return None, end, 0
    return start, end, _count_weekdays(start, end)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def update_universe(
    *,
    fetch: bool = False,
    full: bool = False,
    start: Optional[date] = None,
    end: Optional[date] = None,
    version: str = "v2",
) -> UpdateResult:
    """Refresh the local universe database.

    fetch=False → offline rebuild from committed data (no network).
    fetch=True  → sync the pending bhavcopy window (last-synced+1 -> today by
                  default), ingest, recompute ranks, rebuild.
    full=True   → also refresh corporate actions (yfinance) and detect renames
                  before recomputing ranks — keeps adjustments/universe correct.
    """
    from nse_universe.core.db import rebuild_from_parquet
    from nse_universe.core.export import import_all_if_missing

    res = UpdateResult(fetched=fetch, full=full and fetch)

    if fetch:
        from nse_universe.fetch.bhav import sync_range
        from nse_universe.ingest.bhav import ingest_all_pending
        from nse_universe.rank.monthly import recompute_all

        end = end or date.today()
        if start is None:
            start = _pending_start(version_last_date(version))
        res.window = (str(start), str(end))
        res.pending_days = _count_weekdays(start, end)

        if start <= end:
            synced = sync_range(start, end)
            res.steps["sync"] = f"{_sum(synced)} days"
        else:
            res.steps["sync"] = "up to date (nothing pending)"
        res.steps["ingest"] = f"{_sum(ingest_all_pending())} rows"

        if full:
            from nse_universe.actions.fetch import refresh_actions
            from nse_universe.core import state as state_mod
            r = refresh_actions()
            state_mod.mark_actions_refreshed()
            res.steps["actions"] = (f"ok={r.ok} splits={r.splits} "
                                    f"dividends={r.dividends} errors={r.errors}")
            res.steps["renames"] = _detect_renames()

        recompute_all()
        res.steps["rank"] = "recomputed"

    stats = rebuild_from_parquet()
    import_all_if_missing()
    res.steps["rebuild"] = "ok"
    res.symbols = int(stats.get("symbols", 0))
    res.rows = int(stats.get("rows", 0))

    res.integrity_suspects = check_data_integrity()
    res.steps["integrity"] = (f"{len(res.integrity_suspects)} unrecorded-action "
                              f"suspect(s)" if res.integrity_suspects else "clean")
    return res


def version_last_date(version: str = "v2") -> date:
    from nse_universe import Universe
    return Universe(version=version).health()["last_date"]


def _sum(x) -> int:
    if isinstance(x, dict):
        return int(sum(x.values()))
    try:
        return int(x)
    except (TypeError, ValueError):
        return 0


def _detect_renames() -> str:
    """Run the ISIN-continuity rename scan and apply new proposals additively."""
    try:
        from tools.build_renames import analyze, apply_entries
        result = analyze()
        added = apply_entries(result.get("new_proposals", []))
        return f"{len(result.get('unmapped', []))} unmapped, {added} new applied"
    except Exception as e:  # never let rename detection break a sync
        return f"skipped ({type(e).__name__})"
