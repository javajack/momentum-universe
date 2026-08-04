"""Fresh allocation plan — stateless "here is Rs X, what do I buy?".

The user never inputs existing holdings and never tracks a live portfolio.
This action takes a fresh momentum amount and/or a fresh swing amount and
returns one combined breakup — per-stock approx quantity, rupee value, stop
and (swing) rotation days — with three things fresh money actually needs:

  * OVERLAP netting  — flags any ticker that lands in BOTH sleeves, so the same
    name isn't unknowingly double-bought.
  * ADV fill-check   — flags any slot whose rupee size is a large % of the
    stock's 20-day average traded value (fill / market-impact risk).
  * REGIME hint      — one line from the current regime to inform the split.

It wraps the existing pure actions (plan_rebalance with empty holdings for the
momentum leg, swing_allocation_plan for the swing leg) — a separate menu option
from the holdings-based rebalance, by design.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple


@dataclass
class FreshAllocRow:
    sleeve: str                 # "momentum" | "swing"
    symbol: str
    detail: str                 # momentum: weight%   swing: strategy name
    quantity: int
    value: float
    stop: Optional[float] = None
    stop_pct: Optional[float] = None
    rotation_days: Optional[int] = None
    adv_pct: Optional[float] = None
    adv_warn: bool = False
    overlap: bool = False


@dataclass
class FreshAllocationPlan:
    as_of: date
    regime: str
    regime_hint: str
    momentum_capital: float
    swing_capital: float
    momentum_rows: List[FreshAllocRow] = field(default_factory=list)
    swing_rows: List[FreshAllocRow] = field(default_factory=list)
    defensive_rows: List[FreshAllocRow] = field(default_factory=list)  # gold/cash buffer
    overlaps: List[str] = field(default_factory=list)
    momentum_cash: float = 0.0
    swing_cash: float = 0.0


# ---------------------------------------------------------------------------
# pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def _overlap(momentum_syms, swing_syms) -> set:
    return set(momentum_syms) & set(swing_syms)


def _adv_warn(value: float, adv: float, max_frac: float = 0.10) -> Tuple[Optional[float], bool]:
    """A slot's rupee value vs the stock's avg daily traded value. Returns
    (fraction_of_adv, warn). Missing/zero ADV -> (None, False)."""
    if not adv or adv <= 0:
        return None, False
    pct = value / adv
    return pct, pct > max_frac


def _regime_hint(regime: str) -> str:
    r = (regime or "").lower()
    if r in ("defensive", "caution"):
        return (f"Regime {r.upper()}: momentum is auto-throttled (gold/cash "
                "overlay) and whippier — consider tilting the split toward the "
                "swing sleeve (mean-reversion holds up better in stress).")
    return (f"Regime {r.upper()}: trend-following favourable — the momentum "
            "sleeve carries full equity here.")


def build_fresh_allocation(
    *,
    momentum_rows: List[FreshAllocRow],
    swing_rows: List[FreshAllocRow],
    adv_map: Dict[str, float],
    as_of: date,
    regime: str,
    momentum_capital: float,
    swing_capital: float,
    momentum_cash: float,
    swing_cash: float,
    defensive_rows: Optional[List[FreshAllocRow]] = None,
    adv_frac: float = 0.10,
) -> FreshAllocationPlan:
    """Pure assembler: tag overlap + ADV flags, attach regime hint."""
    overlaps = _overlap([r.symbol for r in momentum_rows],
                        [r.symbol for r in swing_rows])
    for r in momentum_rows + swing_rows:
        r.overlap = r.symbol in overlaps
        r.adv_pct, r.adv_warn = _adv_warn(r.value, adv_map.get(r.symbol, 0.0), adv_frac)
    return FreshAllocationPlan(
        as_of=as_of, regime=regime, regime_hint=_regime_hint(regime),
        momentum_capital=momentum_capital, swing_capital=swing_capital,
        momentum_rows=momentum_rows, swing_rows=swing_rows,
        defensive_rows=defensive_rows or [],
        overlaps=sorted(overlaps), momentum_cash=momentum_cash, swing_cash=swing_cash,
    )


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def fresh_allocation(
    config,
    momentum_capital: float = 0.0,
    swing_capital: float = 0.0,
    *,
    momentum_top_n: Optional[int] = None,
    hb_slots: int = 3,
    rsi_slots: int = 2,
    as_of: Optional[date] = None,
    config_path: str = "config.yaml",
    adv_frac: float = 0.10,
) -> FreshAllocationPlan:
    """Build a fresh combined allocation for the entered amounts (no holdings).

    momentum_capital / swing_capital: rupees to deploy in each sleeve (either
    may be 0 to skip that sleeve).
    """
    from datetime import timedelta

    from fortress.actions.market_state import current_market_state
    from fortress.actions.rebalance import plan_rebalance
    from fortress.actions.swing_allocation import swing_allocation_plan
    from fortress.nse_data_loader import load_historical_bulk

    ms = current_market_state(config)
    regime = ms.regime
    as_of_d = as_of or ms.as_of

    # gold/cash defensive ETFs from the regime overlay are shown separately,
    # not as equity buy-rows (their quantities aren't priced from equity data).
    defensive_syms = {config.regime.gold_symbol, config.regime.cash_symbol}

    momentum_rows: List[FreshAllocRow] = []
    defensive_rows: List[FreshAllocRow] = []
    momentum_cash = 0.0
    if momentum_capital > 0:
        plan = plan_rebalance(config, momentum_capital, holdings={}, top_n=momentum_top_n)
        deployed = 0.0
        for t in plan.targets:
            row = FreshAllocRow(
                sleeve="momentum", symbol=t.symbol, detail=f"{t.weight:.0%}",
                quantity=t.quantity, value=t.target_value)
            if t.symbol in defensive_syms:
                row.sleeve = "defensive"
                defensive_rows.append(row)
            else:
                momentum_rows.append(row)
            deployed += t.target_value
        momentum_cash = max(0.0, momentum_capital - deployed)

    swing_rows: List[FreshAllocRow] = []
    swing_cash = 0.0
    if swing_capital > 0:
        sp = swing_allocation_plan(swing_capital, hb_slots=hb_slots, rsi_slots=rsi_slots,
                                   as_of=as_of, config_path=config_path)
        for s in sp.slots:
            if not s.ticker:
                continue
            swing_rows.append(FreshAllocRow(
                sleeve="swing", symbol=s.ticker, detail=s.strategy,
                quantity=s.quantity, value=s.allocation,
                stop=s.suggested_stop, stop_pct=s.stop_pct, rotation_days=s.time_stop_days))
        swing_cash = sp.cash_reserve

    # ADV map for the union of tickers (20-day average traded value)
    syms = list({r.symbol for r in momentum_rows + swing_rows})
    adv_map: Dict[str, float] = {}
    if syms:
        end = as_of_d
        data = load_historical_bulk(start=end - timedelta(days=60), end=end, symbols=syms)
        for sym, df in data.items():
            if df is not None and len(df) >= 20:
                adv_map[sym] = float((df["close"] * df["volume"]).iloc[-20:].mean())

    return build_fresh_allocation(
        momentum_rows=momentum_rows, swing_rows=swing_rows, adv_map=adv_map,
        defensive_rows=defensive_rows, as_of=as_of_d, regime=regime,
        momentum_capital=momentum_capital, swing_capital=swing_capital,
        momentum_cash=momentum_cash, swing_cash=swing_cash, adv_frac=adv_frac,
    )
