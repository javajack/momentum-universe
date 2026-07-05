"""Swing-strategy engine + the two adopted strategies.

Runs swing strategies on the [201, 600] mid/smallcap PIT universe over a
multi-year window and reports per-trade / summary metrics across cost levels.

Originally a six-strategy bake-off (see the 2026-05-27 spec); after the
comparison, only the two adopted survivors are retained here — `rsi2_pullback`
(Ryner / Option-S) and `high_base_52w` (Option-V) — plus the shared engine
that `tools/high_base_scan.py` reuses. The other four candidates were dropped
in the 2026-07 strategy cleanup (recover from git history if a re-run is
needed). No CLI / Kite integration.
"""
from __future__ import annotations

import argparse
import csv
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


COST_LEVELS: Dict[str, float] = {
    "20bp": 0.0020,
    "35bp": 0.0035,
    "60bp": 0.0060,
}


@dataclass
class Trade:
    strategy: str
    ticker: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    stop_price: float
    pnl_pct: float
    pnl_inr_gross: float
    hold_days: int
    exit_reason: str  # "stop" | "signal" | "time" | "eop"


class SwingStrategy(ABC):
    """Abstract base for a swing strategy. Implementations precompute any
    strategy-specific columns into each DataFrame, then answer per-bar
    entry/exit/stop queries."""

    name: str = "base"

    @abstractmethod
    def precompute(self, prices: Dict[str, pd.DataFrame]) -> None:
        ...

    @abstractmethod
    def should_enter(self, df: pd.DataFrame, today: pd.Timestamp) -> bool:
        ...

    @abstractmethod
    def should_exit(self, entry: dict, df: pd.DataFrame,
                     today: pd.Timestamp, days_held: int) -> Optional[str]:
        ...

    @abstractmethod
    def entry_stop(self, close: float, atr_val: float) -> float:
        ...

    def rank_key(self, df: pd.DataFrame, today: pd.Timestamp) -> float:
        """Lower = picked first when more signals fire than open slots.
        Default: zero (FIFO by ticker iteration order)."""
        return 0.0


def _rsi_wilder(close: pd.Series, period: int = 2) -> pd.Series:
    """Wilder-smoothed RSI. Mirrors tools.ryner_pullback_scan.rsi()."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    saturated = (avg_loss == 0) & (avg_gain > 0)
    return out.where(~saturated, 100.0)


def _atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series,
                 period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_shared_indicators(prices: Dict[str, pd.DataFrame]) -> None:
    """Add SMA/EMA/ATR/RSI/volume/52w-high/range/BB-bandwidth columns to
    each price DataFrame in-place. Idempotent — safe to call multiple times.
    """
    for df in prices.values():
        if df is None or len(df) == 0:
            continue
        if "sma_200" in df.columns:
            continue
        c = df["close"]
        h = df.get("high", c)
        low = df.get("low", c)
        v = df.get("volume", pd.Series([1.0] * len(df), index=df.index))

        df["sma_200"] = c.rolling(200).mean()
        df["sma_50"] = c.rolling(50).mean()
        df["sma_20"] = c.rolling(20).mean()
        df["sma_5"] = c.rolling(5).mean()
        df["ema_21"] = c.ewm(span=21, adjust=False).mean()
        df["atr_14"] = _atr_wilder(h, low, c, 14)
        df["rsi_2"] = _rsi_wilder(c, 2)
        df["avg_vol_20"] = v.rolling(20).mean()
        df["high_252"] = c.rolling(252).max()
        df["low_20"] = c.rolling(20).min()
        df["high_20"] = c.rolling(20).max()
        df["range_20"] = (df["high_20"] - df["low_20"]) / c
        std_20 = c.rolling(20).std()
        df["bb_upper"] = df["sma_20"] + 2 * std_20
        df["bb_lower"] = df["sma_20"] - 2 * std_20
        df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["sma_20"]
        df["bb_bw_pct_120"] = df["bb_bandwidth"].rolling(120).rank(pct=True)
        df["ret_60"] = c / c.shift(60) - 1
        df["low_10"] = c.rolling(10).min()
        df["close_prev"] = c.shift(1)


class RSI2Pullback(SwingStrategy):
    """Connors / Ryner RSI(2) pullback (control).

    Entry: close > 200-SMA AND close < 5-SMA AND RSI(2) <= 7
    Exit:  close > 5-SMA OR RSI(2) >= 70
    Stop:  entry - 1.5 * ATR(14)
    Time:  10 trading days
    Rank:  RSI(2) ascending (deepest oversold first)
    """
    name = "rsi2_pullback"

    MIN_PRICE = 50.0
    MIN_VOL = 200_000
    RSI_ENTRY_MAX = 7.0
    RSI_EXIT_MIN = 70.0
    MAX_DIST_ABOVE_SMA200 = 0.30
    ATR_STOP_MULT = 1.5
    TIME_STOP = 10

    def precompute(self, prices: Dict[str, pd.DataFrame]) -> None:
        pass

    def should_enter(self, df: pd.DataFrame, today: pd.Timestamp) -> bool:
        if today not in df.index:
            return False
        r = df.loc[today]
        for col in ("close", "sma_200", "sma_5", "rsi_2", "atr_14", "avg_vol_20"):
            if pd.isna(r.get(col)):
                return False
        c = r["close"]
        if c < self.MIN_PRICE or r["avg_vol_20"] < self.MIN_VOL:
            return False
        if c < r["sma_200"]:
            return False
        if c >= r["sma_5"]:
            return False
        if r["rsi_2"] > self.RSI_ENTRY_MAX:
            return False
        if (c / r["sma_200"] - 1) > self.MAX_DIST_ABOVE_SMA200:
            return False
        return True

    def should_exit(self, entry: dict, df: pd.DataFrame,
                     today: pd.Timestamp, days_held: int) -> Optional[str]:
        if today not in df.index:
            return None
        r = df.loc[today]
        c = float(r["close"])
        if pd.notna(r.get("rsi_2")) and r["rsi_2"] >= self.RSI_EXIT_MIN:
            return "signal"
        if pd.notna(r.get("sma_5")) and c > r["sma_5"]:
            return "signal"
        if days_held >= self.TIME_STOP:
            return "time"
        return None

    def entry_stop(self, close: float, atr_val: float) -> float:
        return close - self.ATR_STOP_MULT * atr_val

    def rank_key(self, df: pd.DataFrame, today: pd.Timestamp) -> float:
        return float(df.loc[today, "rsi_2"])


class HighBaseBreakout52w(SwingStrategy):
    """52-week high + tight base (Minervini VCP-lite).

    Entry: close >= 0.97 * 52w_high AND 20d range < 10% of price
    Exit:  close < 21-EMA
    Stop:  entry - 3.0 * ATR(14)
    Time:  30 trading days
    Rank:  (52w_high - close) / close ascending (closest to pivot first)
    """
    name = "high_base_52w"

    MIN_PRICE = 50.0
    MIN_VOL = 200_000
    NEAR_HIGH_PCT = 0.97
    MAX_RANGE_20 = 0.10
    ATR_STOP_MULT = 3.0
    TIME_STOP = 30

    def precompute(self, prices: Dict[str, pd.DataFrame]) -> None:
        pass

    def should_enter(self, df: pd.DataFrame, today: pd.Timestamp) -> bool:
        if today not in df.index:
            return False
        r = df.loc[today]
        for col in ("close", "high_252", "range_20", "avg_vol_20", "atr_14"):
            if pd.isna(r.get(col)):
                return False
        c = r["close"]
        if c < self.MIN_PRICE or r["avg_vol_20"] < self.MIN_VOL:
            return False
        if c < self.NEAR_HIGH_PCT * r["high_252"]:
            return False
        if r["range_20"] > self.MAX_RANGE_20:
            return False
        return True

    def should_exit(self, entry: dict, df: pd.DataFrame,
                     today: pd.Timestamp, days_held: int) -> Optional[str]:
        if today not in df.index:
            return None
        r = df.loc[today]
        c = float(r["close"])
        if pd.notna(r.get("ema_21")) and c < r["ema_21"]:
            return "signal"
        if days_held >= self.TIME_STOP:
            return "time"
        return None

    def entry_stop(self, close: float, atr_val: float) -> float:
        return close - self.ATR_STOP_MULT * atr_val

    def rank_key(self, df: pd.DataFrame, today: pd.Timestamp) -> float:
        r = df.loc[today]
        return float((r["high_252"] - r["close"]) / r["close"])


ALL_STRATEGIES = [
    RSI2Pullback, HighBaseBreakout52w,
]


def _next_trading_day_index(df: pd.DataFrame, today: pd.Timestamp) -> Optional[int]:
    """Return positional index of the next bar after `today` in df, or None."""
    if today not in df.index:
        return None
    pos = df.index.get_loc(today)
    if pos + 1 >= len(df):
        return None
    return pos + 1


# Default v3 regime-gate config — mirrors Ryner production
# (tools/ryner_pullback_scan.py DEFAULTS, the regime_* keys).
V3_GATE_CFG: Dict = {
    "trend_sma_period":              200,
    "regime_min_breadth":            0.50,
    "regime_min_breadth_slope_floor": 0.20,
    "regime_slope_min":              5.0,
    "regime_sector_min":             10.0,
    "regime_vix_relax_breadth":      0.40,
    "regime_slope_window":           10,
    "regime_breadth_smoothing":      1,
    "vix_sma_period":                21,
    "vix_max_calm_level":            22.0,
}


def build_v3_gate_series(
    prices: Dict[str, pd.DataFrame],
    membership: Dict[date, set],
    cfg: Dict | None = None,
) -> pd.Series:
    """Build the v3 regime-gate Series (PIT, date-indexed boolean).

    Returns a Series whose value is True on each trading day where the v3
    OR-gate says "allow new entries" — four paths: strong breadth, breadth
    recovering, sectors risk-on, calm VIX. Mirrors the gate Ryner uses live.

    Reuses tools.ryner_backtest._precompute_breadth (PIT-aware, per-day
    membership) and tools.ryner_regime.* primitives — no duplication.
    """
    from tools.ryner_backtest import _precompute_breadth
    from tools.ryner_regime import (
        compute_breadth_slope, compute_sector_breadth_ratio,
        compute_vix_trend, load_sectors_map, load_vix, combine_signals,
    )

    gate_cfg = dict(V3_GATE_CFG)
    if cfg:
        gate_cfg.update(cfg)

    # ryner_backtest._precompute_breadth expects column `sma_trend`. Bake-off
    # uses `sma_200`. Add the alias view in-place (zero-copy via assignment).
    for df in prices.values():
        if df is None or "sma_200" not in df.columns:
            continue
        if "sma_trend" not in df.columns:
            df["sma_trend"] = df["sma_200"]

    breadth = _precompute_breadth(prices, membership, gate_cfg)
    if breadth.empty:
        return pd.Series(dtype=bool)
    slope = compute_breadth_slope(breadth, gate_cfg["regime_slope_window"])
    sectors_map = load_sectors_map()
    sector_ratio = compute_sector_breadth_ratio(
        prices, list(prices.keys()), gate_cfg, sectors_map,
    )
    vix_df = load_vix()
    vix_calm = compute_vix_trend(
        vix_df,
        sma_period=gate_cfg["vix_sma_period"],
        max_calm_level=gate_cfg["vix_max_calm_level"],
    )
    return combine_signals(
        breadth=breadth, breadth_slope=slope,
        sector_ratio=sector_ratio, vix_calm=vix_calm, cfg=gate_cfg,
    )


def run_strategy(
    *,
    strategy: SwingStrategy,
    prices: Dict[str, pd.DataFrame],
    membership: Dict[date, set],
    start: date,
    end: date,
    max_concurrent: int = 5,
    capital_per_trade: float = 100_000.0,
    gate_open_series: Optional[pd.Series] = None,
) -> List[Trade]:
    """Run one strategy on the given PIT universe. Returns closed Trades.

    Execution model:
      - Signal evaluated on close of day D (strategy.should_enter/exit)
      - Fills at OPEN of day D+1
      - Stops: detected on close (close <= stop_price), filled at
        min(stop, next_day_open) to honour gap-down
      - Open positions persist through universe drop-outs
    """
    all_dates = sorted({
        ts.normalize() for df in prices.values()
        for ts in (df.index if df is not None else [])
        if start <= ts.date() <= end
    })
    if not all_dates:
        return []

    open_pos: Dict[str, dict] = {}
    trades: List[Trade] = []

    for i, today in enumerate(all_dates):
        # 1. Exits on today's close (fill at next day's open)
        for t in list(open_pos.keys()):
            df = prices.get(t)
            if df is None or today not in df.index:
                continue
            entry = open_pos[t]
            days_held = i - entry["entry_idx"]
            r = df.loc[today]
            c = float(r["close"])
            reason = None
            fill_price = None
            if c <= entry["stop"]:
                reason = "stop"
                nx = _next_trading_day_index(df, today)
                if nx is None:
                    fill_price = c
                else:
                    nxt_open = float(df.iloc[nx]["open"])
                    fill_price = min(entry["stop"], nxt_open)
            else:
                signal_reason = strategy.should_exit(entry, df, today, days_held)
                if signal_reason is not None:
                    reason = signal_reason
                    nx = _next_trading_day_index(df, today)
                    if nx is None:
                        fill_price = c
                    else:
                        fill_price = float(df.iloc[nx]["open"])
            if reason is None:
                continue
            entry_p = entry["entry_price"]
            pnl_pct = (fill_price / entry_p - 1) * 100
            pnl_inr = capital_per_trade * (fill_price / entry_p - 1)
            nx = _next_trading_day_index(df, today)
            exit_dt = (df.iloc[nx].name.date() if nx is not None
                       else today.date())
            trades.append(Trade(
                strategy=strategy.name, ticker=t,
                entry_date=entry["entry_date"], exit_date=exit_dt,
                entry_price=entry_p, exit_price=fill_price,
                stop_price=entry["stop"], pnl_pct=pnl_pct,
                pnl_inr_gross=pnl_inr, hold_days=days_held,
                exit_reason=reason,
            ))
            del open_pos[t]

        # 2. New entries — restrict to today's PIT members
        # Gate check first: if regime gate is closed today, skip new entries
        # entirely. Open positions still exit normally above.
        if gate_open_series is not None and today in gate_open_series.index:
            if not bool(gate_open_series.loc[today]):
                continue
        members_today = membership.get(today.date())
        if members_today is None:
            for back in range(1, 8):
                prior = (today - pd.Timedelta(days=back)).date()
                if prior in membership:
                    members_today = membership[prior]
                    break
        if not members_today:
            continue
        if len(open_pos) >= max_concurrent:
            continue
        slots = max_concurrent - len(open_pos)
        candidates = []
        for t in members_today:
            if t in open_pos:
                continue
            df = prices.get(t)
            if df is None:
                continue
            if strategy.should_enter(df, today):
                key = strategy.rank_key(df, today)
                candidates.append((key, t))
        candidates.sort()
        for _key, t in candidates[:slots]:
            df = prices.get(t)
            nx = _next_trading_day_index(df, today)
            if nx is None:
                continue
            nxt = df.iloc[nx]
            entry_price = float(nxt["open"])
            atr_val = float(df.loc[today, "atr_14"])
            stop = strategy.entry_stop(entry_price, atr_val)
            open_pos[t] = {
                "entry_date": df.iloc[nx].name.date(),
                "entry_idx": i + 1,
                "entry_price": entry_price,
                "stop": stop,
            }

    # 3. EOP exits
    last = all_dates[-1]
    for t, entry in open_pos.items():
        df = prices.get(t)
        if df is None or last not in df.index:
            continue
        c = float(df.loc[last, "close"])
        entry_p = entry["entry_price"]
        pnl_pct = (c / entry_p - 1) * 100
        pnl_inr = capital_per_trade * (c / entry_p - 1)
        days_held = len(all_dates) - 1 - entry["entry_idx"]
        trades.append(Trade(
            strategy=strategy.name, ticker=t,
            entry_date=entry["entry_date"], exit_date=last.date(),
            entry_price=entry_p, exit_price=c, stop_price=entry["stop"],
            pnl_pct=pnl_pct, pnl_inr_gross=pnl_inr,
            hold_days=max(0, days_held), exit_reason="eop",
        ))
    return trades


def _equity_curve(trades: List[Trade], cost_per_trade: float) -> pd.Series:
    """Per-day total ₹ P&L summed across positions exiting that day, after
    deducting cost on each closed trade. Cumulated to an equity series."""
    if not trades:
        return pd.Series(dtype=float)
    daily = pd.Series(0.0, index=pd.DatetimeIndex(
        sorted({pd.Timestamp(t.exit_date) for t in trades})
    ))
    for t in trades:
        daily.loc[pd.Timestamp(t.exit_date)] += t.pnl_inr_gross - cost_per_trade
    return daily.cumsum()


def _max_drawdown_pct(equity: pd.Series, base_capital: float) -> float:
    if equity.empty:
        return 0.0
    series = equity + base_capital
    peak = series.cummax()
    dd = (series - peak) / peak
    return float(dd.min() * 100)


def _sharpe(equity: pd.Series, base_capital: float) -> float:
    if len(equity) < 2:
        return 0.0
    levels = equity + base_capital
    rets = levels.pct_change().dropna()
    if rets.std() == 0 or len(rets) < 2:
        return 0.0
    return float(rets.mean() / rets.std() * np.sqrt(252))


def score_strategy(trades: List[Trade], cost_rate: float,
                    capital_per_trade: float) -> dict:
    """Compute summary metrics for one strategy at one cost level.

    `cost_rate` is the round-trip fraction (e.g. 0.0035 for 35 bps).
    """
    if not trades:
        return {"n_trades": 0, "win_rate": 0.0, "total_pnl_net": 0.0,
                "profit_factor_net": 0.0, "sharpe_net": 0.0,
                "max_drawdown_pct": 0.0, "avg_hold_days": 0.0,
                "exit_stop": 0, "exit_signal": 0, "exit_time": 0, "exit_eop": 0,
                "avg_pnl_pct_net": 0.0, "median_pnl_pct_net": 0.0,
                "best_trade_pct_net": 0.0, "worst_trade_pct_net": 0.0}
    cost_per_trade = capital_per_trade * cost_rate
    net_inrs = [t.pnl_inr_gross - cost_per_trade for t in trades]
    net_pcts = [(t.pnl_inr_gross - cost_per_trade) / capital_per_trade * 100
                 for t in trades]
    wins = [p for p in net_inrs if p > 0]
    losses = [p for p in net_inrs if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    equity = _equity_curve(trades, cost_per_trade)
    return {
        "n_trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_pnl_pct_net": float(np.mean(net_pcts)),
        "median_pnl_pct_net": float(np.median(net_pcts)),
        "best_trade_pct_net": float(max(net_pcts)),
        "worst_trade_pct_net": float(min(net_pcts)),
        "total_pnl_net": sum(net_inrs),
        "profit_factor_net": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "sharpe_net": _sharpe(equity, capital_per_trade * 5),
        "max_drawdown_pct": _max_drawdown_pct(equity, capital_per_trade * 5),
        "avg_hold_days": float(np.mean([t.hold_days for t in trades])),
        "exit_stop": sum(1 for t in trades if t.exit_reason == "stop"),
        "exit_signal": sum(1 for t in trades if t.exit_reason == "signal"),
        "exit_time": sum(1 for t in trades if t.exit_reason == "time"),
        "exit_eop": sum(1 for t in trades if t.exit_reason == "eop"),
    }


def _print_console_summary(rows: List[dict]) -> None:
    """Print a ranked table: each strategy at each cost level."""
    print()
    print("━" * 110)
    print(f"  {'Strategy':22s} {'Cost':>6s} {'N':>5s} "
          f"{'Win%':>6s} {'AvgP&L%':>8s} {'PF':>6s} "
          f"{'Sharpe':>7s} {'MaxDD%':>8s} {'Hold':>5s}")
    print("━" * 110)
    order = {"20bp": 0, "35bp": 1, "60bp": 2}
    rows_sorted = sorted(rows, key=lambda r: (r["strategy"], order[r["cost_level"]]))
    for r in rows_sorted:
        pf = r["profit_factor_net"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "  inf"
        print(f"  {r['strategy']:22s} {r['cost_level']:>6s} "
              f"{r['n_trades']:>5d} {r['win_rate']*100:>5.1f}% "
              f"{r['avg_pnl_pct_net']:>+7.2f}% {pf_str:>6s} "
              f"{r['sharpe_net']:>+6.2f} {r['max_drawdown_pct']:>+7.2f}% "
              f"{r['avg_hold_days']:>4.1f}d")
    print()
    print("  Ranked by PF @ 35bp (descending):")
    ranked = sorted(
        [r for r in rows if r["cost_level"] == "35bp"],
        key=lambda r: r["profit_factor_net"], reverse=True,
    )
    for i, r in enumerate(ranked, 1):
        pf = r["profit_factor_net"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"    {i}. {r['strategy']:22s}  PF {pf_str}  "
              f"Sharpe {r['sharpe_net']:+.2f}  "
              f"MaxDD {r['max_drawdown_pct']:+.1f}%  "
              f"Win {r['win_rate']*100:.1f}%  N={r['n_trades']}")


def run_bakeoff(
    *, start: date, end: date,
    strategies_subset: Optional[List[str]] = None,
    config_path: str = "config.yaml",
    cost_levels: Optional[Dict[str, float]] = None,
    gate_strategies: Optional[List[str]] = None,
    output_suffix: str = "",
) -> tuple:
    """Top-level driver. Loads prices once, builds PIT membership once,
    runs each strategy, returns (all_trades, summary_rows).

    `gate_strategies`: list of strategy names to apply the v3 regime gate to.
      Built once and shared across these strategies. Other strategies run
      ungated (signal-clean). Empty/None = no gating anywhere.
    `output_suffix`: appended to CSV filenames so gated/ungated runs don't
      overwrite each other.
    """
    from datetime import timedelta as _td
    from fortress.config import load_config
    from fortress.nse_data_loader import load_historical_for_backtest
    from tools.ryner_backtest import _build_membership_index

    cfg = load_config(config_path)
    rank_range = tuple(cfg.universe.rank_range)
    fetch_start = start - _td(days=400)
    levels = cost_levels or COST_LEVELS

    print(f"━━━ Swing-strategy bake-off ━━━")
    print(f"  Window     : {start} → {end}")
    print(f"  Universe   : v={cfg.universe.version}, rank_range={list(rank_range)} (PIT)")
    print(f"  Loading prices: {fetch_start} → {end} ...")
    prices = load_historical_for_backtest(
        start=fetch_start, end=end, rank_range=rank_range,
        version=cfg.universe.version,
    )
    print(f"  Loaded {len(prices)} symbols. Computing shared indicators ...")
    compute_shared_indicators(prices)

    print(f"  Building PIT membership index ...")
    membership = _build_membership_index(
        start=start, end=end, rank_range=rank_range,
        version=cfg.universe.version,
    )
    avg = sum(len(v) for v in membership.values()) / max(1, len(membership))
    print(f"  Membership: {len(membership)} trading days, avg {avg:.0f} symbols/day")

    gate_open_series: Optional[pd.Series] = None
    if gate_strategies:
        print(f"  Gating strategies: {gate_strategies}")
        print(f"  Building v3 regime gate (breadth + slope + sector + VIX) ...")
        gate_open_series = build_v3_gate_series(prices, membership)
        if not gate_open_series.empty:
            n_open = int(gate_open_series.sum())
            n_total = int(gate_open_series.notna().sum())
            print(f"  Gate: open on {n_open}/{n_total} days "
                  f"({n_open/n_total*100:.1f}% of days)")
        else:
            print(f"  Gate: WARNING — empty series (no breadth data?)")
            gate_open_series = None

    selected = [s for s in ALL_STRATEGIES
                 if strategies_subset is None or s().name in strategies_subset]
    all_trades = []
    summary_rows = []
    for strat_cls in selected:
        strat = strat_cls()
        gated = gate_strategies and strat.name in gate_strategies
        gate_tag = " [GATED]" if gated else ""
        print(f"\n  Running {strat.name}{gate_tag} ...")
        strat.precompute(prices)
        trades = run_strategy(
            strategy=strat, prices=prices, membership=membership,
            start=start, end=end, max_concurrent=5,
            capital_per_trade=100_000.0,
            gate_open_series=(gate_open_series if gated else None),
        )
        print(f"    {len(trades)} closed trades")
        all_trades.extend(trades)
        # Tag strategy name with gate suffix in output so it's distinguishable
        out_name = f"{strat.name}_gated" if gated else strat.name
        for t in trades:
            t.strategy = out_name
        for level_name, rate in levels.items():
            score = score_strategy(trades, cost_rate=rate,
                                    capital_per_trade=100_000.0)
            summary_rows.append({"strategy": out_name,
                                  "cost_level": level_name,
                                  "cost_rate_bp": int(rate * 10_000),
                                  **score})

    plans_dir = REPO_ROOT / "plans"
    plans_dir.mkdir(exist_ok=True)
    suf = output_suffix
    trades_path = plans_dir / f"swing_bakeoff_trades_{start}_{end}{suf}.csv"
    with trades_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(Trade(
            "x", "x", start, start, 0, 0, 0, 0, 0, 0, "x")).keys()))
        w.writeheader()
        for t in all_trades:
            w.writerow(asdict(t))
    print(f"\n  Trades written : {trades_path}")

    summary_path = plans_dir / f"swing_bakeoff_summary_{start}_{end}{suf}.csv"
    if summary_rows:
        with summary_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            w.writeheader()
            for row in summary_rows:
                w.writerow(row)
        print(f"  Summary written: {summary_path}")

    _print_console_summary(summary_rows)
    return all_trades, summary_rows


def main() -> None:
    p = argparse.ArgumentParser(description="Swing-strategy bake-off")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--strategies", default=None,
                    help="Comma-separated subset of strategy names")
    p.add_argument("--gate-strategies", default=None,
                    help="Comma-separated strategy names to apply the v3 "
                         "regime gate to (breadth + slope + sector + VIX). "
                         "Others run ungated. Default: no gating.")
    p.add_argument("--output-suffix", default="",
                    help="Appended to CSV output filenames so gated/ungated "
                         "runs don't clobber each other (e.g. '_gated_hb').")
    args = p.parse_args()
    subset = (args.strategies.split(",") if args.strategies else None)
    gated = (args.gate_strategies.split(",") if args.gate_strategies else None)
    run_bakeoff(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        strategies_subset=subset,
        gate_strategies=gated,
        output_suffix=args.output_suffix,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
