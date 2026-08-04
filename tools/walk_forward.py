"""Walk-forward validation harness — is a strategy's edge robust, or lucky?

Slides non-overlapping out-of-sample TEST windows across a decade of different
regimes (bull, COVID crash, recovery, 2022 correction, recent) and scores each
strategy on data the choice never depended on. The verdict is CONSISTENCY —
which strategy wins the most windows and holds the best MEDIAN test Calmar —
not any single backtest number.

Run (solo — holds the DuckDB lock):
    .venv/bin/python tools/walk_forward.py
"""
from __future__ import annotations

import sys
import time
from statistics import median
from typing import Dict, List, Tuple

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

from fortress.config import load_config
from fortress import actions as A

# Non-overlapping 2-year OOS windows spanning ~2016->2026 (distinct regimes).
WINDOWS: List[Tuple[str, str]] = [
    ("2016-07-01", "2018-07-01"),   # bull
    ("2018-07-01", "2020-07-01"),   # NBFC stress -> COVID crash
    ("2020-07-01", "2022-07-01"),   # post-COVID rally
    ("2022-07-01", "2024-07-01"),   # 2022 correction -> recovery
    ("2024-07-01", "2026-07-01"),   # recent top + correction
]
STRATS = ["dual_momentum", "regime_switched_momentum", "emerging_momentum"]


def run(strategy: str, start: str, end: str) -> Dict[str, float]:
    cfg = load_config("config.yaml")
    cfg = A.apply_selection(cfg, strategy=strategy, rank_range=[201, 600])
    r = A.run_backtest(cfg, start, end)
    dd = abs(r.max_drawdown) or 1e-9
    return {"cagr": r.cagr, "calmar": r.cagr / dd, "sharpe": r.sharpe_ratio,
            "maxdd": r.max_drawdown}


def summarize(grid: Dict[Tuple[str, str], Dict[str, Dict[str, float]]]) -> None:
    """grid[(start,end)][strategy] = metrics. Print per-window winners,
    win-rates, and median test metrics per strategy."""
    wins = {s: 0 for s in STRATS}
    cagrs = {s: [] for s in STRATS}
    calmars = {s: [] for s in STRATS}

    print("\n" + "=" * 78)
    print("PER-WINDOW (out-of-sample) — winner by Calmar")
    print("=" * 78)
    for win in WINDOWS:
        row = grid[win]
        winner = max(STRATS, key=lambda s: row[s]["calmar"])
        wins[winner] += 1
        print(f"\n{win[0]} -> {win[1]}   winner: {winner}")
        for s in STRATS:
            m = row[s]
            cagrs[s].append(m["cagr"]); calmars[s].append(m["calmar"])
            star = "  <-- win" if s == winner else ""
            print(f"   {s:26} CAGR {m['cagr']*100:6.1f}%  Calmar {m['calmar']:5.2f}  "
                  f"Sharpe {m['sharpe']:5.2f}  MaxDD {m['maxdd']*100:6.1f}%{star}")

    print("\n" + "=" * 78)
    print(f"WALK-FORWARD SUMMARY  ({len(WINDOWS)} out-of-sample windows)")
    print("=" * 78)
    print(f"{'strategy':26} {'windows won':>11} {'median CAGR':>12} {'median Calmar':>14}")
    for s in sorted(STRATS, key=lambda s: median(calmars[s]), reverse=True):
        print(f"{s:26} {wins[s]:>7}/{len(WINDOWS)}   {median(cagrs[s])*100:>10.1f}%   "
              f"{median(calmars[s]):>12.2f}")


if __name__ == "__main__":
    grid: Dict[Tuple[str, str], Dict[str, Dict[str, float]]] = {}
    for win in WINDOWS:
        grid[win] = {}
        for strat in STRATS:
            t0 = time.time()
            grid[win][strat] = run(strat, *win)
            m = grid[win][strat]
            print(f"[{win[0]}->{win[1]} {strat[:20]:20}] "
                  f"CAGR {m['cagr']*100:5.1f}% Calmar {m['calmar']:.2f} "
                  f"[{time.time()-t0:.0f}s]", flush=True)
    summarize(grid)
