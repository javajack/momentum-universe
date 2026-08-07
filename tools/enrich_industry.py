"""Phase-2: add per-stock INDUSTRY to data/sector_map.json from StockEdge.

build_sector_map gives sector only (GetSectorPeerList is sector-level). StockEdge
DOES expose industry constituents via GetIndustryPeerList/{industryId}, so this
iterates the ~145 industry IDs, builds SecurityID -> industry, and patches the
`industry` + `sub_sector` fields of every StockEdge-matched entry (those carry a
securityid). Non-matched entries keep sub_sector = sector.

Run:  .venv/bin/python tools/enrich_industry.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, "/home/rakesh/work/momentum-universe")
from tools.build_sector_map import _token, _get, canon, OUT

PAUSE = 0.15


def main():
    tok = _token()
    sectors = _get("/Api/SectorDashboardApi/GetAllSectorsWithRespectiveIndustriesAndMcap?sectorSort=1", tok)
    industries = [(i["ID"], canon(i["Name"]))
                  for sec in sectors for i in sec.get("IndustriesForSector", [])]
    print(f"pulling {len(industries)} industries ...", flush=True)

    secid_industry = {}
    for iid, iname in industries:
        page = 1
        while True:
            peers = _get(f"/Api/IndustryDashboardApi/GetIndustryPeerList/{iid}?page={page}&pageSize=20", tok)
            if not peers:
                break
            for p in peers:
                secid_industry[p["SecurityID"]] = iname
            if len(peers) < 20:
                break
            page += 1
            time.sleep(PAUSE)
        time.sleep(PAUSE)
    print(f"  indexed {len(secid_industry)} securities to industries", flush=True)

    sm = json.loads(OUT.read_text())
    patched = 0
    for sym, v in sm["symbols"].items():
        sid = v.get("securityid")
        if sid and sid in secid_industry:
            v["industry"] = secid_industry[sid]
            v["sub_sector"] = secid_industry[sid]   # sub_sector = industry now
            patched += 1
    sm["_meta"]["updated"] = str(date.today())
    sm["_meta"]["industry_enriched"] = patched
    OUT.write_text(json.dumps(sm, indent=1))
    print(f"patched industry on {patched} entries · wrote {OUT}")


if __name__ == "__main__":
    main()
