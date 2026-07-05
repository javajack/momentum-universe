"""Rebalance-from-inputs — target portfolio + order diff, credential-free.

Given a capital amount (and optional current holdings), compute what the active
strategy would hold as of the latest available data, and the BUY/SELL orders to
get there. Uses the vendored nse_universe price data via the backtest market-
data adapter — no broker feed, no credentials.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from fortress.config import Config


@dataclass
class TargetPosition:
    symbol: str
    weight: float
    target_value: float
    price: float
    quantity: int


@dataclass
class Order:
    symbol: str
    action: str          # BUY | SELL
    quantity: int
    price: float
    value: float


@dataclass
class RebalancePlan:
    as_of: date
    regime: str
    capital: float
    targets: List[TargetPosition] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)


def plan_rebalance(
    config: Config,
    capital: float,
    holdings: Optional[Dict[str, int]] = None,
    as_of: Optional[date] = None,
    top_n: Optional[int] = None,
) -> RebalancePlan:
    """Compute the target portfolio for `capital` and the orders vs `holdings`
    (symbol -> quantity). `as_of` defaults to the latest available data date.
    `top_n` overrides the target number of positions (custom allocation).
    Pure: no prompts, no network, no broker.
    """
    from fortress.universe import Universe
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.backtest import BacktestMarketDataAdapter
    from fortress.momentum_engine import MomentumEngine
    from fortress.strategy.registry import StrategyRegistry

    if top_n:
        n = int(top_n)
        sizing = config.position_sizing.model_copy(update={
            "target_positions": n,
            "min_positions": max(5, n - 3),
            "max_positions": n + 3,
        })
        config = config.model_copy(update={"position_sizing": sizing})

    holdings = holdings or {}
    u = config.universe
    rank_range = tuple(u.rank_range)
    end = as_of or date.today()
    start = end - timedelta(days=365 * 3)  # enough for 12m momentum + regime warmup

    historical = load_historical_for_backtest(
        start=start, end=end, rank_range=rank_range, version=u.version
    )
    if not historical:
        raise ValueError("no price data available for the selected universe/window")

    # Latest common date actually present in the data.
    latest_ts = max(df.index[-1] for df in historical.values() if len(df))
    as_of_dt = datetime.combine(latest_ts.date(), datetime.max.time())

    universe = Universe(rank_range=rank_range, version=u.version)
    strategy = StrategyRegistry.get(config.active_strategy, config)
    engine = MomentumEngine(
        universe=universe,
        market_data=BacktestMarketDataAdapter(historical),
        momentum_config=config.pure_momentum,
        sizing_config=config.position_sizing,
        risk_config=config.risk,
        strategy=strategy,
        app_config=config,
        cached_data=historical,
    )
    target_weights, regime = engine.select_portfolio_with_regime(
        as_of_date=as_of_dt,
        portfolio_value=float(capital),
        profile_max_gold=config.regime.max_gold_allocation,
    )

    def latest_price(sym: str) -> float:
        df = historical.get(sym)
        return float(df["close"].iloc[-1]) if df is not None and len(df) else 0.0

    targets: List[TargetPosition] = []
    for sym, w in sorted(target_weights.items(), key=lambda kv: kv[1], reverse=True):
        px = latest_price(sym)
        tval = float(capital) * w
        qty = int(tval / px) if px > 0 else 0
        targets.append(TargetPosition(sym, w, tval, px, qty))

    # Orders vs current holdings (target qty - current qty).
    orders: List[Order] = []
    target_qty = {t.symbol: t.quantity for t in targets}
    for sym in sorted(set(target_qty) | set(holdings)):
        cur = int(holdings.get(sym, 0))
        tgt = int(target_qty.get(sym, 0))
        delta = tgt - cur
        if delta == 0:
            continue
        px = latest_price(sym)
        orders.append(Order(
            symbol=sym,
            action="BUY" if delta > 0 else "SELL",
            quantity=abs(delta),
            price=px,
            value=abs(delta) * px,
        ))

    return RebalancePlan(
        as_of=latest_ts.date(),
        regime=regime.regime.value if regime else "unknown",
        capital=float(capital),
        targets=targets,
        orders=orders,
    )
