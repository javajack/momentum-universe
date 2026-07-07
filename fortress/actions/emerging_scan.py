"""Emerging-momentum scan — find stocks EARLY in a momentum move, not late.

The `momentum_scan` action ranks by absolute momentum score, so it surfaces
names that have already run (up 150-250%). This scan instead looks for the
*earlier* part of the curve by combining two signals a single snapshot can't
show:

  1. LIQUIDITY RANK CLIMBING — using the point-in-time universe, compare each
     stock's turnover rank ~2y ago / ~1y ago / now. A stock climbing from
     rank ~800 to ~300 means money is flowing in before the crowd notices.
  2. EARLY PRICE MOMENTUM — above the 200-day SMA, breaking toward its 52-week
     high, recent 3-month leg accelerating, but 12-month return capped (so the
     already-parabolic names are excluded).

Plus artifact guards (bad-tick data) and a liquidity floor. The result is a
ranked shortlist of "just-emerging" candidates for LLM diligence to pressure-
test (see llms.txt: the intended workflow is emerging_scan -> per-ticker
fundamental/shareholding/news diligence).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

from fortress.config import Config


@dataclass(frozen=True)
class Thresholds:
    band_lo: int = 150            # current-rank band to scan
    band_hi: int = 650
    min_climb_2y: int = 60        # min 2y rank improvement to count as emerging
    r12_lo: float = 0.08          # 12m return band: started...
    r12_hi: float = 0.80          # ...but NOT already parabolic
    prox_lo: float = 0.85         # near breakout (fraction of 52w high)
    min_turnover: float = 5e7     # >= Rs 5 cr/day
    max_vol: float = 0.75         # drop the wildest
    max_dist200_pct: float = 150.0  # artifact guard (bad-tick)
    max_return: float = 2.5       # artifact guard on any lookback return


DEFAULT_THRESHOLDS = Thresholds()


@dataclass
class EmergingRow:
    symbol: str
    sector: str
    rank_now: int
    rank_1y: Optional[int]
    rank_2y: Optional[int]
    climb: int              # liquidity-rank climb signal (higher = stronger)
    new_entrant: bool
    price: float
    ret_3m_pct: float
    ret_6m_pct: float
    ret_12m_pct: float
    prox_52w_high: float
    accel_pct: float        # momentum-of-momentum (recent-leg strength)
    volatility: float
    daily_turnover: float
    score: float = 0.0


@dataclass
class EmergingScanResult:
    version: str
    as_of: date
    band: Tuple[int, int]
    candidates_scanned: int
    total_passing: int
    stocks: List[EmergingRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def _price_metrics(df) -> Optional[Dict]:
    """Technical metrics from a daily OHLCV frame; None if < 200 bars."""
    c = df["close"]
    n = len(c)
    if n < 200:
        return None
    px = float(c.iloc[-1])

    def ret(bars: int) -> Optional[float]:
        return float(c.iloc[-1] / c.iloc[-1 - bars] - 1) if n > bars else None

    sma200 = float(c.iloc[-200:].mean())
    high_252 = float(c.iloc[-252:].max()) if n >= 252 else float(c.max())
    logret = np.log(c / c.shift(1)).dropna()
    vol = float(logret.iloc[-126:].std() * np.sqrt(252)) if len(logret) >= 20 else None
    turnover = float((c * df["volume"]).iloc[-20:].mean()) if n >= 20 else 0.0
    return {
        "px": px,
        "above200": px > sma200,
        "dist200_pct": (px / sma200 - 1) * 100 if sma200 else 0.0,
        "prox": px / high_252 if high_252 else None,
        "r3": ret(63), "r6": ret(126), "r12": ret(252),
        "vol": vol, "turnover": turnover,
    }


def _passes_early_filters(m: Dict, t: Thresholds) -> bool:
    """True if the metrics describe an EARLY-stage, clean momentum mover."""
    for k in ("r3", "r6", "r12", "prox"):
        if m.get(k) is None:
            return False
    if abs(m["dist200_pct"]) > t.max_dist200_pct:
        return False
    if max(m["r3"], m["r6"], m["r12"]) > t.max_return:
        return False
    if not m["above200"]:
        return False
    if not (t.prox_lo <= m["prox"] <= 1.001):
        return False
    if not (t.r12_lo <= m["r12"] <= t.r12_hi):     # started, but not parabolic
        return False
    if m["r3"] <= 0:                                # recent leg must be up
        return False
    if m["turnover"] < t.min_turnover:
        return False
    if m["vol"] is not None and m["vol"] > t.max_vol:
        return False
    return True


def _climb_signal(rank_now: int, rank_y1: Optional[int], rank_y2: Optional[int]) -> int:
    """Liquidity-rank climb: how strongly turnover-rank is rising.

    - present 2y ago: full 2y improvement (rank_y2 - rank_now).
    - entered within 2y but present 1y ago and climbing: 1y improvement + entry bonus.
    - brand-new entrant (<1y): a modest positive (still emerging, but unproven).
    """
    if rank_y2 is not None:
        return int(rank_y2 - rank_now)
    if rank_y1 is not None:
        return int((rank_y1 - rank_now) + 150)
    return 120


def _score(rows: List[EmergingRow]) -> None:
    """Z-scored composite; mutates rows in place, sets .score."""
    if not rows:
        return
    def z(vals):
        a = np.array(vals, dtype=float)
        s = a.std()
        return (a - a.mean()) / (s if s else 1.0)
    climb = z([min(r.climb, 450) for r in rows])
    accel = z([r.accel_pct for r in rows])
    prox = z([r.prox_52w_high for r in rows])
    r12 = z([r.ret_12m_pct for r in rows])
    for i, r in enumerate(rows):
        # reward climbing liquidity + accelerating + near breakout; PREFER earlier
        r.score = float(1.1 * climb[i] + 1.1 * accel[i] + 0.8 * prox[i] - 0.7 * r12[i])


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def emerging_scan(
    config: Config,
    top_n: int = 20,
    as_of: Optional[date] = None,
    lookback_years: int = 2,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> EmergingScanResult:
    """Scan for stocks EARLY in a momentum move (rank climbing + early price
    momentum) on the configured universe. Pure: no prompts, no broker."""
    import json

    from nse_universe import Universe
    from fortress.nse_data_loader import load_historical_bulk

    version = config.universe.version
    u = Universe(version=version)
    last = u.health()["last_date"]
    ref = as_of or last
    y1 = ref - timedelta(days=365)
    y2 = ref - timedelta(days=365 * lookback_years)

    snap_now = u.universe_at(ref).set_index("symbol")["rank"]
    snap_y1 = u.universe_at(y1).set_index("symbol")["rank"]
    snap_y2 = u.universe_at(y2).set_index("symbol")["rank"]

    try:
        sectors = json.load(open(config.paths.sectors_file))["symbols"]
    except Exception:
        sectors = {}

    def _sec(sym: str) -> str:
        s = sectors.get(sym)
        return (s.get("sector") if isinstance(s, dict) else s) or "?"

    def _present(r) -> bool:
        return r is not None and not (isinstance(r, float) and np.isnan(r))

    # Pre-filter to liquidity-emerging names before loading prices.
    band = snap_now[(snap_now >= thresholds.band_lo) & (snap_now <= thresholds.band_hi)]
    pre: List[Dict] = []
    for sym, r_now in band.items():
        r_y1, r_y2 = snap_y1.get(sym), snap_y2.get(sym)
        has_y1, has_y2 = _present(r_y1), _present(r_y2)
        new_entrant = not has_y2
        if new_entrant or (has_y2 and (r_y2 - r_now) >= thresholds.min_climb_2y):
            pre.append({
                "symbol": sym, "rank_now": int(r_now),
                "rank_y1": int(r_y1) if has_y1 else None,
                "rank_y2": int(r_y2) if has_y2 else None,
                "new_entrant": new_entrant,
                "climb": _climb_signal(int(r_now), int(r_y1) if has_y1 else None,
                                       int(r_y2) if has_y2 else None),
                "sector": _sec(sym),
            })

    syms = [p["symbol"] for p in pre]
    prices = load_historical_bulk(start=ref - timedelta(days=460), end=ref, symbols=syms)

    rows: List[EmergingRow] = []
    for p in pre:
        df = prices.get(p["symbol"])
        if df is None or df.empty:
            continue
        m = _price_metrics(df)
        if m is None or not _passes_early_filters(m, thresholds):
            continue
        rows.append(EmergingRow(
            symbol=p["symbol"], sector=p["sector"], rank_now=p["rank_now"],
            rank_1y=p["rank_y1"], rank_2y=p["rank_y2"], climb=p["climb"],
            new_entrant=p["new_entrant"], price=m["px"],
            ret_3m_pct=m["r3"] * 100, ret_6m_pct=m["r6"] * 100, ret_12m_pct=m["r12"] * 100,
            prox_52w_high=m["prox"], accel_pct=(2 * m["r3"] - m["r6"]) * 100,
            volatility=m["vol"] or 0.0, daily_turnover=m["turnover"],
        ))

    _score(rows)
    rows.sort(key=lambda r: r.score, reverse=True)
    return EmergingScanResult(
        version=version, as_of=ref, band=(thresholds.band_lo, thresholds.band_hi),
        candidates_scanned=len(pre), total_passing=len(rows), stocks=rows[:top_n],
    )
