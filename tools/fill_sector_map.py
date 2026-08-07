"""Fill every remaining universe ticker into the ONE StockEdge-canonical map.

Runs AFTER build_sector_map.py. There is a single mapping system: data/sector_map.json.
Whatever StockEdge didn't match directly is resolved INTO the same 41-sector
canonical vocabulary, in priority order:

  1. stockedge   — direct match (already in the file from build_sector_map)
  2. rename      — ticker renamed; re-search the current name on StockEdge
  3. non_equity  — ETF / index-fund / gold-silver-liquid product (no sector)
  4. translated  — existing stock-sectors.json label mapped to StockEdge canonical
  5. unclassified— genuinely unknown (mostly delisted); left explicit

No second mapping file is kept — this rewrites data/sector_map.json in place.
Run:  .venv/bin/python tools/fill_sector_map.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

from tools.build_sector_map import (_token, _get, canon, build_sector_index,
                                     search_securityid, OUT, CACHE)
from nse_universe.rank.deny import is_non_equity

REPO = Path("/home/rakesh/work/momentum-universe")

# ETF / index / fund patterns that slipped deny.py (universe_rank still carries a
# few). These are NON_EQUITY — they get no sector.
_FUND_RE = re.compile(
    r"(NIFTY|SENSEX|BANKNIF|BANKBEE|GOLD|SILVER|LIQUID|SETF|IETF|ETF|BEES|"
    r"MOMENTUM|BFSI|PSUBAN|CPSE|BHARATBOND|DIVIDEND|VALUE|QUAL|ALPHA|LOWVOL|"
    r"MAFANG|NV20|SENETF|MID150|SML250|N100|MON100|TOP100|EQUAL|MULTICAP|"
    r"AONE|AXIS.*(NIFTY|SENSEX|VALUE|GOLD)|^EB|^BB|^ABSL|^UTINEXT|^ICICIB22)")

# old ~20-vocab -> StockEdge canonical (best-effort; ambiguous ones documented)
_TRANSLATE = {
    "FINANCIALS": "FINANCE",
    "INFORMATION_TECHNOLOGY": "INFORMATION_TECHNOLOGY",
    "HEALTHCARE": "HEALTHCARE",
    "INDUSTRIALS": "CAPITAL_GOODS",           # broad industrial default
    "ENERGY": "CRUDE_OIL",                     # oil & gas (power is separate)
    "UTILITIES": "POWER",
    "CONSUMER_STAPLES": "FAST_MOVING_CONSUMER_GOODS",
    "CONSUMER_DISCRETIONARY": "RETAILING",     # ambiguous (retail/durables)
    "AUTOMOBILES": "AUTOMOBILE_AND_ANCILLARIES",
    "INFRASTRUCTURE": "INFRASTRUCTURE",
    "REAL_ESTATE": "REALTY",
    "MATERIALS": "CHEMICALS",                  # ambiguous (largest SE materials bucket)
    "METALS_MINING": "IRON_AND_STEEL",         # ambiguous (steel/non-ferrous/mining)
    "MEDIA": "MEDIA_AND_ENTERTAINMENT",
    "TELECOM": "TELECOM",
    "DEFENSIVE": "NON_EQUITY", "COMMODITIES": "NON_EQUITY", "DEBT": "NON_EQUITY",
}


# Live index/ETF products that slipped both deny.py and _FUND_RE (curated from
# the residual live-UNCLASSIFIED list) — NON_EQUITY, no sector.
_CURATED_NONEQ = {
    "BANKBETA", "BANKPSU", "ELM250", "EMULTIMQ", "ESG", "EVINDIA", "ITBETA",
    "IWEL", "MAKEINDIA", "MASPTOP50", "MIDCAP", "MIDCAPBETA", "MNC", "MOGSEC",
    "MOM100", "MOM50", "MOMGF", "MOMIDMTM", "MONEXT50", "MONQ50", "MOPSE",
    "MOSERVICE", "MSCIINDIA", "NEXT50", "NEXT50BETA", "NPBET", "SBIBPB",
    "SELECTIPO", "SNXT50BETA", "TOP20", "GSEC10ABSL", "GSEC10YEAR",
    "HDFCBSE500", "HDFCGROWTH", "HDFCMOMENT", "HDFCNEXT50", "HDFCNIF100",
    "HDFCNIFBAN", "HDFCPSUBK", "HDFCPVTBAN",
} | {f"GROWW{s}" for s in ("CAPM", "DEFNC", "EV", "LIQID", "LOVOL", "MC150",
                            "MOM50", "N200", "NET", "NXT50", "RLTY", "SC250")}

# Real live equities StockEdge search missed on the raw ticker — researched into
# StockEdge-canonical sectors. (low-confidence ones flagged in the note.)
_CURATED_EQUITY = {
    "ACLGATI": "LOGISTICS",                    # Gati Ltd (Allcargo express logistics)
    "BARBEQUE": "HOSPITALITY",                 # Barbeque-Nation
    "FCL": "CHEMICALS",                        # Fineotex Chemical
    "GANESHHOUC": "REALTY",                    # Ganesh Housing
    "GEPIL": "CAPITAL_GOODS",                  # GE Power India
    "HEUBACHIND": "CHEMICALS",                 # Heubach Colorants (ex-Clariant pigments)
    "HOVS": "INFORMATION_TECHNOLOGY",          # HOV Services (BPO)
    "IPL": "CHEMICALS",                        # India Pesticides
    "JCHAC": "CONSUMER_DURABLES",              # Johnson Controls-Hitachi AC
    "KSL": "IRON_AND_STEEL",                   # Kalyani Steels
    "SANGHIIND": "CONSTRUCTION_MATERIALS",     # Sanghi Industries (cement)
    "SMLISUZU": "AUTOMOBILE_AND_ANCILLARIES",  # SML Isuzu (commercial vehicles)
    "ROML": "FAST_MOVING_CONSUMER_GOODS",      # Raj Oil Mills (edible oil) [low-conf]
    "SASTASUNDR": "RETAILING",                 # Sastasundar (online pharmacy/e-com) [low-conf]
    "SUNDARMHLD": "AGRICULTURE",               # Sundaram Holdings (plantations) [low-conf]
    "GEECEE": "REALTY",
}


def _is_fund(t: str) -> bool:
    return t in _CURATED_NONEQ or is_non_equity(t) or bool(_FUND_RE.search(t))


def main():
    tok = _token()
    sm = json.loads(OUT.read_text())
    out = sm["symbols"]
    taxonomy = sm["_meta"]["taxonomy"]
    valid = set(taxonomy) | {"NON_EQUITY", "UNCLASSIFIED"}

    from nse_universe.core.db import db
    with db(read_only=True) as con:
        allsyms = sorted(r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM universe_rank").fetchall())
    # Re-runnable: lock direct StockEdge/rename hits, reprocess everything else so
    # curated rules + new fund patterns re-apply on each run.
    out = {s: v for s, v in out.items() if v.get("source") in ("stockedge", "rename")}
    unmatched = [s for s in allsyms if s not in out]
    print(f"{len(out)} locked (stockedge/rename) · {len(unmatched)} to fill", flush=True)

    renames = json.loads((REPO / "stock-renames.json").read_text()).get("renames", {})
    ss = json.loads((REPO / "stock-sectors.json").read_text()).get("symbols", {})
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    # need SecurityID->sector to resolve renamed tickers on StockEdge
    print("rebuilding StockEdge sector index for rename-resolve ...", flush=True)
    secid_map, _ = build_sector_index(tok)

    today = str(date.today())
    counts = {"rename": 0, "non_equity": 0, "translated": 0, "unclassified": 0}
    for t in unmatched:
        # 0) curated researched equity -> StockEdge canonical
        if t in _CURATED_EQUITY:
            out[t] = {"sector": _CURATED_EQUITY[t], "source": "researched", "updated": today}
            counts["translated"] += 1
            continue
        # 1) fund/ETF -> NON_EQUITY
        if _is_fund(t):
            out[t] = {"sector": "NON_EQUITY", "source": "non_equity", "updated": today}
            counts["non_equity"] += 1
            continue
        # 2) rename -> re-search current name on StockEdge
        rn = renames.get(t)
        if rn and rn.get("to"):
            sid = search_securityid(rn["to"], tok, cache)
            if sid and sid in secid_map:
                m = secid_map[sid]
                out[t] = {"sector": m["sector"], "sector_id": m["sector_id"], "securityid": sid,
                          "se_name": m["se_name"], "renamed_to": rn["to"],
                          "source": "rename", "updated": today}
                counts["rename"] += 1
                continue
        # 3) translate existing label -> StockEdge canonical
        old = ss.get(t)
        old_sec = old.get("sector") if isinstance(old, dict) else old
        tgt = _TRANSLATE.get(old_sec)
        if tgt:
            out[t] = {"sector": tgt, "source": "translated", "from_label": old_sec, "updated": today}
            counts["translated"] += 1
            continue
        # 4) unknown
        out[t] = {"sector": "UNCLASSIFIED", "source": "unclassified", "updated": today}
        counts["unclassified"] += 1
        time.sleep(0.05)
    CACHE.write_text(json.dumps(cache))

    bad = {s: v["sector"] for s, v in out.items() if v["sector"] not in valid}
    assert not bad, f"non-canonical sectors produced: {bad}"

    from collections import Counter
    dist = Counter(v["sector"] for v in out.values())
    src = Counter(v["source"] for v in out.values())
    sm["symbols"] = out
    sm["_meta"].update({"updated": today, "total": len(out), "source_breakdown": dict(src)})
    OUT.write_text(json.dumps(sm, indent=1))
    print(f"\nfilled: {counts}")
    print(f"source breakdown: {dict(src)}")
    print(f"total mapped: {len(out)} · sectors used: {len(dist)}")
    print(f"top sectors: {dict(dist.most_common(12))}")
    print(f"still UNCLASSIFIED: {dist['UNCLASSIFIED']} (mostly delisted)")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
