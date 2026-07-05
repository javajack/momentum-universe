"""Momentum scan — rank the universe by the active strategy, top-N.

Ranks every stock in the selected [lo,hi] rank window by the ACTIVE strategy's
momentum score as of the latest data, and returns the top N that pass the
strategy's entry filters — each with its useful per-stock parameters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from fortress.config import Config


@dataclass
class ScanResult:
    strategy: str
    version: str
    rank_range: Tuple[int, int]
    as_of: date
    total_passing: int
    stocks: List = field(default_factory=list)  # top-N StockScore objects


def momentum_scan(config: Config, top_n: int = 20, as_of: Optional[date] = None) -> ScanResult:
    """Return the top `top_n` momentum-ranked stocks (active strategy, selected
    universe) as of the latest available data. Pure: no prompts, no broker."""
    from fortress.universe import Universe
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.backtest import BacktestMarketDataAdapter
    from fortress.strategy.registry import StrategyRegistry

    u = config.universe
    rank_range = tuple(u.rank_range)
    end = as_of or date.today()
    start = end - timedelta(days=365 * 3)  # enough for 12m momentum + warmup

    historical = load_historical_for_backtest(
        start=start, end=end, rank_range=rank_range, version=u.version
    )
    if not historical:
        raise ValueError("no price data for the selected universe/window")
    latest = max(df.index[-1] for df in historical.values() if len(df))
    as_of_dt = datetime.combine(latest.date(), datetime.max.time())

    universe = Universe(rank_range=rank_range, version=u.version)
    strategy = StrategyRegistry.get(config.active_strategy, config)
    ranked = strategy.rank_stocks(
        as_of_date=as_of_dt,
        universe=universe,
        market_data=BacktestMarketDataAdapter(historical),
        filter_entry=True,
    )
    return ScanResult(
        strategy=config.active_strategy,
        version=u.version,
        rank_range=(rank_range[0], rank_range[1]),
        as_of=latest.date(),
        total_passing=len(ranked),
        stocks=ranked[:top_n],
    )
