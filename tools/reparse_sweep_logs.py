"""Re-parse sweep logs with corrected Overall-section regex.

The original sweep_rank_windows.py grabbed the FIRST occurrence of
'Max Drawdown' / 'Sharpe Ratio', which matched per-phase tables instead
of the Overall block. This fixes by anchoring at the 'Overall (XXX)'
section header.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WINDOWS = [
    ("large_cap",         (1, 200)),
    ("upper_mid",         (101, 300)),
    ("mid_band",          (201, 500)),
    ("small_top",         (301, 500)),
    ("small_tail",        (401, 600)),
    ("mid_to_small_wide", (201, 600)),
    ("ultra_wide",        (1, 600)),
]


def parse_overall(text: str) -> dict:
    """Extract metrics from the Overall section only."""
    # Slice from "Overall (...)" header to the next major section header.
    m = re.search(r"Overall \(EMERGING_MOMENTUM\).*?(?=Return Contribution|═{20,}|\Z)",
                   text, re.DOTALL)
    if not m:
        return {}
    block = m.group(0)

    patterns = {
        "final_value":  re.compile(r"Final Value\s+│\s+₹([\d,]+)"),
        "total_return": re.compile(r"Total Return\s+\(Phases\)\s+│\s+\+?([-+]?\d+\.\d+)%"),
        "cagr":         re.compile(r"CAGR\s+\(Phases\)\s+│\s+\+?([-+]?\d+\.\d+)%"),
        "sharpe":       re.compile(r"Sharpe Ratio\s+│\s+([-+]?\d+\.\d+)"),
        "max_dd":       re.compile(r"Max Drawdown\s+│\s+([-+]?\d+\.\d+)%"),
        "win_rate":     re.compile(r"Win Rate\s+│\s+(\d+)%"),
        "total_trades": re.compile(r"Total Trades\s+│\s+(\d+)"),
        "alpha_nifty":  re.compile(r"Alpha vs NIFTY 50\s+│\s+\+?([-+]?\d+\.\d+)%"),
    }
    out = {}
    for key, pat in patterns.items():
        mm = pat.search(block)
        if mm:
            raw = mm.group(1).replace(",", "")
            try:
                out[key] = float(raw)
            except ValueError:
                out[key] = raw
    return out


def main() -> None:
    results = []
    for label, rng in WINDOWS:
        log = REPO_ROOT / "plans" / f"sweep_{rng[0]}_{rng[1]}.log"
        if not log.exists():
            print(f"missing log: {log}")
            continue
        text = log.read_text()
        parsed = parse_overall(text)
        parsed["label"] = label
        parsed["rank_range"] = list(rng)
        results.append(parsed)

    print()
    print("=" * 110)
    print("CORRECTED SUMMARY — emerging_momentum + v2 — rank-window sweep (Overall section)")
    print("=" * 110)
    print(f"{'Label':25s} {'Range':12s} {'CAGR':>7s} {'Sharpe':>8s} {'MaxDD':>9s} "
          f"{'WinRt':>7s} {'Trades':>8s} {'TotRet':>10s} {'Alpha':>10s}")
    print("-" * 110)
    for r in results:
        rng_str = f"[{r['rank_range'][0]}, {r['rank_range'][1]}]"
        cagr = f"{r.get('cagr', float('nan')):>6.1f}%"
        sharpe = f"{r.get('sharpe', float('nan')):>7.2f}"
        maxdd = f"{r.get('max_dd', float('nan')):>8.1f}%"
        wr = f"{int(r.get('win_rate', 0)):>5d}%"
        trades = f"{int(r.get('total_trades', 0)):>7d}"
        totret = f"{r.get('total_return', float('nan')):>9.1f}%"
        alpha = f"{r.get('alpha_nifty', float('nan')):>9.1f}%"
        print(f"{r['label']:25s} {rng_str:12s} {cagr:>7s} {sharpe:>8s} {maxdd:>9s} "
              f"{wr:>7s} {trades:>8s} {totret:>10s} {alpha:>10s}")

    out = REPO_ROOT / "plans" / "rank_window_sweep_emerging_v2_corrected.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nCorrected results saved to {out}")


if __name__ == "__main__":
    main()
