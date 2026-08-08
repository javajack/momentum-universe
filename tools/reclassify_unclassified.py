"""Second pass: iterate EVERY remaining UNCLASSIFIED ticker and map it from
StockEdge if resolvable — closing the gap left by the first build.

The first build searched by raw NSE ticker; some real names failed on ticker
formatting (MRO-TEK hyphen), transient errors, or cached nulls. This re-searches
each UNCLASSIFIED with a cleaned-ticker fallback, joins against the CURRENT
StockEdge sector + industry indices, and maps every hit (source=stockedge_retry).
Names StockEdge's current constituent lists don't carry (genuinely delisted) stay
UNCLASSIFIED — no live source classifies them. Rewrites data/sector_map.json.

Run:  .venv/bin/python tools/reclassify_unclassified.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date

sys.path.insert(0, "/home/rakesh/work/momentum-universe")
from tools.build_sector_map import _token, _get, canon, build_sector_index, OUT

PAUSE = 0.12


def industry_index(tok):
    sectors = _get("/Api/SectorDashboardApi/GetAllSectorsWithRespectiveIndustriesAndMcap?sectorSort=1", tok)
    inds = [(i["ID"], canon(i["Name"])) for s in sectors for i in s.get("IndustriesForSector", [])]
    out = {}
    for iid, iname in inds:
        page = 1
        while True:
            peers = _get(f"/Api/IndustryDashboardApi/GetIndustryPeerList/{iid}?page={page}&pageSize=20", tok)
            if not peers:
                break
            for p in peers:
                out[p["SecurityID"]] = iname
            if len(peers) < 20:
                break
            page += 1
            time.sleep(PAUSE)
        time.sleep(PAUSE)
    return out


def find_secid(ticker, tok):
    for term in (ticker, re.sub(r"[^A-Za-z0-9]", "", ticker)):
        try:
            res = _get(f"/Api/UniversalSearchApi/GetQuickSearchResult?searchTerm={term}", tok)
        except Exception:
            continue
        for row in res.get("Data", []):
            if row.get("EntityCode") == "se_security":
                return row["DocId"], row["Name"]
        time.sleep(PAUSE)
    return None, None


def main():
    tok = _token()
    sm = json.loads(OUT.read_text())
    uncl = sorted(s for s, v in sm["symbols"].items() if v["sector"] == "UNCLASSIFIED")
    print(f"{len(uncl)} UNCLASSIFIED to retry", flush=True)

    print("rebuilding sector + industry indices ...", flush=True)
    secid_sector, _ = build_sector_index(tok)
    secid_ind = industry_index(tok)
    print(f"  sector idx {len(secid_sector)} · industry idx {len(secid_ind)}\n", flush=True)

    today = str(date.today())
    recovered = 0
    for i, t in enumerate(uncl):
        sid, name = find_secid(t, tok)
        if sid and sid in secid_sector:
            m = secid_sector[sid]
            ind = secid_ind.get(sid, m["sector"])
            sm["symbols"][t] = {"sector": m["sector"], "sector_id": m["sector_id"],
                                "securityid": sid, "se_name": name, "industry": ind,
                                "sub_sector": ind, "source": "stockedge_retry", "updated": today}
            recovered += 1
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(uncl)} · recovered {recovered}", flush=True)
        time.sleep(PAUSE)

    from collections import Counter
    src = Counter(v["source"] for v in sm["symbols"].values())
    still = sum(1 for v in sm["symbols"].values() if v["sector"] == "UNCLASSIFIED")
    sm["_meta"].update({"updated": today, "source_breakdown": dict(src)})
    OUT.write_text(json.dumps(sm, indent=1))
    print(f"\nrecovered {recovered} · still UNCLASSIFIED {still}")
    print(f"source breakdown: {dict(src)}")


if __name__ == "__main__":
    main()
