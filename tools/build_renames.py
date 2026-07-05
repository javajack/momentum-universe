"""Detect ticker-mapping breaks in the live universe and propose stock-renames.json entries.

WHY THIS EXISTS
---------------
The momentum universe is selected from nse-universe rank files (point-in-time,
turnover-ranked). nse-universe ingests **EQ series only** and does NOT track
corporate name changes, EQ->BE (Trade-to-Trade) surveillance moves, or
delistings (see nse500 README "Design tradeoffs"). So a stock can stay in the
selected universe under a ticker that the **current Kite NSE EQ instruments
dump no longer carries** — which raises `ValueError: Unknown symbol: <T>` on
the next cache update (fortress/market_data.py).

`stock-renames.json` is the patch layer for this. This tool finds the breaks
and proposes the entries automatically, instead of diffing by hand each month:

  - **EQ -> BE / delisted**  -> `{"to": null}`  (drop from selection; untradeable on Kite EQ)
  - **renamed (ISIN stays EQ under a new ticker)** -> `{"to": "<NEW>"}`  (rewrite)

Classification is by **ISIN continuity** against the raw NSE bhavcopy archive
(the same source nse-universe is built from), so every proposal carries the
ISIN, the transition date, and the evidence that produced it.

WHEN TO RUN
-----------
  - After an nse500 sync (monthly, or whenever you refresh bhavcopy), OR
  - Whenever a cache update (CLI Option 1) reports "Unknown symbol" failures.
It first warns if nse-universe itself is stale — run the nse500 sync before
trusting the proposals, or recent renames will be invisible (see learning below).

USAGE
-----
    .venv/bin/python tools/build_renames.py                 # dry-run: print proposals
    .venv/bin/python tools/build_renames.py --apply         # merge proposals into stock-renames.json
    .venv/bin/python tools/build_renames.py --as-of 2026-06-03 --window 120

`--apply` only ADDS new keys; it never overwrites or removes existing entries,
and writes a .bak first. Verify renames (not drops) by eye before trusting —
ISIN continuity is reliable but mergers/demergers can be ambiguous.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

RENAMES_PATH = REPO_ROOT / "stock-renames.json"
_DATE_RE = re.compile(r"_(\d{8})_F_")  # new UDiFF filename: BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip


# ---- bhavcopy access -------------------------------------------------------

def _bhav_date(path: Path) -> Optional[str]:
    m = _DATE_RE.search(path.name)
    return m.group(1) if m else None


def _read_bhav(path: Path) -> list[tuple[str, str, str, str]]:
    """Return [(ticker, series, isin, name)] for one UDiFF bhavcopy zip."""
    with zipfile.ZipFile(path) as z:
        data = z.read(z.namelist()[0]).decode()
    out = []
    for r in csv.DictReader(io.StringIO(data)):
        out.append(
            (r["TckrSymb"].strip(), r["SctySrs"].strip(),
             r["ISIN"].strip(), r["FinInstrmNm"].strip())
        )
    return out


def _build_index(raw_dir: Path, as_of: date, window_days: int) -> list[dict]:
    """Per-date maps for the last `window_days`, oldest-first.

    Each element: {date, t_series, isin_eq_tickers, isin_series, isin_name}.
    Only the new UDiFF format is parsed (covers every event since 2024-07).
    """
    lo = (as_of - timedelta(days=window_days)).strftime("%Y%m%d")
    hi = as_of.strftime("%Y%m%d")
    zips = []
    for p in raw_dir.glob("*/*/BhavCopy_*.zip"):
        d = _bhav_date(p)
        if d and lo <= d <= hi:
            zips.append((d, p))
    zips.sort()
    index = []
    for d, p in zips:
        t_series: dict[str, set] = {}
        isin_eq: dict[str, set] = {}
        isin_series: dict[str, set] = {}
        isin_name: dict[str, str] = {}
        for t, s, i, nm in _read_bhav(p):
            t_series.setdefault(t, set()).add(s)
            isin_series.setdefault(i, set()).add(s)
            isin_name[i] = nm
            if s == "EQ":
                isin_eq.setdefault(i, set()).add(t)
        index.append({
            "date": d, "t_series": t_series, "isin_eq": isin_eq,
            "isin_series": isin_series, "isin_name": isin_name,
        })
    return index


# ---- classification --------------------------------------------------------

def _classify(old: str, index: list[dict]) -> Optional[dict]:
    """Classify one selected-but-unmapped ticker via ISIN continuity.

    Returns a proposal dict, or None if the ticker is actually fine (false
    positive — still EQ in the latest bhavcopy).
    """
    if not index:
        return {"old": old, "to": None, "isin": None, "effective": None,
                "note": "no bhavcopy in window — cannot classify (run nse500 sync)"}
    latest = index[-1]

    # ISIN + last date `old` traded as EQ (scan newest-first).
    isin = last_eq = None
    for snap in reversed(index):
        eqset = snap["t_series"].get(old)
        if eqset and "EQ" in eqset:
            last_eq = snap["date"]
            for i, tks in snap["isin_eq"].items():
                if old in tks:
                    isin = i
                    break
            break
    if isin is None:
        return {"old": old, "to": None, "isin": None, "effective": None,
                "note": f"{old}: never EQ in window — likely always-BE or pre-window delisting"}

    eq_now = latest["isin_eq"].get(isin, set())
    series_now = sorted(latest["isin_series"].get(isin, []))
    name = latest["isin_name"].get(isin, "")

    # False positive: still EQ under the same ticker -> maps fine, no entry.
    if old in eq_now:
        return None

    # effective = first date after last_eq where the OLD ticker stopped trading
    # as EQ (it moved series, got delisted, or was replaced by the rename target).
    # Tracking the ticker (not the ISIN) is correct for renames too, where the
    # ISIN keeps trading EQ under the new symbol.
    effective = None
    for snap in index:
        if snap["date"] <= last_eq:
            continue
        ser = snap["t_series"].get(old)
        if not ser or "EQ" not in ser:
            effective = snap["date"]
            break
    eff_iso = (datetime.strptime(effective, "%Y%m%d").date().isoformat()
               if effective else date.today().isoformat())

    other_eq = sorted(eq_now - {old})
    if other_eq:  # ISIN now trades EQ under a different ticker -> rename
        new = other_eq[0]
        return {"old": old, "to": new, "isin": isin, "effective": eff_iso,
                "note": f"{name or old}: renamed {old} -> {new} (ISIN continuity, last EQ {last_eq})"}
    if series_now:  # present but not EQ -> surveillance move (BE / T2T)
        return {"old": old, "to": None, "isin": isin, "effective": eff_iso,
                "note": f"{name or old}: moved EQ -> {'/'.join(series_now)} "
                        f"(Trade-to-Trade) {eff_iso}; not in Kite NSE EQ dump"}
    # ISIN absent from latest bhavcopy entirely -> delisted
    return {"old": old, "to": None, "isin": isin, "effective": eff_iso,
            "note": f"{name or old}: delisted — ISIN absent from NSE bhavcopy after last EQ {last_eq}"}


# ---- staleness -------------------------------------------------------------

def _sync_status(index: list[dict]) -> dict:
    """Compare nse-universe last-sync vs the freshest bhavcopy on disk."""
    try:
        from nse_universe.paths import STATE_PATH
        state = json.loads(Path(STATE_PATH).read_text())
        last_sync = (state.get("last_sync_completed_at") or "")[:10]
    except Exception:
        last_sync = ""
    freshest = (datetime.strptime(index[-1]["date"], "%Y%m%d").date().isoformat()
                if index else "")
    stale = bool(last_sync and freshest and last_sync < freshest)
    return {"last_sync": last_sync, "freshest_bhav": freshest, "stale": stale}


# ---- top-level -------------------------------------------------------------

def analyze(*, as_of: Optional[date] = None, config_path: str = "config.yaml",
            window_days: int = 120) -> dict:
    """Find selected tickers that no longer map to a Kite NSE EQ instrument
    and classify each. Returns {proposals, sync, selection_size, unmapped}."""
    from fortress.config import load_config
    from fortress.universe import Universe
    from nse_universe.paths import RAW_DIR

    as_of = as_of or date.today()
    cfg = load_config(config_path)
    ver, rr = cfg.universe.version, tuple(cfg.universe.rank_range)
    selected = sorted(s.ticker for s in
                      Universe(as_of=as_of, rank_range=rr, version=ver).get_all_stocks())

    index = _build_index(Path(RAW_DIR), as_of, window_days)
    eq_now = set()
    if index:
        for t, ss in index[-1]["t_series"].items():
            if "EQ" in ss:
                eq_now.add(t)

    unmapped = [s for s in selected if s not in eq_now] if index else []
    proposals = [p for p in (_classify(s, index) for s in unmapped) if p]
    existing = set(json.loads(RENAMES_PATH.read_text()).get("renames", {}))
    new = [p for p in proposals if p["old"] not in existing]
    return {
        "as_of": as_of.isoformat(),
        "version": ver, "rank_range": list(rr),
        "sync": _sync_status(index),
        "selection_size": len(selected),
        "unmapped": unmapped,
        "proposals": proposals,
        "new_proposals": new,
    }


def apply_entries(new_proposals: list[dict], path: Path = RENAMES_PATH) -> int:
    """Merge new entries into stock-renames.json (additive only). Returns count added."""
    doc = json.loads(path.read_text())
    renames = doc.setdefault("renames", {})
    added = 0
    for p in new_proposals:
        if p["old"] in renames:
            continue
        renames[p["old"]] = {
            "to": p["to"], "isin": p["isin"],
            "effective": p["effective"], "note": p["note"],
        }
        added += 1
    if added:
        path.with_suffix(".json.bak").write_text(json.dumps(json.loads(path.read_text()), indent=2) + "\n")
        path.write_text(json.dumps(doc, indent=2) + "\n")
    return added


def format_report(result: dict) -> str:
    lines = []
    s = result["sync"]
    lines.append("━━━ stock-renames builder — ISIN-continuity scan ━━━")
    lines.append(f"  as-of: {result['as_of']}  •  universe v={result['version']} "
                 f"rank_range={result['rank_range']}  •  selected={result['selection_size']}")
    lines.append(f"  nse-universe last sync: {s['last_sync'] or '?'}  •  "
                 f"freshest bhavcopy: {s['freshest_bhav'] or 'none'}")
    if s["stale"]:
        lines.append("  ⚠ nse-universe is STALE vs bhavcopy on disk — run the nse500 sync "
                     "(./start.sh → Full pipeline) before trusting these proposals.")
    if not result["unmapped"]:
        lines.append("\n  ✓ every selected ticker maps to a Kite NSE EQ instrument — nothing to do.")
        return "\n".join(lines)

    new = result["new_proposals"]
    lines.append(f"\n  {len(result['unmapped'])} unmapped, "
                 f"{len(new)} new (not already in stock-renames.json):\n")
    lines.append(f"  {'OLD':13s} {'VERDICT':8s} {'TO':12s} {'EFFECTIVE':11s} {'ISIN':14s} NOTE")
    for p in result["proposals"]:
        is_new = p in new
        verdict = "RENAME" if p["to"] else "DROP"
        flag = "" if is_new else "  (exists)"
        lines.append(f"  {p['old']:13s} {verdict:8s} {str(p['to'] or '-'):12s} "
                     f"{str(p['effective']):11s} {str(p['isin']):14s} {p['note']}{flag}")
    if new:
        lines.append("\n  Run with --apply to add the new entries to stock-renames.json.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Propose/apply stock-renames.json entries from bhavcopy ISIN continuity")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--as-of", default=None, help="ISO date; default today")
    ap.add_argument("--window", type=int, default=120, help="bhavcopy look-back days for ISIN backtrace")
    ap.add_argument("--apply", action="store_true", help="merge NEW proposals into stock-renames.json")
    args = ap.parse_args()

    result = analyze(
        as_of=date.fromisoformat(args.as_of) if args.as_of else None,
        config_path=args.config, window_days=args.window,
    )
    print(format_report(result))
    if args.apply:
        n = apply_entries(result["new_proposals"])
        print(f"\n  applied: {n} new entr{'y' if n == 1 else 'ies'} added to {RENAMES_PATH.name}"
              + (" (backup: stock-renames.json.bak)" if n else " — nothing to add"))


if __name__ == "__main__":
    main()
