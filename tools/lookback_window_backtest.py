"""Run emerging_momentum + v2 over rolling lookback windows ending 2026-02-11.

For each window in [6, 12, 18, 24, 36, 48] months:
  - start_date = end_date − N months
  - Pull historical data covering 18 months extra warmup (NMS lookback)
  - Run BacktestEngine, capture metrics

Usage:
    .venv/bin/python tools/lookback_window_backtest.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    from fortress.backtest import BacktestConfig, BacktestEngine
    from fortress.config import load_config
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.universe import Universe

    cfg = load_config("config.yaml")
    print(f"active_strategy:  {cfg.active_strategy}")
    print(f"universe.version: {cfg.universe.version}")
    print(f"rank_range:       {cfg.universe.rank_range}")

    rank_range = tuple(cfg.universe.rank_range)
    end_date = date(2026, 2, 11)        # match phase backtest end
    windows = [6, 12, 18, 24, 36, 48]

    # Load enough data to cover the longest window + 18mo warmup
    earliest_start = end_date - relativedelta(months=max(windows))
    data_start = earliest_start - relativedelta(months=18)
    print(f"\nLoading historical data: {data_start} → {end_date}")
    historical_data = load_historical_for_backtest(
        start=data_start, end=end_date, rank_range=rank_range,
        version=cfg.universe.version,
    )
    print(f"Loaded {len(historical_data)} symbols")

    results = []
    for n_months in windows:
        bt_start = end_date - relativedelta(months=n_months)
        # Universe pinned at end_date (membership at as-of-end)
        uni = Universe(as_of=end_date, rank_range=rank_range, version=cfg.universe.version)

        bt_config = BacktestConfig(
            start_date=datetime.combine(bt_start, datetime.min.time()),
            end_date=datetime.combine(end_date, datetime.min.time()),
            initial_capital=cfg.portfolio.initial_capital,
            rebalance_days=cfg.rebalancing.rebalance_days,
            transaction_cost=cfg.costs.transaction_cost,
            target_positions=cfg.position_sizing.target_positions,
            min_positions=cfg.position_sizing.min_positions,
            max_positions=cfg.position_sizing.max_positions,
            max_stocks_per_sector=3,
            use_stop_loss=True,
            initial_stop_loss=cfg.risk.initial_stop_loss,
            trailing_stop=cfg.risk.trailing_stop,
            trailing_activation=cfg.risk.trailing_activation,
            min_score_percentile=85.0,
            min_52w_high_prox=0.85,
            min_volume_ratio=1.1,
            min_daily_turnover=20_000_000,
            use_regime_detection=True,
            compare_benchmarks=True,
            profile_max_gold=cfg.regime.max_gold_allocation,
            strategy_name=cfg.active_strategy,
        )

        engine = BacktestEngine(
            universe=uni,
            historical_data=historical_data,
            config=bt_config,
            app_config=cfg,
            strategy_name=cfg.active_strategy,
        )

        print(f"\n--- Running {n_months}-month backtest ({bt_start} → {end_date}) ---")
        try:
            result = engine.run()
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            results.append({"months": n_months, "error": str(e)})
            continue

        row = {
            "months": n_months,
            "period": f"{bt_start} → {end_date}",
            "initial_capital": float(result.initial_capital),
            "final_value": float(result.final_value),
            "peak_value": float(result.peak_value),
            "total_return_pct": float(result.total_return) * 100,
            "cagr_pct":       float(result.cagr) * 100,
            "sharpe":         float(result.sharpe_ratio),
            "max_dd_pct":     float(result.max_drawdown) * 100,
            "win_rate_pct":   float(result.win_rate) * 100,
            "total_trades":   int(result.total_trades),
            "nifty_50_return_pct": (
                float(result.nifty_50_return) * 100 if result.nifty_50_return is not None else None
            ),
        }
        alpha = (
            row["total_return_pct"] - row["nifty_50_return_pct"]
            if row["nifty_50_return_pct"] is not None else None
        )
        if alpha is not None:
            row["alpha_vs_nifty_pct"] = alpha
        results.append(row)
        print(f"  CAGR {row['cagr_pct']:.1f}% | Sharpe {row['sharpe']:.2f} | "
              f"MaxDD {row['max_dd_pct']:.1f}% | TotRet {row['total_return_pct']:.1f}%")

    # Pretty table
    print()
    print("=" * 100)
    print(f"emerging_momentum + v2 — rolling lookback windows ending {end_date}")
    print("=" * 100)
    header = f"{'Months':>7} {'Period':30s} {'CAGR':>7s} {'Sharpe':>8s} {'MaxDD':>9s} {'TotRet':>10s} {'Alpha':>9s}"
    print(header)
    print("-" * 100)
    for r in results:
        if "error" in r:
            print(f"{r['months']:>7d} ERROR: {r['error']}")
            continue
        period = r["period"]
        cagr = f"{r['cagr_pct']:>6.1f}%"
        sharpe = f"{r['sharpe']:>7.2f}"
        maxdd = f"{r['max_dd_pct']:>8.1f}%"
        totret = f"{r['total_return_pct']:>9.1f}%"
        alpha = f"{r.get('alpha_vs_nifty_pct', float('nan')):>8.1f}%"
        print(f"{r['months']:>7d} {period:30s} {cagr:>7s} {sharpe:>8s} {maxdd:>9s} {totret:>10s} {alpha:>9s}")

    out = REPO_ROOT / "plans" / "lookback_windows_emerging_v2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
