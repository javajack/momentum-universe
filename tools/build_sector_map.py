"""Independent sector/industry mapping layer for our NSE universe, from StockEdge.

Our stock-sectors.json is ~52% UNCLASSIFIED and a patchwork of heuristics. This
ETL builds an INDEPENDENT, re-runnable mapping keyed by NSE ticker using
StockEdge's richer taxonomy (41 sectors / ~145 industries), adopted wholesale
and normalised to CANONICAL_UPPERCASE_UNDERSCORE names.

Pipeline (direct StockEdge REST API — NOT 4000 MCP round-trips):
  1. GetAllSectors...            -> the 41-sector / industry taxonomy
  2. GetSectorPeerList/{id}      -> SecurityID -> sector (all ~7000 SE stocks)
  3. per universe ticker: quick-search -> SecurityID -> join sector
Writes data/sector_map.json {ticker: {sector, sector_id, securityid, se_name,
source:"stockedge", updated}}. Resumable: caches ticker->securityid to disk.

Run POC first:  .venv/bin/python tools/build_sector_map.py --poc 40
Full run:       .venv/bin/python tools/build_sector_map.py
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

BASE = "https://api.stockedge.com"
TOKENS = Path.home() / ".config/stockedge/tokens.json"
REPO = Path("/home/rakesh/work/momentum-universe")
OUT = REPO / "data/sector_map.json"
CACHE = REPO / "data/.se_search_cache.json"
PAUSE = 0.15                     # polite rate-limit between calls


def _token() -> str:
    return json.loads(TOKENS.read_text())["access_token"]


def _get(path: str, tok: str):
    r = requests.get(BASE + path, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    if r.status_code == 401:
        raise SystemExit("StockEdge token expired — refresh via scripts/stockedge-token-extract.js")
    r.raise_for_status()
    return r.json()


def canon(name: str) -> str:
    """'Fast Moving Consumer Goods' -> FAST_MOVING_CONSUMER_GOODS;
    'Automobile & Ancillaries' -> AUTOMOBILE_AND_ANCILLARIES;
    'Non - Ferrous Metals' -> NON_FERROUS_METALS."""
    s = name.upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9]+", "_", s).strip("_")
    return re.sub(r"_+", "_", s)


def build_sector_index(tok: str) -> tuple[dict, dict]:
    """Return (securityid -> {sector, sector_id, industry?}, taxonomy)."""
    sectors = _get("/Api/SectorDashboardApi/GetAllSectorsWithRespectiveIndustriesAndMcap?sectorSort=1", tok)
    taxonomy = {}
    secid_map = {}
    for sec in sectors:
        sid, sname = sec["ID"], canon(sec["Name"])
        taxonomy[sname] = {"id": sid, "industries": [canon(i["Name"]) for i in sec.get("IndustriesForSector", [])]}
        page = 1
        while True:
            peers = _get(f"/Api/SectorDashboardApi/GetSectorPeerList/{sid}?page={page}&pageSize=20", tok)
            if not peers:
                break
            for p in peers:
                secid_map[p["SecurityID"]] = {"sector": sname, "sector_id": sid, "se_name": p["Name"]}
            if len(peers) < 20:
                break
            page += 1
            time.sleep(PAUSE)
        print(f"  [{sname}] {sec['StocksCount']} stocks", flush=True)
        time.sleep(PAUSE)
    return secid_map, taxonomy


def search_securityid(ticker: str, tok: str, cache: dict) -> int | None:
    if ticker in cache:
        return cache[ticker]
    try:
        res = _get(f"/Api/UniversalSearchApi/GetQuickSearchResult?searchTerm={ticker}", tok)
    except Exception:
        return None
    sid = None
    for row in res.get("Data", []):
        if row.get("EntityCode") == "se_security":
            sid = row["DocId"]; break
    cache[ticker] = sid
    return sid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poc", type=int, default=0, help="limit to N tickers for a proof-of-concept")
    args = ap.parse_args()
    tok = _token()

    from nse_universe.core.db import db
    with db(read_only=True) as con:
        tickers = sorted(r[0] for r in con.execute("SELECT DISTINCT symbol FROM universe_rank").fetchall())
    if args.poc:
        tickers = tickers[:: max(1, len(tickers) // args.poc)][: args.poc]
    print(f"building sector index from StockEdge ...", flush=True)
    secid_map, taxonomy = build_sector_index(tok)
    print(f"  indexed {len(secid_map)} StockEdge securities across {len(taxonomy)} sectors\n", flush=True)

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    out, matched, unmatched = {}, 0, []
    for i, t in enumerate(tickers):
        sid = search_securityid(t, tok, cache)
        if sid and sid in secid_map:
            m = secid_map[sid]
            out[t] = {"sector": m["sector"], "sector_id": m["sector_id"], "securityid": sid,
                      "se_name": m["se_name"], "source": "stockedge", "updated": str(date.today())}
            matched += 1
        else:
            unmatched.append(t)
        if (i + 1) % 100 == 0:
            CACHE.write_text(json.dumps(cache)); print(f"  {i+1}/{len(tickers)} · matched {matched}", flush=True)
        time.sleep(PAUSE)
    CACHE.write_text(json.dumps(cache))

    payload = {"_meta": {"source": "stockedge", "updated": str(date.today()),
                         "matched": matched, "total": len(tickers), "taxonomy": taxonomy},
               "symbols": out}
    if not args.poc:
        OUT.write_text(json.dumps(payload, indent=1))
    print(f"\nMATCHED {matched}/{len(tickers)} ({matched*100//len(tickers)}%) · "
          f"unmatched {len(unmatched)}")
    print("sample:", {k: out[k]["sector"] for k in list(out)[:12]})
    print("unmatched sample:", unmatched[:15])
    if not args.poc:
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
