"""Verify emerging_momentum + v2 doesn't blow past 12% position size.

Scans sample historical rebalance dates, runs the engine end-to-end, captures
target weights. Reports per-date max position + global max + any names that
exceeded hard_max_position (12%).

Usage:
    .venv/bin/python tools/check_position_sizes.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from fortress.cache import CacheManager
    from fortress.config import load_config
    from fortress.market_data import BacktestDataProvider
    from fortress.momentum_engine import MomentumEngine
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.strategy.registry import StrategyRegistry
    from fortress.universe import Universe

    cfg = load_config("config.yaml")
    print(f"active_strategy: {cfg.active_strategy}")
    print(f"universe.version: {cfg.universe.version}")
    print(f"universe.rank_range: {cfg.universe.rank_range}")
    print(f"hard_max_position: {cfg.risk.hard_max_position}")
    print()

    # Load data covering the period we want to sample
    sample_dates = [
        date(2014, 6, 2),    # bull early
        date(2016, 1, 4),    # post-correction
        date(2017, 7, 3),    # bull peak
        date(2018, 10, 1),   # NBFC crisis
        date(2019, 6, 3),    # pre-elections
        date(2020, 4, 1),    # covid trough
        date(2020, 10, 1),   # covid recovery
        date(2021, 7, 1),    # bull peak 2
        date(2022, 6, 1),    # rate shock
        date(2023, 1, 2),    # post-correction recovery
        date(2023, 11, 1),   # mid-bull
        date(2024, 5, 2),    # election overhang
        date(2024, 10, 1),   # post-elections
        date(2025, 3, 3),    # consolidation
        date(2025, 8, 1),    # late-cycle
        date(2026, 1, 1),    # year-start
    ]
    rank_range = tuple(cfg.universe.rank_range)
    earliest = min(sample_dates) - timedelta(days=500)
    latest = max(sample_dates) + timedelta(days=10)
    print(f"Loading historical data: {earliest} → {latest} (~{(latest-earliest).days}d)")
    historical_data = load_historical_for_backtest(
        start=earliest, end=latest, rank_range=rank_range,
        version=cfg.universe.version,
    )
    print(f"Loaded {len(historical_data)} symbols")

    strategy = StrategyRegistry.get(cfg.active_strategy, cfg)
    cached_provider = BacktestDataProvider(historical_data)

    results = []
    global_max = 0.0
    breaches = []

    for d in sample_dates:
        try:
            uni = Universe(as_of=d, rank_range=rank_range, version=cfg.universe.version)
        except Exception as e:
            print(f"[{d}] universe build failed: {e}")
            continue

        engine = MomentumEngine(
            universe=uni,
            market_data=cached_provider,
            momentum_config=cfg.pure_momentum,
            sizing_config=cfg.position_sizing,
            risk_config=cfg.risk,
            regime_config=cfg.regime,
            strategy=strategy,
            app_config=cfg,
            cached_data=historical_data,
        )

        as_of_dt = datetime.combine(d, datetime.max.time())
        try:
            target_weights, regime = engine.select_portfolio_with_regime(
                as_of_date=as_of_dt,
                portfolio_value=2_000_000,
                max_per_sector=3,
                profile_max_gold=cfg.regime.max_gold_allocation,
            )
        except Exception as e:
            print(f"[{d}] selection failed: {e}")
            continue

        if not target_weights:
            print(f"[{d}] empty basket")
            continue

        # Drop hedges (gold/cash) for the position-size check
        defensive = {cfg.regime.gold_symbol, cfg.regime.cash_symbol}
        equity_weights = {t: w for t, w in target_weights.items() if t not in defensive}
        if not equity_weights:
            continue

        max_t, max_w = max(equity_weights.items(), key=lambda kv: kv[1])
        n_positions = len(equity_weights)
        eq_total = sum(equity_weights.values())
        top5 = sorted(equity_weights.items(), key=lambda kv: -kv[1])[:5]

        regime_str = regime.regime.value if regime else "?"
        print(
            f"[{d}] regime={regime_str:9s}  N={n_positions:2d}  "
            f"max={max_w:.1%} ({max_t})  eq_sum={eq_total:.1%}"
        )
        print(f"            top5: " +
              ", ".join(f"{t} {w:.1%}" for t, w in top5))

        results.append({
            "date": str(d),
            "n_positions": n_positions,
            "max_weight": max_w,
            "max_ticker": max_t,
            "equity_total": eq_total,
            "regime": regime_str,
            "top5": [(t, float(w)) for t, w in top5],
        })

        if max_w > global_max:
            global_max = max_w

        if max_w > cfg.risk.hard_max_position:
            breaches.append((d, max_t, max_w))

    print()
    print("=" * 70)
    print(f"Scanned {len(results)} dates")
    print(f"Global max single-equity weight: {global_max:.1%}")
    print(f"Hard cap (risk.hard_max_position): {cfg.risk.hard_max_position:.1%}")
    if breaches:
        print(f"BREACHES (> hard cap): {len(breaches)}")
        for d, t, w in breaches:
            print(f"  {d}  {t}  {w:.1%}")
    else:
        print("No breaches — boost stacking is correctly contained by sizing logic.")

    # Persist for downstream reading
    out = REPO_ROOT / "plans" / f"position_size_audit_{cfg.active_strategy}_{cfg.universe.version}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "active_strategy": cfg.active_strategy,
        "universe_version": cfg.universe.version,
        "rank_range": list(rank_range),
        "hard_max_position": cfg.risk.hard_max_position,
        "global_max": global_max,
        "breaches": [(str(d), t, w) for d, t, w in breaches],
        "results": results,
    }, indent=2, default=str))
    print(f"Audit saved to {out}")


if __name__ == "__main__":
    main()
