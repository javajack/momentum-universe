"""Backtest action — build engine from config, run, return the result."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fortress.config import Config


def _as_datetime(d) -> datetime:
    if isinstance(d, datetime):
        return d
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day)
    return datetime.fromisoformat(str(d))


def run_backtest(
    config: Config,
    start,
    end,
    *,
    rebalance_days: Optional[int] = None,
):
    """Run a single backtest over [start, end] with the config's active strategy
    and universe, on the vendored nse_universe price data. Returns the engine's
    `BacktestResult` (total_return, cagr, sharpe_ratio, max_drawdown,
    equity_curve, trades, ...).

    Pure: no prompts, no printing. Rank window / version / strategy come from
    `config` (set them via `apply_selection` first).
    """
    from fortress.universe import Universe
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.backtest import BacktestConfig, BacktestEngine

    start_dt, end_dt = _as_datetime(start), _as_datetime(end)
    u = config.universe
    rank_range = tuple(u.rank_range)

    universe = Universe(rank_range=rank_range, version=u.version)
    historical = load_historical_for_backtest(
        start=start_dt.date(), end=end_dt.date(), rank_range=rank_range, version=u.version
    )

    bt_config = BacktestConfig(
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=config.portfolio.initial_capital,
        rebalance_days=rebalance_days or config.rebalancing.rebalance_days,
        transaction_cost=config.costs.transaction_cost,
        target_positions=config.position_sizing.target_positions,
        min_positions=config.position_sizing.min_positions,
        min_score_percentile=config.pure_momentum.min_score_percentile,
        min_52w_high_prox=config.pure_momentum.min_52w_high_prox,
        initial_stop_loss=config.risk.initial_stop_loss,
        trailing_stop=config.risk.trailing_stop,
        weight_6m=config.pure_momentum.weight_6m,
        weight_12m=config.pure_momentum.weight_12m,
        strategy_name=config.active_strategy,
        profile_max_gold=config.regime.max_gold_allocation,
    )
    engine = BacktestEngine(
        universe=universe,
        historical_data=historical,
        config=bt_config,
        app_config=config,
        strategy_name=config.active_strategy,
    )
    return engine.run()
