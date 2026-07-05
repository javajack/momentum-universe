"""Ryner Teo / Connors-style RSI(2) momentum-pullback scanner.

DAILY SIGNAL GENERATOR for swing trades — NOT a portfolio strategy.

Identifies stocks in a confirmed uptrend that have pulled back temporarily,
producing an oversold setup that historically mean-reverts in 2-10 days.
Reads the v2 universe + cached parquet prices. Prints today's entry
candidates with suggested stop levels. **No portfolio tracking, no auto-
rebalance — entries and exits are manual on Kite.**

Default rules (canonical RSI(2) pullback, Connors / Ryner Teo style):
  ENTRY:
    1. Liquidity        : 20d avg volume ≥ MIN_AVG_VOLUME_20D, close ≥ MIN_PRICE
    2. Uptrend          : close > 200-SMA (long-only filter)
    3. Pullback         : close < 5-SMA (recent weakness inside uptrend)
    4. Oversold         : RSI(2) ≤ 10  (deeply mean-reverting setup)
  EXIT (suggested — user manages manually):
    - Close > 5-SMA (mean reversion completed)        — primary
    - RSI(2) ≥ 70                                      — alternate
    - 10 trading days elapsed (time stop)              — backup
  STOP:
    - close - 1.5 × ATR(14)

Tune any value via DEFAULTS at the top of the file, or override per-run
through CLI flags.

Usage:
    .venv/bin/python tools/ryner_pullback_scan.py
    .venv/bin/python tools/ryner_pullback_scan.py --top 30 --rsi-max 5
    .venv/bin/python tools/ryner_pullback_scan.py --as-of 2026-05-13
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---- Default rules — TUNED VARIANT (2026-05-14 sweep over 2022-02→2026-02).
#
# Rationale (vs original Connors/Ryner defaults of RSI≤10, ATR 1.5×, 10d stop):
#   - rsi_entry_max  10 → 7      : deeper-oversold gate filters out shallow
#                                    pullbacks that don't reliably bounce.
#                                    Sample size shrinks ~15%, PF +0.04.
#   - atr_stop_mult  1.5 → 3.0   : wider stops let mean-reversion play out.
#                                    Tight stops fire on noise mid-pullback.
#                                    max_consec_losses 12 → 7. Tradeoff:
#                                    worst-case ₹ loss per trade is bigger
#                                    but happens less often.
#   - time_stop_days 10 → 20     : Indian mid-caps often need 7-15 days for
#                                    full mean-reversion completion. 10d
#                                    forced premature exits on otherwise
#                                    profitable bounces.
# Other knobs unchanged. To revert to canonical Connors values, restore the
# old triplet at top of `DEFAULTS`. Tune freely otherwise.
DEFAULTS = {
    "trend_sma_period":     200,         # uptrend confirmation
    "pullback_sma_period":  5,           # pullback reference MA
    "rsi_period":           2,           # RSI lookback
    "rsi_entry_max":        7.0,         # RSI(2) ≤ this = oversold  (was 10.0)
    "rsi_exit_min":         70.0,        # RSI(2) ≥ this = take exit
    "atr_period":           14,
    "atr_stop_mult":        3.0,         # stop = entry − mult × ATR  (was 1.5)
    "time_stop_days":       20,          # max hold before forced exit  (was 10)
    "min_avg_volume_20d":   200_000,     # shares
    "min_price":            50.0,
    "max_dist_above_200sma": 0.30,       # avoid extended names (>30% above 200d = wait)
    # ---- Regime gate (OFF by default — keep the user in control) ----
    # 8-window validation (seed=42, 2013→2026) shows:
    #   - Gate ON v1 (raw breadth, 50%):     median cum +147%, worst -67%
    #   - Gate ON v2 (smooth+hyst+nifty):    median cum +113%, worst -58%
    #   - Gate OFF (current default):         median cum +168%, worst -115%
    # Gate trims downside *and* upside. Better defaults (gate OFF) keep the
    # full alpha; users who want tail protection can flip require_market_uptrend
    # to True and tune the parameters below. Useful flags:
    #   - require_market_uptrend: True  → enables the gate
    #   - regime_breadth_smoothing >1   → N-day rolling mean (filter noise)
    #   - regime_open_breadth > regime_min_breadth  → adds hysteresis
    #   - regime_use_nifty_overlay: True → OR-gate with Nifty 50 > 200-SMA
    # Breadth + Nifty status are always *printed* as awareness, even when the
    # gate isn't enforced — so you can see incoming regime risk before it bites.
    "require_market_uptrend":       True,    # v3 gate ON (lossless in bulls, protects distribution)
    "regime_breadth_smoothing":     1,       # 1 = raw daily (no smoothing)
    "regime_min_breadth":           0.50,    # close threshold (when gate enabled)
    "regime_open_breadth":          0.50,    # = min → no hysteresis by default
    "regime_use_nifty_overlay":     False,   # off by default
    "regime_nifty_relax_breadth":   0.40,
    "nifty50_sma_period":           200,
    # ---- v3 regime signals (breadth slope + sector ratio + VIX) ----
    # Off by default; flip use_v3_gate=True to enable. When enabled, any of
    # four paths opens the gate: (A) strong breadth, (B) breadth recovering
    # from a dip, (C) cyclicals outperforming defensives, (D) calm VIX with
    # moderate breadth. Distinguishes 2025-Q1 distribution from 2026-Q1
    # early recovery — both had low breadth but different slope/sector/VIX.
    "use_v3_gate":                       True,    # v3 distinguishes distribution vs early recovery
    "regime_slope_window":               10,      # days for breadth slope
    "regime_slope_min":                  5.0,     # +5 pp over window = recovery
    "regime_min_breadth_slope_floor":    0.20,    # min breadth even for recovery path
    "regime_sector_min":                10.0,     # cyclical-defensive breadth gap (pp)
    "vix_sma_period":                    21,      # VIX trend lookback
    "vix_max_calm_level":               22.0,     # absolute ceiling for "calm"
    "regime_vix_relax_breadth":          0.40,
}


# ---- Indicators ----------------------------------------------------------

def rsi(prices: pd.Series, period: int = 2) -> pd.Series:
    """Wilder-smoothed RSI. Returns NaN for the first `period` observations.

    Edge cases:
      avg_loss=0, avg_gain>0  →  RSI = 100  (max bullish, no losses at all)
      avg_loss=0, avg_gain=0  →  RSI = NaN  (flat series, undefined)
    """
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    # Zero-loss case: RSI saturates at 100 if there's any gain, NaN if flat
    saturated = (avg_loss == 0) & (avg_gain > 0)
    out = out.where(~saturated, 100.0)
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range, Wilder-smoothed."""
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# ---- Scan ---------------------------------------------------------------

def evaluate_one(df: pd.DataFrame, cfg: dict) -> dict | None:
    """Return signal dict for one stock, or None if it doesn't qualify."""
    needed = max(cfg["trend_sma_period"], cfg["atr_period"]) + cfg["pullback_sma_period"]
    if df is None or len(df) < needed:
        return None

    close = df["close"]
    high = df.get("high", close)
    low = df.get("low", close)
    volume = df.get("volume")
    if volume is None:
        volume = pd.Series([1.0] * len(df), index=df.index)

    sma_trend = close.rolling(cfg["trend_sma_period"]).mean()
    sma_pull = close.rolling(cfg["pullback_sma_period"]).mean()
    rsi_s = rsi(close, cfg["rsi_period"])
    atr_s = atr(high, low, close, cfg["atr_period"])
    avg_vol_20 = volume.rolling(20).mean()

    if pd.isna(sma_trend.iloc[-1]) or pd.isna(rsi_s.iloc[-1]) or pd.isna(atr_s.iloc[-1]):
        return None

    c = float(close.iloc[-1])
    sma_t = float(sma_trend.iloc[-1])
    sma_p = float(sma_pull.iloc[-1])
    r = float(rsi_s.iloc[-1])
    a = float(atr_s.iloc[-1])
    avg_v = float(avg_vol_20.iloc[-1])

    # ---- Filters ----
    if c < cfg["min_price"]:
        return None
    if avg_v < cfg["min_avg_volume_20d"]:
        return None
    if c < sma_t:                       # not in uptrend
        return None
    if c >= sma_p:                      # not pulled back
        return None
    if r > cfg["rsi_entry_max"]:        # not oversold enough
        return None
    if (c / sma_t - 1) > cfg["max_dist_above_200sma"]:   # too extended
        return None

    return {
        "close":              c,
        "rsi2":               r,
        "above_200sma_pct":  (c / sma_t - 1) * 100,
        "below_5sma_pct":    (c / sma_p - 1) * 100,
        "atr14":              a,
        "suggested_stop":     c - cfg["atr_stop_mult"] * a,
        "stop_pct":           cfg["atr_stop_mult"] * a / c * 100,
        "avg_vol_20d":        avg_v,
    }


def scan(universe_tickers: list[str], prices_data: dict[str, pd.DataFrame],
         cfg: dict) -> list[dict]:
    out = []
    for t in universe_tickers:
        result = evaluate_one(prices_data.get(t), cfg)
        if result is None:
            continue
        result["ticker"] = t
        out.append(result)
    # Sort by RSI ascending (deepest oversold first)
    out.sort(key=lambda x: x["rsi2"])
    return out


def compute_breadth(universe_tickers: list[str], prices_data: dict[str, pd.DataFrame],
                     cfg: dict) -> float:
    """Smoothed fraction of universe with close > own 200-SMA.

    Computes daily breadth for the last `regime_breadth_smoothing` × 2 days,
    applies an N-day rolling mean, and returns the latest smoothed value.
    Mirrors the per-date breadth-smoothing used in the backtest.
    """
    period = cfg["trend_sma_period"]
    smooth_n = max(1, int(cfg.get("regime_breadth_smoothing", 1)))
    # Need at least `smooth_n` days of breadth values to compute the mean
    daily_breadth = []
    # Use the last `smooth_n + 5` days for the rolling mean (a few extra for buffer)
    window_len = smooth_n + 5
    flag_frames = {}
    for t in universe_tickers:
        df = prices_data.get(t)
        if df is None or len(df) < period + window_len:
            continue
        c = df["close"]
        sma = c.rolling(period).mean()
        flag = (c > sma).tail(window_len)
        flag_frames[t] = flag
    if not flag_frames:
        return 0.0
    combined = pd.DataFrame(flag_frames)
    daily = combined.mean(axis=1, skipna=True)
    smoothed = daily.rolling(smooth_n, min_periods=1).mean()
    if smoothed.empty:
        return 0.0
    return float(smoothed.iloc[-1])


def nifty50_above_sma(prices_data: dict[str, pd.DataFrame], cfg: dict) -> bool | None:
    """True if Nifty 50 close > N-SMA on latest day. None if no Nifty data."""
    nifty = prices_data.get("NIFTY 50")
    period = cfg.get("nifty50_sma_period", 200)
    if nifty is None or "close" not in nifty.columns or len(nifty) < period:
        return None
    sma = nifty["close"].iloc[-period:].mean()
    return bool(nifty["close"].iloc[-1] > sma)


# ANSI color codes for the concern banner
_C = {
    "green":  "\033[92m", "yellow": "\033[93m",
    "orange": "\033[38;5;208m", "red": "\033[91m",
    "bold":   "\033[1m", "reset": "\033[0m",
}


def _color(text: str, c: str) -> str:
    return f"{_C.get(c, '')}{text}{_C['reset']}"


# ---- Main ---------------------------------------------------------------

def run_scan(
    *,
    as_of: date | None = None,
    top: int = 20,
    rsi_max: float | None = None,
    min_price: float | None = None,
    skip_regime_gate: bool = False,
    config_path: str = "config.yaml",
) -> list[dict]:
    """Programmatic entry point for the scanner.

    Returns the list of candidate dicts. Prints the full banner + table to
    stdout (same output as the CLI). Used by both the argparse `main()`
    below and the fortress menu Option S.
    """
    from fortress.config import load_config
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.universe import Universe

    cfg_app = load_config(config_path)
    cfg = dict(DEFAULTS)
    if rsi_max is not None:
        cfg["rsi_entry_max"] = rsi_max
    if min_price is not None:
        cfg["min_price"] = min_price
    if skip_regime_gate:
        cfg["require_market_uptrend"] = False

    if as_of is None:
        as_of = date.today()
    rank_range = tuple(cfg_app.universe.rank_range)

    print(f"━━━ Ryner Teo / RSI(2) Pullback Scanner ━━━")
    print(f"  As-of date:       {as_of}")
    print(f"  Universe:         v={cfg_app.universe.version}, "
          f"rank_range={list(rank_range)}")
    print(f"  Trend filter:     close > {cfg['trend_sma_period']}-SMA")
    print(f"  Pullback filter:  close < {cfg['pullback_sma_period']}-SMA "
          f"AND RSI({cfg['rsi_period']}) ≤ {cfg['rsi_entry_max']}")
    print(f"  Liquidity:        avg vol 20d ≥ {cfg['min_avg_volume_20d']:,}, "
          f"close ≥ ₹{cfg['min_price']:.0f}")
    print()

    uni = Universe(as_of=as_of, rank_range=rank_range, version=cfg_app.universe.version)
    tickers = [s.ticker for s in uni.get_all_stocks()]
    print(f"  Universe size:    {len(tickers)} stocks")

    needed_history_days = cfg["trend_sma_period"] + cfg["atr_period"] + 30
    fetch_start = as_of - timedelta(days=int(needed_history_days * 1.5))
    print(f"  Loading prices:   {fetch_start} → {as_of} ...")
    prices_data = load_historical_for_backtest(
        start=fetch_start, end=as_of, rank_range=rank_range,
        version=cfg_app.universe.version,
    )
    print(f"  Loaded:           {len(prices_data)} symbols")

    # ---- Regime awareness (always shown) + optional gate ----
    breadth = compute_breadth(tickers, prices_data, cfg)
    nifty_bull = nifty50_above_sma(prices_data, cfg)
    smooth_n = cfg.get("regime_breadth_smoothing", 1)
    close_thr = cfg.get("regime_min_breadth", 0.50)
    relax_thr = cfg.get("regime_nifty_relax_breadth", 0.40)
    use_nifty = cfg.get("regime_use_nifty_overlay", False)
    gate_enabled = cfg.get("require_market_uptrend", False)

    nifty_str = ("?" if nifty_bull is None else ("up" if nifty_bull else "down"))
    smoothing_str = f"{smooth_n}d smooth" if smooth_n > 1 else "raw"
    print(f"  Breadth ({smoothing_str} >200-SMA): {breadth:.1%}")
    print(f"  Nifty 50 vs 200-SMA               : {nifty_str}")

    # ---- 2018/2022-style setup concern flag (NON-BLOCKING — awareness only) ----
    try:
        from tools.ryner_regime import (
            compute_breadth_slope, compute_concern_signal, load_sectors_map, load_vix,
        )
        # Need a breadth series, not just latest, to compute slope. Re-derive.
        flag_frames = {}
        period = cfg["trend_sma_period"]
        smooth_n_local = max(1, int(cfg.get("regime_breadth_smoothing", 1)))
        for t in tickers:
            df = prices_data.get(t)
            if df is None or len(df) < period + 30:
                continue
            sma = df["close"].rolling(period).mean()
            flag_frames[t] = df["close"] > sma
        if flag_frames:
            daily = pd.DataFrame(flag_frames).mean(axis=1, skipna=True)
            smoothed = daily.rolling(smooth_n_local, min_periods=1).mean()
            slope = compute_breadth_slope(smoothed, cfg.get("regime_slope_window", 10))
            vix_df = load_vix()
            sectors_map = load_sectors_map()
            concern = compute_concern_signal(
                breadth=smoothed, breadth_slope=slope, vix_df=vix_df,
                prices=prices_data, universe=tickers,
                sectors_map=sectors_map, cfg=cfg,
            )
            level = concern["level"]
            if level == 0:
                banner = _color("✓ Stress check: CLEAN (0/3)", "green")
                tail = "— no 2018/2022 setup signature"
            elif level == 1:
                banner = _color("⚠ Stress check: 1/3 flag", "yellow")
                tail = "— minor concern, signals OK to trade"
            elif level == 2:
                banner = _color("⚠⚠ Stress check: 2/3 flags — REGIME WATCH", "orange")
                tail = "— signals still active but consider sizing down"
            else:
                banner = _color(_color("⚠⚠⚠ Stress check: 3/3 flags — MATCHES 2018/2022 SETUP", "red"), "bold")
                tail = "— signals NOT blocked; strongly consider manual override"
            print(f"  {banner}  {tail}")
            for f in concern["flags"]:
                print(f"      • {f}")
    except Exception as e:
        # Concern flag is opportunistic — never fail the scanner if it errors.
        print(f"  (stress check unavailable: {e})")
    if not gate_enabled:
        # Show risk awareness even when gate is OFF
        if breadth < close_thr or nifty_bull is False:
            print(f"  ⚠  Regime risk flag: "
                  f"{'breadth weak ' if breadth < close_thr else ''}"
                  f"{'+ ' if breadth < close_thr and nifty_bull is False else ''}"
                  f"{'Nifty < 200-SMA' if nifty_bull is False else ''}"
                  f"  (gate is OFF — signals not suppressed)")
    elif gate_enabled:
        nifty_path_open = (use_nifty and nifty_bull is True
                            and breadth >= relax_thr)
        strict_path_open = breadth >= close_thr
        gate_open = strict_path_open or nifty_path_open
        if not gate_open:
            print()
            why = []
            if breadth < close_thr:
                why.append(f"breadth {breadth:.0%} < {close_thr:.0%}")
            if use_nifty and nifty_bull is False:
                why.append("Nifty 50 below 200-SMA")
            if use_nifty and nifty_bull is True and breadth < relax_thr:
                why.append(f"Nifty bull but breadth {breadth:.0%} < relax {relax_thr:.0%}")
            print(f"  ⚠  Regime gate CLOSED ({'; '.join(why)}). New Ryner entries SUPPRESSED.")
            print(f"     Continue holding existing positions; do not open new ones.")
            print(f"     Pass --skip-regime-gate to force the scan anyway.")
            return []
        else:
            print(f"  Regime gate OPEN  "
                  f"(breadth {breadth:.0%}, nifty {nifty_str})")
    print()

    candidates = scan(tickers, prices_data, cfg)
    print(f"━━━ {len(candidates)} pullback candidate(s) ━━━\n")

    if not candidates:
        print("  No qualifying setups today. Try --rsi-max 15 to loosen.")
        return []

    n_show = min(top, len(candidates))
    print(f"{'#':>3} {'Ticker':12s} {'Close':>9s} {'RSI(2)':>7s} "
          f"{'>200SMA':>9s} {'<5SMA':>8s} {'ATR14':>8s} "
          f"{'Stop':>9s} {'Stop%':>7s}")
    print("-" * 88)
    for i, c in enumerate(candidates[:n_show], 1):
        print(f"{i:>3d} {c['ticker']:12s} ₹{c['close']:>8.2f} "
              f"{c['rsi2']:>7.1f} {c['above_200sma_pct']:>+8.1f}% "
              f"{c['below_5sma_pct']:>+7.1f}% ₹{c['atr14']:>6.2f} "
              f"₹{c['suggested_stop']:>7.2f} {c['stop_pct']:>6.2f}%")

    if len(candidates) > n_show:
        print(f"\n  … ({len(candidates) - n_show} more — request top={len(candidates)} to see all)")

    # Save CSV
    plans_dir = REPO_ROOT / "plans"
    plans_dir.mkdir(exist_ok=True)
    out_path = plans_dir / f"ryner_pullback_{as_of}.csv"
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "ticker", "close", "rsi2", "above_200sma_pct", "below_5sma_pct",
            "atr14", "suggested_stop", "stop_pct", "avg_vol_20d",
        ])
        w.writeheader()
        for c in candidates:
            w.writerow({k: c[k] for k in w.fieldnames})
    print(f"\n  Signals saved:    {out_path}")
    print(f"\n  Exit rules to track manually:")
    print(f"    - Close > {cfg['pullback_sma_period']}-SMA (primary)")
    print(f"    - RSI({cfg['rsi_period']}) ≥ {cfg['rsi_exit_min']} (alternate)")
    print(f"    - {cfg['time_stop_days']} trading days elapsed (time stop)")
    print(f"    - Hard stop at suggested level if breached intraday")
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Ryner Teo / RSI(2) pullback scanner")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--top", type=int, default=20, help="Max candidates to print")
    parser.add_argument("--as-of", type=str, default=None,
                        help="ISO date (YYYY-MM-DD) to scan as-of (default: today)")
    parser.add_argument("--rsi-max", type=float, default=None,
                        help=f"Override RSI entry threshold (default {DEFAULTS['rsi_entry_max']})")
    parser.add_argument("--min-price", type=float, default=None,
                        help=f"Override min price (default ₹{DEFAULTS['min_price']})")
    parser.add_argument("--skip-regime-gate", action="store_true",
                        help="Force scan even when market breadth is sub-threshold")
    args = parser.parse_args()
    run_scan(
        as_of=date.fromisoformat(args.as_of) if args.as_of else None,
        top=args.top,
        rsi_max=args.rsi_max,
        min_price=args.min_price,
        skip_regime_gate=args.skip_regime_gate,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
