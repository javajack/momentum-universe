"""Backtest of the Ryner Teo / Connors RSI(2) pullback rules.

Per-trade simulation:
  ENTRY  : close < 5-SMA AND close > 200-SMA AND RSI(2) ≤ rsi_entry_max AND liquidity filters
  EXIT   : (a) close > 5-SMA  (primary)
           (b) RSI(2) ≥ rsi_exit_min  (alternate)
           (c) close ≤ stop_price = entry − atr_stop_mult × ATR(14)  (hard stop)
           (d) days_held ≥ time_stop_days  (time stop)
  SIZING : equal capital per trade (no compounding *within* concurrent trades)
  CONCUR : up to `max_concurrent` open positions at once

Output:
  - Per-trade table (console + CSV)
  - Summary: trades, win rate, avg win/loss, profit factor, total return,
    avg hold, exit-reason breakdown

Usage:
  .venv/bin/python tools/ryner_backtest.py --months 12
  .venv/bin/python tools/ryner_backtest.py --start 2023-01-01 --end 2026-02-11
  .venv/bin/python tools/ryner_backtest.py --rsi-max 5 --max-concurrent 3
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.ryner_pullback_scan import DEFAULTS as SCAN_DEFAULTS  # noqa: E402
from tools.ryner_pullback_scan import atr, rsi  # noqa: E402


BACKTEST_DEFAULTS = {
    **SCAN_DEFAULTS,
    "max_concurrent":     5,           # open positions cap
    "capital_per_trade":  100_000.0,   # ₹ per trade (notional — affects P&L ₹ only)
}


@dataclass
class Trade:
    ticker: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    stop_price: float
    pnl_pct: float
    pnl_inr: float
    hold_days: int
    exit_reason: str


def _precompute_indicators(prices: Dict[str, pd.DataFrame], cfg: dict) -> Dict[str, pd.DataFrame]:
    """Add SMA200 / SMA5 / RSI2 / ATR14 / AvgVol20 columns to each DF in place.
    Returns the dict for chaining."""
    for ticker, df in prices.items():
        if df is None or len(df) < cfg["trend_sma_period"]:
            continue
        c = df["close"]
        h = df.get("high", c)
        l = df.get("low", c)
        v = df.get("volume", pd.Series([1.0] * len(df), index=df.index))
        df["sma_trend"] = c.rolling(cfg["trend_sma_period"]).mean()
        df["sma_pull"] = c.rolling(cfg["pullback_sma_period"]).mean()
        df["rsi"] = rsi(c, cfg["rsi_period"])
        df["atr"] = atr(h, l, c, cfg["atr_period"])
        df["avg_vol_20"] = v.rolling(20).mean()
    return prices


def _build_membership_index(
    start: date, end: date, rank_range: tuple, version: str,
) -> Dict[date, set]:
    """Point-in-time membership: per-trading-day set of symbols in [lo, hi].

    Mirrors what `Universe(as_of=D).get_all_stocks()` would return for each D,
    but pre-built once for the whole window so the inner loop is O(1) per day.
    Strict [lo, hi] (NOT the widened price-load window) — this is what the
    live scanner sees on each day.
    """
    from nse_universe import Universe as NSEUniverse
    nse = NSEUniverse(version=version)
    lo, hi = rank_range
    ceiling_index = "nifty_1000" if hi > 500 else "nifty_500"
    df = nse.members_df(start, end, ceiling_index)
    df = df[(df["rank"] >= lo) & (df["rank"] <= hi)]
    out: Dict[date, set] = {}
    for d, group in df.groupby(df["date"].dt.date):
        out[d] = set(group["symbol"].tolist())
    return out


def _precompute_breadth(prices: Dict[str, pd.DataFrame],
                         membership: Dict[date, set],
                         cfg: dict) -> pd.Series:
    """Return a date-indexed Series: fraction of *point-in-time* universe whose
    close > own `trend_sma_period`-SMA on that date.

    For each date D we restrict the universe to `membership[D]` — the set of
    symbols that were actually in [lo, hi] on D. Mirrors how the live scanner
    computes breadth (on today's [201, 600] only, not on a static snapshot).

    Optionally smoothed via N-day rolling mean (cfg['regime_breadth_smoothing']).
    """
    flags = {}
    for ticker, df in prices.items():
        if df is None or "sma_trend" not in df.columns:
            continue
        flags[ticker] = df["close"] > df["sma_trend"]
    if not flags:
        return pd.Series(dtype=float)
    mat = pd.DataFrame(flags)

    # Point-in-time row reduction: for each date, mask to that day's members.
    breadths = []
    cached_members_cols: tuple | None = None  # (frozenset, list) to skip rebuild
    for ts in mat.index:
        d = ts.date()
        members = membership.get(d)
        if not members:
            breadths.append(np.nan)
            continue
        # Membership changes slowly (monthly-ish); cache last reduction.
        members_fset = frozenset(members)
        if cached_members_cols and cached_members_cols[0] == members_fset:
            cols_present = cached_members_cols[1]
        else:
            cols_present = [t for t in members if t in mat.columns]
            cached_members_cols = (members_fset, cols_present)
        if not cols_present:
            breadths.append(np.nan)
            continue
        row = mat.loc[ts, cols_present]
        breadths.append(float(row.mean(skipna=True)))
    breadth = pd.Series(breadths, index=mat.index)

    smooth_n = cfg.get("regime_breadth_smoothing", 1)
    if smooth_n and smooth_n > 1:
        breadth = breadth.rolling(int(smooth_n), min_periods=1).mean()
    return breadth


def _precompute_nifty_above_sma(prices: Dict[str, pd.DataFrame],
                                  cfg: dict) -> pd.Series:
    """Return a date-indexed boolean Series: True if Nifty 50 close > N-SMA.

    Returns an empty Series if the Nifty 50 benchmark isn't in prices_data.
    """
    nifty = prices.get("NIFTY 50")
    if nifty is None or len(nifty) < cfg.get("nifty50_sma_period", 200):
        return pd.Series(dtype=bool)
    period = cfg.get("nifty50_sma_period", 200)
    sma = nifty["close"].rolling(period).mean()
    return (nifty["close"] > sma).fillna(False)


def _precompute_gate_states(breadth: pd.Series, nifty_above: pd.Series,
                              cfg: dict) -> pd.Series:
    """Compute the gate-open boolean per date.

    State machine:
      - Start open
      - CLOSE when smoothed breadth drops below `regime_min_breadth`
      - REOPEN only when smoothed breadth crosses above `regime_open_breadth`
        (hysteresis — prevents flip-flopping near the threshold)
      - OVERRIDE: if Nifty 50 > 200-SMA AND smoothed breadth >=
        `regime_nifty_relax_breadth`, force gate open regardless of state
        (catches V-shaped recoveries faster than breadth alone)
    """
    open_thr = float(cfg.get("regime_open_breadth", 0.55))
    close_thr = float(cfg.get("regime_min_breadth", 0.50))
    use_nifty = bool(cfg.get("regime_use_nifty_overlay", True))
    relax_thr = float(cfg.get("regime_nifty_relax_breadth", 0.40))

    if breadth.empty:
        return pd.Series(dtype=bool)

    states = []
    gate_open = True
    for ts in breadth.index:
        b = float(breadth.loc[ts]) if pd.notna(breadth.loc[ts]) else 1.0
        nifty_bull = (use_nifty and ts in nifty_above.index
                       and bool(nifty_above.loc[ts]))

        # State machine with hysteresis (strict path)
        if gate_open and b < close_thr:
            gate_open = False
        elif (not gate_open) and b >= open_thr:
            gate_open = True

        # OR-overlay: Nifty bullish + relaxed breadth floor wins
        effective_open = gate_open or (nifty_bull and b >= relax_thr)
        states.append(effective_open)

    return pd.Series(states, index=breadth.index)


def _entry_signal(row: pd.Series, cfg: dict) -> bool:
    """True if this row's values qualify for entry."""
    try:
        c = row["close"]
        if not (
            np.isfinite(c) and np.isfinite(row["sma_trend"])
            and np.isfinite(row["sma_pull"]) and np.isfinite(row["rsi"])
            and np.isfinite(row["atr"]) and np.isfinite(row["avg_vol_20"])
        ):
            return False
    except KeyError:
        return False

    if c < cfg["min_price"]:
        return False
    if row["avg_vol_20"] < cfg["min_avg_volume_20d"]:
        return False
    if c < row["sma_trend"]:
        return False
    if c >= row["sma_pull"]:
        return False
    if row["rsi"] > cfg["rsi_entry_max"]:
        return False
    if (c / row["sma_trend"] - 1) > cfg["max_dist_above_200sma"]:
        return False
    return True


def _exit_reason(row: pd.Series, entry_price: float, stop_price: float,
                  days_held: int, cfg: dict) -> Optional[str]:
    """Return exit reason string or None to hold."""
    c = float(row["close"])
    if c <= stop_price:
        return "stop"
    if "rsi" in row and np.isfinite(row["rsi"]) and row["rsi"] >= cfg["rsi_exit_min"]:
        return "rsi_extreme"
    if "sma_pull" in row and np.isfinite(row["sma_pull"]) and c > row["sma_pull"]:
        return "above_5sma"
    if days_held >= cfg["time_stop_days"]:
        return "time_stop"
    return None


def backtest(prices: Dict[str, pd.DataFrame],
              universe,
              start: date, end: date, cfg: dict,
              progress_cb=None) -> List[Trade]:
    """Run the per-trade backtest. Returns list of closed Trades.

    `universe` accepts two shapes:
      • Dict[date, set[str]] → point-in-time membership (preferred, mirrors
        live scanner; entries on date D restricted to members[D]).
      • List[str]            → legacy static snapshot used across the whole
        run. Has survivorship/look-ahead bias on universe membership; kept
        for backwards-compat with sweeps that were tuned on it.

    Open positions persist through universe drop-outs in either mode — the
    live workflow doesn't auto-exit on membership change.
    """
    if isinstance(universe, dict):
        membership: Dict[date, set] = universe
        breadth_tickers = list(prices.keys())
    else:
        # Legacy path: static set used every day. Cheap synthetic membership.
        static_set = set(universe)
        membership = None  # type: ignore[assignment]
        breadth_tickers = list(static_set)

    _precompute_indicators(prices, cfg)

    # Pre-compute the regime-gate state series (cheap; done once).
    # If use_v3_gate=True, route through the combined signal (breadth slope +
    # sector ratio + VIX); otherwise legacy v1/v2 path (smoothed breadth +
    # hysteresis + optional Nifty overlay).
    # In PIT mode breadth is computed per-day against point-in-time members
    # inside _precompute_breadth. In legacy mode we synthesise an everyday
    # membership of the static set so the same code path works.
    if cfg.get("require_market_uptrend"):
        if membership is None:
            # Synthesise a constant membership across all loaded dates
            synth_membership = {}
            for df in prices.values():
                if df is None:
                    continue
                for ts in df.index:
                    synth_membership.setdefault(ts.date(), static_set)
            breadth_membership = synth_membership
        else:
            breadth_membership = membership
        breadth = _precompute_breadth(prices, breadth_membership, cfg)
        if cfg.get("use_v3_gate", False):
            from tools.ryner_regime import (
                compute_breadth_slope, compute_sector_breadth_ratio,
                compute_vix_trend, load_sectors_map, load_vix,
                combine_signals,
            )
            slope = compute_breadth_slope(breadth, cfg.get("regime_slope_window", 10))
            sectors_map = load_sectors_map()
            sector_ratio = compute_sector_breadth_ratio(
                prices, breadth_tickers, cfg, sectors_map,
            )
            vix_df = load_vix()
            vix_calm = compute_vix_trend(
                vix_df,
                sma_period=cfg.get("vix_sma_period", 21),
                max_calm_level=cfg.get("vix_max_calm_level", 22.0),
            )
            gate_open_series = combine_signals(
                breadth=breadth, breadth_slope=slope,
                sector_ratio=sector_ratio, vix_calm=vix_calm, cfg=cfg,
            )
        else:
            nifty_above = _precompute_nifty_above_sma(prices, cfg)
            gate_open_series = _precompute_gate_states(breadth, nifty_above, cfg)
    else:
        gate_open_series = None

    # Trading-day calendar = union of dates across all loaded price DFs.
    all_dates = sorted({
        d.normalize() for df in prices.values()
        for d in (df.index if df is not None else [])
        if start <= d.date() <= end
    })
    if not all_dates:
        return []

    open_pos: Dict[str, dict] = {}    # ticker → {entry_date, entry_price, stop, entry_idx}
    trades: List[Trade] = []

    for i, today in enumerate(all_dates):
        # --- 1. Process exits on today's close ---
        for t in list(open_pos.keys()):
            df = prices.get(t)
            if df is None or today not in df.index:
                continue
            row = df.loc[today]
            entry = open_pos[t]
            days_held = i - entry["entry_idx"]
            reason = _exit_reason(row, entry["entry_price"], entry["stop"], days_held, cfg)
            if reason is None:
                continue
            exit_price = float(row["close"])
            pnl_pct = (exit_price / entry["entry_price"] - 1) * 100
            pnl_inr = cfg["capital_per_trade"] * (exit_price / entry["entry_price"] - 1)
            trades.append(Trade(
                ticker=t,
                entry_date=entry["entry_date"],
                exit_date=today.date(),
                entry_price=entry["entry_price"],
                exit_price=exit_price,
                stop_price=entry["stop"],
                pnl_pct=pnl_pct,
                pnl_inr=pnl_inr,
                hold_days=days_held,
                exit_reason=reason,
            ))
            del open_pos[t]

        # --- 2. New entries on today's close (executed tomorrow open in practice;
        #         here we use today's close as entry price for simplicity) ---
        # Regime gate: skip new entries when smoothed breadth + Nifty overlay
        # both say the market is in distribution.
        if gate_open_series is not None and today in gate_open_series.index:
            if not bool(gate_open_series.loc[today]):
                # Gate closed — no new entries (existing positions still exit normally)
                continue

        # Point-in-time members for this date — the universe the live scanner
        # would have seen on this day. Fallback: most recent prior membership
        # (covers non-trading days / minor gaps in nse-universe member series).
        if membership is not None:
            members_today = membership.get(today.date())
            if members_today is None:
                for back in range(1, 8):
                    prior = (today - pd.Timedelta(days=back)).date()
                    if prior in membership:
                        members_today = membership[prior]
                        break
            if not members_today:
                continue
        else:
            members_today = static_set

        if len(open_pos) < cfg["max_concurrent"]:
            slots = cfg["max_concurrent"] - len(open_pos)
            today_signals: List[tuple[str, float, float, float]] = []  # (ticker, rsi, close, atr)
            for t in members_today:
                if t in open_pos:
                    continue
                df = prices.get(t)
                if df is None or today not in df.index:
                    continue
                row = df.loc[today]
                if _entry_signal(row, cfg):
                    today_signals.append((
                        t, float(row["rsi"]), float(row["close"]), float(row["atr"]),
                    ))
            # Sort by RSI ascending (deepest oversold first)
            today_signals.sort(key=lambda x: x[1])
            for t, _r, c, a in today_signals[:slots]:
                stop = c - cfg["atr_stop_mult"] * a
                open_pos[t] = {
                    "entry_date": today.date(),
                    "entry_idx": i,
                    "entry_price": c,
                    "stop": stop,
                }

        if progress_cb and i % 20 == 0:
            progress_cb(i, len(all_dates), today.date(), len(open_pos), len(trades))

    # --- 3. Close any remaining positions at end-of-period ---
    last_day = all_dates[-1]
    for t, entry in open_pos.items():
        df = prices.get(t)
        if df is None or last_day not in df.index:
            continue
        exit_price = float(df.loc[last_day, "close"])
        days_held = len(all_dates) - 1 - entry["entry_idx"]
        pnl_pct = (exit_price / entry["entry_price"] - 1) * 100
        pnl_inr = cfg["capital_per_trade"] * (exit_price / entry["entry_price"] - 1)
        trades.append(Trade(
            ticker=t,
            entry_date=entry["entry_date"],
            exit_date=last_day.date(),
            entry_price=entry["entry_price"],
            exit_price=exit_price,
            stop_price=entry["stop"],
            pnl_pct=pnl_pct,
            pnl_inr=pnl_inr,
            hold_days=days_held,
            exit_reason="end_of_period",
        ))
    return trades


def summarize(trades: List[Trade], cfg: dict) -> dict:
    if not trades:
        return {"n_trades": 0}
    pnls_pct = [t.pnl_pct for t in trades]
    pnls_inr = [t.pnl_inr for t in trades]
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    gross_win = sum(t.pnl_inr for t in wins)
    gross_loss = -sum(t.pnl_inr for t in losses)  # positive number
    return {
        "n_trades":          len(trades),
        "win_rate":          len(wins) / len(trades),
        "avg_win_pct":       np.mean([t.pnl_pct for t in wins]) if wins else 0.0,
        "avg_loss_pct":      np.mean([t.pnl_pct for t in losses]) if losses else 0.0,
        "avg_pnl_pct":       np.mean(pnls_pct),
        "median_pnl_pct":    float(np.median(pnls_pct)),
        "best_trade_pct":    max(pnls_pct),
        "worst_trade_pct":   min(pnls_pct),
        "profit_factor":     (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "total_pnl_inr":     sum(pnls_inr),
        "total_return_pct": (sum(pnls_inr) / cfg["capital_per_trade"]) * 100,
        "avg_hold_days":     np.mean([t.hold_days for t in trades]),
        "max_consec_losses": _max_consecutive(trades, lambda t: t.pnl_pct <= 0),
        "exit_reasons":      _count_by(trades, lambda t: t.exit_reason),
    }


def _max_consecutive(trades, pred) -> int:
    best = cur = 0
    for t in trades:
        if pred(t):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _count_by(trades, key) -> dict:
    out: dict = {}
    for t in trades:
        k = key(t)
        out[k] = out.get(k, 0) + 1
    return out


def _print_results(trades, summary, top_n: int = 20) -> None:
    print()
    print("━" * 88)
    print(f"  Ryner Teo / RSI(2) Pullback — Backtest Results")
    print("━" * 88)
    if not trades:
        print("  No trades generated. Loosen rsi_entry_max or expand date range.")
        return

    print(f"  Trades                : {summary['n_trades']}")
    print(f"  Win rate              : {summary['win_rate']:.1%}")
    print(f"  Avg P&L / trade       : {summary['avg_pnl_pct']:+.2f}%  "
          f"(median {summary['median_pnl_pct']:+.2f}%)")
    print(f"  Avg win / avg loss    : {summary['avg_win_pct']:+.2f}% / "
          f"{summary['avg_loss_pct']:+.2f}%")
    print(f"  Profit factor         : {summary['profit_factor']:.2f}")
    print(f"  Best / worst trade    : {summary['best_trade_pct']:+.1f}% / "
          f"{summary['worst_trade_pct']:+.1f}%")
    print(f"  Avg hold              : {summary['avg_hold_days']:.1f} days")
    print(f"  Max consec losses     : {summary['max_consec_losses']}")
    print(f"  Total P&L (₹/trade)   : ₹{summary['total_pnl_inr']:,.0f}")
    print(f"  Cumulative return%    : {summary['total_return_pct']:+.1f}%")
    print(f"  Exit reasons          : "
          + ", ".join(f"{r}={n}" for r, n in summary['exit_reasons'].items()))
    print()
    print(f"  ── First {min(top_n, len(trades))} trades (of {len(trades)}) ──")
    print(f"  {'Ticker':12s} {'Entered':>11s} {'Exited':>11s} "
          f"{'Days':>5s} {'Entry':>9s} {'Exit':>9s} {'P&L%':>7s} {'Reason':>12s}")
    print("  " + "-" * 86)
    for t in trades[:top_n]:
        print(f"  {t.ticker:12s} {str(t.entry_date):>11s} {str(t.exit_date):>11s} "
              f"{t.hold_days:>5d} ₹{t.entry_price:>7.2f} ₹{t.exit_price:>7.2f} "
              f"{t.pnl_pct:>+6.1f}% {t.exit_reason:>12s}")
    if len(trades) > top_n:
        print(f"  … ({len(trades) - top_n} more — see CSV)")


def _save_csv(trades: List[Trade], path: Path) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="") as f:
        if not trades:
            return
        w = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
        w.writeheader()
        for t in trades:
            w.writerow(asdict(t))


def run_backtest(
    start: date, end: date,
    rsi_entry_max: float = None,
    max_concurrent: int = None,
    capital_per_trade: float = None,
    config_path: str = "config.yaml",
    print_results: bool = True,
    save_csv: bool = True,
) -> tuple[List[Trade], dict]:
    """Programmatic entry point — used by the CLI menu hook."""
    from fortress.config import load_config
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.universe import Universe

    cfg_app = load_config(config_path)
    cfg = dict(BACKTEST_DEFAULTS)
    if rsi_entry_max is not None:
        cfg["rsi_entry_max"] = rsi_entry_max
    if max_concurrent is not None:
        cfg["max_concurrent"] = max_concurrent
    if capital_per_trade is not None:
        cfg["capital_per_trade"] = capital_per_trade

    rank_range = tuple(cfg_app.universe.rank_range)
    needed_warmup = cfg["trend_sma_period"] + cfg["atr_period"] + 30
    fetch_start = start - timedelta(days=int(needed_warmup * 1.5))

    print(f"  Universe          : v={cfg_app.universe.version}, "
          f"rank_range={list(rank_range)} (point-in-time per trading day)")
    print(f"  Backtest period   : {start} → {end}")
    print(f"  Rules             : RSI({cfg['rsi_period']}) ≤ {cfg['rsi_entry_max']}, "
          f"close < {cfg['pullback_sma_period']}-SMA, "
          f"close > {cfg['trend_sma_period']}-SMA")
    print(f"  Max concurrent    : {cfg['max_concurrent']}")
    print(f"  Capital per trade : ₹{cfg['capital_per_trade']:,.0f}")
    print(f"  Loading data      : {fetch_start} → {end} ...")

    prices = load_historical_for_backtest(
        start=fetch_start, end=end, rank_range=rank_range,
        version=cfg_app.universe.version,
    )
    membership = _build_membership_index(
        start=start, end=end, rank_range=rank_range,
        version=cfg_app.universe.version,
    )
    if membership:
        n_days = len(membership)
        avg_sz = sum(len(v) for v in membership.values()) / n_days
        print(f"  Loaded {len(prices)} symbol price series, "
              f"point-in-time universe avg {avg_sz:.0f} symbols/day "
              f"over {n_days} trading days")
    else:
        print(f"  Loaded {len(prices)} symbol price series, "
              f"WARNING: empty membership index")

    trades = backtest(prices, membership, start, end, cfg)
    summary = summarize(trades, cfg)
    if print_results:
        _print_results(trades, summary)
    if save_csv:
        out_path = REPO_ROOT / "plans" / f"ryner_backtest_{start}_{end}.csv"
        _save_csv(trades, out_path)
        print(f"\n  Trades saved      : {out_path}")
    return trades, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Ryner / RSI(2) pullback backtest")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--months", type=int, default=12,
                        help="Backtest the last N months ending today (default 12)")
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD; overrides --months")
    parser.add_argument("--end", type=str, default=None, help="YYYY-MM-DD; default today")
    parser.add_argument("--rsi-max", type=float, default=None)
    parser.add_argument("--max-concurrent", type=int, default=None)
    parser.add_argument("--capital-per-trade", type=float, default=None)
    args = parser.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today()
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        start = end - relativedelta(months=args.months)

    run_backtest(
        start=start, end=end,
        rsi_entry_max=args.rsi_max,
        max_concurrent=args.max_concurrent,
        capital_per_trade=args.capital_per_trade,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
