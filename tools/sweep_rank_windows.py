"""Sweep emerging_momentum + v2 across rank windows.

Runs the 13-yr phase backtest for each rank window, parses CAGR / Sharpe /
MaxDD / Total Return / Final Value, and prints a comparison table.

Each backtest takes ~4-5 minutes; full sweep ~25-30 min.

Usage:
    .venv/bin/python tools/sweep_rank_windows.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WINDOWS = [
    ("large_cap",         (1, 200)),
    ("upper_mid",         (101, 300)),
    ("mid_band",          (201, 500)),
    ("small_top",         (301, 500)),
    ("small_tail",        (401, 600)),
    ("mid_to_small_wide", (201, 600)),  # current default
    ("ultra_wide",        (1, 600)),
]

NUMBER = r"[-+]?\d[\d,]*\.\d+|[-+]?\d+\.\d+|[-+]?\d+"

PATTERNS = {
    "final_value":    re.compile(r"Final Value\s+│\s+₹([\d,]+)"),
    "total_return":   re.compile(r"Total Return\s+\(Phases\)\s+│\s+\+?([-+]?\d+\.\d+)%"),
    "cagr":           re.compile(r"CAGR\s+\(Phases\)\s+│\s+\+?([-+]?\d+\.\d+)%"),
    "sharpe":         re.compile(r"Sharpe Ratio\s+│\s+([-+]?\d+\.\d+)"),
    "max_dd":         re.compile(r"Max Drawdown\s+│\s+([-+]?\d+\.\d+)%"),
    "alpha_nifty":    re.compile(r"Alpha vs NIFTY 50\s+│\s+\+?([-+]?\d+\.\d+)%"),
}


def make_temp_config(rank_range: tuple[int, int]) -> Path:
    """Return path to a temp config.yaml with the rank_range overridden."""
    src = (REPO_ROOT / "config.yaml").read_text()
    new = re.sub(
        r"^(\s*rank_range:\s*)\[\s*\d+\s*,\s*\d+\s*\]",
        rf"\g<1>[{rank_range[0]}, {rank_range[1]}]",
        src, count=1, flags=re.MULTILINE,
    )
    out = Path(f"/tmp/config_sweep_{rank_range[0]}_{rank_range[1]}.yaml")
    out.write_text(new)
    return out


def parse_output(text: str) -> dict:
    out = {}
    for key, pat in PATTERNS.items():
        m = pat.search(text)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                out[key] = float(raw)
            except ValueError:
                out[key] = raw
    return out


def run_one(label: str, rng: tuple[int, int]) -> dict:
    print(f"\n{'=' * 70}")
    print(f"=== {label:24s}  rank_range = [{rng[0]}, {rng[1]}]")
    print(f"{'=' * 70}")
    cfg = make_temp_config(rng)
    t0 = datetime.now()
    cmd = [
        ".venv/bin/python", "-u",
        "tools/run_phase_backtest.py", "--config", str(cfg),
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=str(REPO_ROOT), env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    elapsed = (datetime.now() - t0).total_seconds()
    text = proc.stdout + proc.stderr
    log_out = REPO_ROOT / "plans" / f"sweep_{rng[0]}_{rng[1]}.log"
    log_out.parent.mkdir(exist_ok=True)
    log_out.write_text(text)

    parsed = parse_output(text)
    parsed["label"] = label
    parsed["rank_range"] = list(rng)
    parsed["elapsed_s"] = elapsed
    parsed["returncode"] = proc.returncode
    print(f"  elapsed={elapsed:.0f}s  rc={proc.returncode}  parsed={parsed}")
    return parsed


def main() -> None:
    results = []
    for label, rng in WINDOWS:
        try:
            results.append(run_one(label, rng))
        except Exception as e:
            print(f"  FAILED: {e}")
            results.append({"label": label, "rank_range": list(rng), "error": str(e)})

    # Print table
    print()
    print("=" * 100)
    print("SUMMARY  —  emerging_momentum + v2  —  rank window sweep")
    print("=" * 100)
    print(f"{'Label':25s} {'Range':12s} {'CAGR':>7s} {'Sharpe':>8s} {'MaxDD':>9s} "
          f"{'TotRet':>10s} {'Alpha':>9s}")
    print("-" * 100)
    for r in results:
        rng_str = f"[{r['rank_range'][0]}, {r['rank_range'][1]}]"
        cagr = f"{r.get('cagr', float('nan')):>6.1f}%"
        sharpe = f"{r.get('sharpe', float('nan')):>7.2f}"
        maxdd = f"{r.get('max_dd', float('nan')):>8.1f}%"
        totret = f"{r.get('total_return', float('nan')):>9.1f}%"
        alpha = f"{r.get('alpha_nifty', float('nan')):>8.1f}%"
        print(f"{r['label']:25s} {rng_str:12s} {cagr:>7s} {sharpe:>8s} {maxdd:>9s} "
              f"{totret:>10s} {alpha:>9s}")

    out = REPO_ROOT / "plans" / "rank_window_sweep_emerging_v2.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSweep results saved to {out}")


if __name__ == "__main__":
    main()
