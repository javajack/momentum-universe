"""Re-tune the sector cap for the new 41-sector StockEdge vocabulary.

Adopting StockEdge's finer taxonomy made the existing max_sector_exposure (0.30,
tuned for ~20 coarse sectors) bind differently — a % cap on FINANCE is looser
once it splits into BANK_PRIVATE / NBFC / INSURANCE / AMC. This sweeps the cap
on the NEW map (now the default), full-period + walk-forward on the best, so the
setting is chosen on honest classification rather than the old 52%-unclassified
data. Run:  .venv/bin/python tools/sector_cap_sweep.py
"""
from __future__ import annotations

import sys
from datetime import date

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

CAPS = [0.25, 0.30, 0.35, 0.40, 0.50]     # 0.30 = current; le=0.50 is the field max


def bt(cap: float, start: date, end: date):
    from fortress.config import load_config
    from fortress import actions as A
    cfg = load_config("config.yaml")
    cfg = A.apply_selection(cfg, strategy="dual_momentum")
    cfg.position_sizing.max_sector_exposure = cap
    return A.run_backtest(cfg, start, end)


def line(tag, r):
    calmar = r.cagr / abs(r.max_drawdown) if r.max_drawdown else 0
    print(f"  {tag:20} CAGR {r.cagr*100:6.2f}%  MaxDD {r.max_drawdown*100:6.1f}%  "
          f"Sharpe {r.sharpe_ratio:5.2f}  Calmar {calmar:4.2f}  Rs{r.final_value/1e5:.1f}L", flush=True)
    return calmar


def main():
    start, end = date(2016, 1, 1), date(2026, 8, 5)
    print(f"sector-cap re-tune (41-vocab map) — dual_momentum {start}..{end}\n"
          f"FULL-PERIOD sweep:", flush=True)
    best = (None, -1)
    for cap in CAPS:
        r = bt(cap, start, end)
        c = line(f"cap={cap:.2f}" + (" (current)" if cap == 0.30 else ""), r)
        if c > best[1]:
            best = (cap, c)
    print(f"\nbest full-period Calmar: cap={best[0]:.2f}", flush=True)

    print(f"\nWALK-FORWARD @ cap={best[0]:.2f} vs current 0.30:")
    wins = [(date(2016,1,1),date(2019,12,31),"2016-2019"),
            (date(2020,1,1),date(2022,12,31),"2020-2022"),
            (date(2023,1,1),end,"2023-2026 OOS")]
    for cap in sorted({best[0], 0.30}):
        print(f" cap={cap:.2f}:")
        for a, b, lab in wins:
            line(f"  {lab}", bt(cap, a, b))


if __name__ == "__main__":
    main()
