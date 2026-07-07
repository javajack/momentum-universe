"""Swing allocation plan — the ₹-partitioned deployment of the two adopted
swing scanners in one account (nightlog Part 13).

The bake-off's slot-partition study showed a FIXED partition of
high_base_52w x3 slots + rsi2_pullback x2 slots beats a shared competing
pool and either scanner solo on the same capital (slot caps act as
signal-quality filters because entries are rank-ordered). This action turns
that finding into an actionable order plan: given an overall allocation
amount, it attributes equal-sized slots to each scanner's top-ranked
candidates and reports quantity / ₹ allocation / stop / rotation days.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional


@dataclass
class SwingSlot:
    strategy: str            # "high_base_52w" | "rsi2_pullback"
    slot: int                # 1-based within the strategy's partition
    ticker: Optional[str]    # None = no affordable candidate -> hold cash
    close: float = 0.0
    quantity: int = 0
    allocation: float = 0.0  # quantity * close
    suggested_stop: float = 0.0
    stop_pct: float = 0.0
    time_stop_days: int = 0  # forced-rotation horizon (trading days)


@dataclass
class SwingAllocationPlan:
    as_of: date
    capital: float
    per_trade: float
    hb_slots: int
    rsi_slots: int
    slots: List[SwingSlot] = field(default_factory=list)
    total_allocated: float = 0.0
    cash_reserve: float = 0.0


def _fill_partition(
    strategy: str, candidates: List[dict], n_slots: int,
    per_trade: float, time_stop: int,
) -> List[SwingSlot]:
    """Assign top-ranked affordable candidates to the partition's slots."""
    slots: List[SwingSlot] = []
    pool = list(candidates)
    for slot_no in range(1, n_slots + 1):
        pick = None
        while pool:
            cand = pool.pop(0)
            qty = math.floor(per_trade / cand["close"]) if cand["close"] > 0 else 0
            if qty > 0:
                pick = (cand, qty)
                break
        if pick is None:
            slots.append(SwingSlot(strategy=strategy, slot=slot_no, ticker=None,
                                   time_stop_days=time_stop))
            continue
        cand, qty = pick
        slots.append(SwingSlot(
            strategy=strategy, slot=slot_no, ticker=cand["ticker"],
            close=float(cand["close"]), quantity=qty,
            allocation=qty * float(cand["close"]),
            suggested_stop=float(cand.get("suggested_stop", 0.0)),
            stop_pct=float(cand.get("stop_pct", 0.0)),
            time_stop_days=time_stop,
        ))
    return slots


def build_swing_allocation(
    *,
    hb_candidates: List[dict],
    rsi_candidates: List[dict],
    capital: float,
    hb_slots: int = 3,
    rsi_slots: int = 2,
    as_of: Optional[date] = None,
    hb_time_stop: int = 30,
    rsi_time_stop: int = 20,
) -> SwingAllocationPlan:
    """Pure builder: partition `capital` into equal slots and attribute each
    scanner's top-ranked candidates. Candidates priced above one slot are
    skipped in favour of the next-ranked name."""
    n = hb_slots + rsi_slots
    per_trade = capital / n if n else 0.0
    slots = _fill_partition("high_base_52w", hb_candidates, hb_slots,
                            per_trade, hb_time_stop)
    slots += _fill_partition("rsi2_pullback", rsi_candidates, rsi_slots,
                             per_trade, rsi_time_stop)
    total = sum(s.allocation for s in slots)
    return SwingAllocationPlan(
        as_of=as_of or date.today(), capital=capital, per_trade=per_trade,
        hb_slots=hb_slots, rsi_slots=rsi_slots, slots=slots,
        total_allocated=total, cash_reserve=capital - total,
    )


def swing_allocation_plan(
    capital: float,
    *,
    hb_slots: int = 3,
    rsi_slots: int = 2,
    as_of: Optional[date] = None,
    config_path: str = "config.yaml",
) -> SwingAllocationPlan:
    """Run both live scanners as of `as_of` and build the partitioned plan.

    Time stops come from each scanner's own configured defaults, so the
    rotation guidance always matches what the scanner would actually do.
    """
    from fortress.actions.swing import run_high_base_scan, run_ryner_scan
    from tools.high_base_scan import DEFAULTS as HB_DEFAULTS
    from tools.ryner_pullback_scan import DEFAULTS as RS_DEFAULTS

    hb = run_high_base_scan(as_of=as_of, top=hb_slots + 5, config_path=config_path)
    rs = run_ryner_scan(as_of=as_of, top=rsi_slots + 5, config_path=config_path)
    return build_swing_allocation(
        hb_candidates=hb, rsi_candidates=rs, capital=capital,
        hb_slots=hb_slots, rsi_slots=rsi_slots, as_of=as_of,
        hb_time_stop=int(HB_DEFAULTS["time_stop_days"]),
        rsi_time_stop=int(RS_DEFAULTS["time_stop_days"]),
    )
