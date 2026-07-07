"""
Regime-switched momentum strategy for FORTRESS MOMENTUM.

Hard-switches the *scoring brain* by confirmed market regime while keeping
every other subsystem (portfolio selection, sizing, stops, gold/cash overlay,
recovery machines) identical to the parent strategies:

    NORMAL / BULLISH    (risk-on)  -> emerging_momentum scoring + exit ladder
    CAUTION / DEFENSIVE (risk-off) -> dual_momentum scoring + exit ladder
    regime unavailable             -> dual_momentum (conservative fallback)

Rationale: emerging_momentum's early-stage scoring leads whenever the market
is not stressed, while dual_momentum's classic 12-1 defends better once the
regime machine signals stress. Mapping was selected empirically on the 13y
backtest (2013->2026, v2 universe, ranks 201-600): risk-on/risk-off split
gives CAGR 21.9% / Sharpe 0.97 vs 20.3% / 0.89 for the best single strategy;
the two rejected mappings (emerging only in BULLISH, emerging only in NORMAL)
both landed between or below the single-strategy baselines. The engine already
pushes the confirmed RegimeResult into the strategy before every selection
(backtest and live paths both call `set_regime`), and its 3-day confirmation
hysteresis prevents whipsaw switching.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from ..indicators import MarketRegime
from .adaptive_dual_momentum import AdaptiveDualMomentumStrategy
from .base import ExitSignal, StockScore
from .emerging_momentum import EmergingMomentumStrategy
from .registry import StrategyRegistry

if TYPE_CHECKING:
    from ..market_data import MarketDataProvider
    from ..universe import Universe

logger = logging.getLogger(__name__)


class RegimeSwitchedMomentumStrategy(EmergingMomentumStrategy):
    """Best-of-both switcher: emerging scoring in confirmed bulls, dual otherwise."""

    aliases = ("switcher",)

    # Regimes in which the emerging scorer governs (dual governs the rest).
    # NOTE: gating the scorer on universe breadth (raw 0.45 floor, and a
    # 0.40/0.55 hysteresis band) was tested and REJECTED — both variants
    # underperformed this regime-only mapping because every scorer switch
    # forces turnover into a different book; the switch signal must be rare
    # and decisive. See docs/superpowers/specs/2026-07-06-regime-switched-
    # momentum-design.md for the numbers.
    EMERGING_REGIMES = frozenset({MarketRegime.NORMAL, MarketRegime.BULLISH})

    @property
    def name(self) -> str:
        return "regime_switched_momentum"

    @property
    def description(self) -> str:
        return (
            "Regime-switched momentum: emerging_momentum scoring in risk-on "
            "regimes (NORMAL/BULLISH), dual_momentum scoring in stress"
        )

    def _active_scorer(self) -> str:
        """Which scoring brain governs right now: 'emerging' or 'dual'."""
        regime = getattr(self, "_current_regime", None)
        if regime is not None and regime.regime in self.EMERGING_REGIMES:
            return "emerging"
        return "dual"

    def rank_stocks(
        self,
        as_of_date: datetime,
        universe: "Universe",
        market_data: "MarketDataProvider",
        filter_entry: bool = True,
    ) -> List[StockScore]:
        scorer = self._active_scorer()
        logger.info(f"regime_switched_momentum: {scorer} scorer active on {as_of_date:%Y-%m-%d}")
        if scorer == "emerging":
            return super().rank_stocks(
                as_of_date=as_of_date,
                universe=universe,
                market_data=market_data,
                filter_entry=filter_entry,
            )
        return AdaptiveDualMomentumStrategy.rank_stocks(
            self,
            as_of_date=as_of_date,
            universe=universe,
            market_data=market_data,
            filter_entry=filter_entry,
        )

    def check_exit_triggers(
        self,
        ticker: str,
        entry_price: float,
        current_price: float,
        peak_price: float,
        days_held: int,
        stock_score: Optional[StockScore],
        nms_percentile: float,
    ) -> ExitSignal:
        if self._active_scorer() == "emerging":
            return super().check_exit_triggers(
                ticker=ticker,
                entry_price=entry_price,
                current_price=current_price,
                peak_price=peak_price,
                days_held=days_held,
                stock_score=stock_score,
                nms_percentile=nms_percentile,
            )
        return AdaptiveDualMomentumStrategy.check_exit_triggers(
            self,
            ticker=ticker,
            entry_price=entry_price,
            current_price=current_price,
            peak_price=peak_price,
            days_held=days_held,
            stock_score=stock_score,
            nms_percentile=nms_percentile,
        )


StrategyRegistry.register(RegimeSwitchedMomentumStrategy)
