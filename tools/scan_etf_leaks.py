"""Catch newly-listed ETFs that leaked into the ranked universe — the robust,
authoritative way, via StockEdge's EntityCode.

NSE lists sector/thematic ETFs in SERIES='EQ', so new ones keep slipping past
deny.py's static list (MODEFENCE, MOENERGY, GROWWRAIL...). Name patterns are
unsafe (they false-positive on BBOX/GOLDIAM). StockEdge's search, however, tags
each result: se_security = a real equity, se_etf / se_index / se_mutualfund = a
fund. This sweeps the CURRENTLY-ranked symbols that aren't confirmed equities and
prints any StockEdge marks as a fund — paste the list into deny.py's
_NON_EQUITY_INDEX_ETF and recompute. Run quarterly after a data fetch.

Run:  .venv/bin/python tools/scan_etf_leaks.py
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, "/home/rakesh/work/momentum-universe")
from tools.build_sector_map import _token, _get, OUT

FUND_CODES = {"se_etf", "se_index", "se_mutualfund"}


def main():
    tok = _token()
    sm = json.loads(OUT.read_text())["symbols"]
    from nse_universe.core.db import db
    from nse_universe.rank.deny import is_non_equity
    with db(read_only=True) as con:
        d1 = con.execute("SELECT max(as_of_date) FROM universe_rank").fetchone()[0]
        d2 = con.execute("SELECT max(as_of_date) FROM universe_v2").fetchone()[0]
        cur = set(r[0] for r in con.execute("SELECT symbol FROM universe_rank WHERE as_of_date=?", [d1]).fetchall())
        cur |= set(r[0] for r in con.execute("SELECT symbol FROM universe_v2 WHERE as_of_date=? AND passes", [d2]).fetchall())
    # suspects: currently ranked, not already denied, not a confirmed StockEdge equity
    suspects = sorted(s for s in cur if not is_non_equity(s)
                      and sm.get(s, {}).get("source") not in ("stockedge", "rename", "researched"))
    print(f"scanning {len(suspects)} currently-ranked suspects for fund EntityCode ...", flush=True)

    funds = []
    for s in suspects:
        try:
            r = _get(f"/Api/UniversalSearchApi/GetQuickSearchResult?searchTerm={s}", tok)
        except Exception:
            continue
        codes = [x.get("EntityCode") for x in r.get("Data", [])]
        if codes and "se_security" not in codes and any(c in FUND_CODES for c in codes):
            funds.append((s, r["Data"][0].get("Name", "")[:40]))
        time.sleep(0.12)

    print(f"\n{len(funds)} leaked funds found (add to deny._NON_EQUITY_INDEX_ETF, then recompute):")
    for s, n in funds:
        print(f"  {s:14} {n}")
    if funds:
        print("\ndeny-list literal:")
        print("    " + ", ".join(repr(s) for s, _ in funds))


if __name__ == "__main__":
    main()
