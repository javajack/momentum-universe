"""#2 validation: how much do returns change when sector caps bucket on the new
StockEdge 41-sector vocabulary vs the old ~20-vocabulary?

Sectors only affect the strategy via sector-exposure caps. Finer buckets =
fewer names share a bucket = the cap binds less often = potentially more
concentration. This runs the SAME dual_momentum backtest twice — once with the
old stock-sectors.json, once with the new data/sector_map.json — and reports the
CAGR / MaxDD / Sharpe delta so the behavioural change is quantified, not guessed.

Run:  .venv/bin/python tools/sector_cap_ab.py
"""
from __future__ import annotations

import sys
from datetime import date

sys.path.insert(0, "/home/rakesh/work/momentum-universe")


def run(sectors_file: str, start: date, end: date):
    import json
    from fortress.config import load_config
    from fortress import actions as A
    import fortress.universe as U
    cfg = load_config("config.yaml")
    cfg = A.apply_selection(cfg, strategy="dual_momentum")
    # The backtest builds Universe() with the default sectors path, so drive the
    # A/B by injecting the chosen map into the process-wide cache under that key.
    symbols = json.load(open(sectors_file)).get("symbols", {})
    U._SECTOR_MAP_CACHE.clear()
    U._SECTOR_MAP_CACHE["data/sector_map.json"] = symbols
    return A.run_backtest(cfg, start, end)


def main():
    start, end = date(2016, 1, 1), date(2026, 8, 5)
    print(f"sector-cap A/B — dual_momentum {start}..{end}\n", flush=True)
    for label, sf in [("OLD (~20 vocab, stock-sectors.json)", "stock-sectors.json"),
                      ("NEW (41 vocab, data/sector_map.json)", "data/sector_map.json")]:
        r = run(sf, start, end)
        print(f"{label:42}  CAGR {r.cagr*100:6.2f}%  MaxDD {r.max_drawdown*100:6.1f}%  "
              f"Sharpe {r.sharpe_ratio:5.2f}  trades {len(r.trades)}  "
              f"final Rs{r.final_value/1e5:.1f}L", flush=True)


if __name__ == "__main__":
    main()
