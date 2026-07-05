"""Market-phase analysis — run the strategy across labelled market regimes.

`MARKET_PHASES` is a hand-curated timeline of Indian-market regimes. Phases from
2013-2024 are the established set; the 2024-09 → 2026-06 tail was re-segmented
from the actual NIFTY 50 path (peak/trough turning points) into six distinct
phases rather than one long lump. The benchmark data is refreshed through
2026-07, so the final phase runs to 2026-06-30 with per-phase alpha computable
throughout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd

from fortress.config import Config

# (name, start, end, type). Adjacent phases share a boundary date.
MARKET_PHASES = [
    ("2013 Consolidation", "2013-01-01", "2013-05-22", "Sideways"),
    ("Taper Tantrum & Rupee Crisis", "2013-05-22", "2013-08-28", "Bearish"),
    ("Pre-Election Rally", "2013-08-28", "2014-05-16", "Bullish"),
    ("Modi Election Bull Run", "2014-05-16", "2015-03-03", "Bullish"),
    ("2015 Correction", "2015-03-03", "2015-08-24", "Bearish"),
    ("China Scare & Recovery", "2015-08-24", "2016-03-01", "Bearish→Recovery"),
    ("Pre-Demonetization Bull", "2016-03-01", "2016-11-08", "Bullish"),
    ("Demonetization Shock & Recovery", "2016-11-08", "2017-04-01", "Bearish→Recovery"),
    ("2017 Bull Run", "2017-04-01", "2018-01-29", "Bullish"),
    ("NBFC / IL&FS Crisis", "2018-01-29", "2019-03-01", "Bearish"),
    ("2019 Recovery (Corp Tax Cut)", "2019-03-01", "2020-01-20", "Sideways→Bullish"),
    ("COVID Crash", "2020-01-20", "2020-04-01", "Crash"),
    ("Post-COVID Rally", "2020-04-01", "2021-10-18", "Bullish"),
    ("2022 Correction (Ukraine/Rates)", "2021-10-18", "2022-06-17", "Bearish"),
    ("2023-24 Recovery & Bull Run", "2022-06-17", "2024-09-27", "Bullish"),
    # --- re-segmented 2024-09 → 2026-04 (from NIFTY 50 turning points) ---
    ("2024-25 Correction", "2024-09-27", "2025-03-04", "Bearish"),        # peak 26216 -> trough 22083 (-15.7%)
    ("2025 Recovery Rally", "2025-03-04", "2025-06-27", "Bullish"),        # +16% off the March low
    ("Mid-2025 Consolidation", "2025-06-27", "2025-10-01", "Sideways"),    # choppy, dip to 24363 then back
    ("Late-2025 Bull Run", "2025-10-01", "2026-01-02", "Bullish"),         # to fresh highs 26329
    ("Early-2026 Correction", "2026-01-02", "2026-03-30", "Bearish"),       # peak 26329 -> trough 22331 (-15.2%)
    ("2026 Stabilization", "2026-03-30", "2026-06-30", "Sideways→Recovery"),  # bounce + choppy ~23.0-24.6k range
]


@dataclass
class PhaseResult:
    name: str
    phase_type: str
    start: str
    end: str
    strat_return: float
    max_dd: float
    nifty_return: Optional[float]
    alpha: Optional[float]


@dataclass
class PhaseReport:
    overall_return: float
    cagr: float
    sharpe: float
    max_dd: float
    initial_capital: float
    final_value: float
    phases: List[PhaseResult] = field(default_factory=list)


def run_market_phases(config: Config) -> PhaseReport:
    """Run one continuous backtest across MARKET_PHASES and return per-phase
    strategy return / drawdown / NIFTY alpha. Pure: no prompts, no printing.
    """
    from fortress.universe import Universe
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.backtest import BacktestConfig, BacktestEngine
    from fortress.indicators import calculate_drawdown

    start = datetime.strptime(MARKET_PHASES[0][1], "%Y-%m-%d")
    end = datetime.strptime(MARKET_PHASES[-1][2], "%Y-%m-%d")
    u = config.universe
    rank_range = tuple(u.rank_range)

    universe = Universe(rank_range=rank_range, version=u.version)
    historical = load_historical_for_backtest(
        start=start.date(), end=end.date(), rank_range=rank_range, version=u.version
    )
    bt = BacktestConfig(
        start_date=start, end_date=end,
        initial_capital=config.portfolio.initial_capital,
        rebalance_days=5,
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
        universe=universe, historical_data=historical, config=bt,
        app_config=config, strategy_name=config.active_strategy,
    )
    result = engine.run()
    equity = result.equity_curve

    nifty = historical.get("NIFTY 50")

    def nifty_return(a: pd.Timestamp, b: pd.Timestamp) -> Optional[float]:
        if nifty is None:
            return None
        w = nifty.loc[(nifty.index >= a) & (nifty.index <= b), "close"]
        return float(w.iloc[-1] / w.iloc[0] - 1) if len(w) >= 2 else None

    # Only phases that overlap actual trading.
    first_buy = next((t for t in result.trades if t.action == "BUY"), None)
    first_ts = pd.Timestamp(first_buy.date) if first_buy else equity.index[0]

    phases: List[PhaseResult] = []
    for name, s, e, ptype in MARKET_PHASES:
        p_start, p_end = pd.Timestamp(s), pd.Timestamp(e)
        if p_end <= first_ts:
            continue
        eq = equity.loc[(equity.index >= p_start) & (equity.index <= p_end)]
        if len(eq) < 2:
            continue
        ret = float(eq.iloc[-1] / eq.iloc[0] - 1)
        _, max_dd = calculate_drawdown(eq)
        nret = nifty_return(p_start, p_end)
        alpha = (ret - nret) if nret is not None else None
        phases.append(PhaseResult(name, ptype, s, e, ret, float(max_dd), nret, alpha))

    return PhaseReport(
        overall_return=float(result.total_return),
        cagr=float(result.cagr),
        sharpe=float(result.sharpe_ratio),
        max_dd=float(result.max_drawdown),
        initial_capital=float(result.initial_capital),
        final_value=float(equity.iloc[-1]) if len(equity) else float(result.initial_capital),
        phases=phases,
    )
