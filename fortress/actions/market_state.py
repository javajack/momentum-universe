"""Market-state / trigger check — current regime from the vendored index data.

Answers "how's the market lately / at the time of this run" without any broker
feed: it reads the shipped NIFTY 50 + India VIX benchmark series and runs the
same regime detector the backtest uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from fortress.config import Config

_BENCH_DIR = Path(__file__).resolve().parents[2] / "data" / "benchmarks"


@dataclass
class MarketState:
    as_of: date
    regime: str
    nifty_52w_position: float   # 0-1, position within trailing 52w range
    nifty_3m_return: float
    vix_level: float
    equity_weight: float
    gold_weight: float
    stress_score: float


def _close_series(name: str, as_of: Optional[date]) -> pd.Series:
    df = pd.read_parquet(_BENCH_DIR / f"{name}.parquet")
    s = df["close"].sort_index()
    if as_of is not None:
        s = s[s.index <= pd.Timestamp(as_of)]
    return s


def current_market_state(config: Config, as_of: Optional[date] = None) -> MarketState:
    """Compute the current market regime from the shipped NIFTY 50 / VIX series
    (as of `as_of`, default the latest available date). Pure: no prompts, no
    network. Delegates to `indicators.detect_market_regime`.
    """
    from fortress.indicators import detect_market_regime

    nifty = _close_series("nifty_50", as_of)
    vix = _close_series("india_vix", as_of)
    if len(nifty) < 63:
        raise ValueError("insufficient NIFTY history in the vendored benchmark data")

    vix_value = float(vix.iloc[-1]) if len(vix) else 15.0
    vix_history = vix.iloc[-30:] if len(vix) >= 10 else None
    regime = detect_market_regime(nifty, vix_value, config.regime, vix_history=vix_history)

    return MarketState(
        as_of=nifty.index[-1].date(),
        regime=regime.regime.value,
        nifty_52w_position=float(regime.nifty_52w_position),
        nifty_3m_return=float(regime.nifty_3m_return),
        vix_level=float(regime.vix_level),
        equity_weight=float(regime.equity_weight),
        gold_weight=float(regime.gold_weight),
        stress_score=float(regime.stress_score),
    )
