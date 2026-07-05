"""52-week-high + tight-base swing scanner (Minervini VCP-lite).

DAILY SIGNAL GENERATOR for swing trades — complements (does NOT replace)
the Ryner RSI(2) pullback scanner. Both strategies share the same [201, 600]
mid/smallcap universe but read different signal types: Ryner buys oversold
weakness inside an uptrend, this scanner buys strength resolving from a
tight base near 52w highs.

5-year bake-off result (`docs/superpowers/specs/2026-05-27-swing-bakeoff-design.md`
+ `nightlog.md` Parts 4-12) shows:
  - This strategy:  PF 1.41, Sharpe 1.58, MaxDD −22%, 2/5 years positive,
                     concentrated in 2021+2023 bull/recovery
  - Ryner control:  PF 1.17, Sharpe 1.03, MaxDD −23%, 5/6 years positive,
                     consistent across regimes but cost-fragile (PF 1.02 at 60bp)
  - PAIR  (₹500k each, independent accounts): Calmar 0.73 (vs 0.56/0.57 solo),
                     MaxDD −17%, 4/6 years positive. The pair is the answer.

Run both scanners daily, allocate distinct ₹ pools. This file is the
"Option-V" half of that pair.

Default rules (textbook Minervini, NOT tuned — three improvement attempts
in nightlog Parts 8/9/10 all hurt performance):
  ENTRY:
    1. Liquidity     : 20d avg vol >= MIN_AVG_VOLUME_20D, close >= MIN_PRICE
    2. Near 52w high : close >= NEAR_HIGH_PCT * max(close, 252)
    3. Tight base    : (high_20 - low_20) / close < MAX_RANGE_20
  EXIT (suggested — user manages manually):
    - close < 21-EMA                                    — primary
    - close < entry - ATR_STOP_MULT × ATR(14)           — hard stop
    - 30 trading days elapsed                            — time stop
  STOP:
    - entry - 3.0 × ATR(14)  (wide — the strategy lets winners run)

DO NOT add: regime gate, volume confirmation, or tighter time stop. All
three were tested empirically on the 5y bake-off and all three reduced
total P&L by killing the right tail. See nightlog Parts 8-10 for details.

Usage:
    .venv/bin/python tools/high_base_scan.py
    .venv/bin/python tools/high_base_scan.py --top 30 --as-of 2026-05-13
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.swing_bakeoff import (  # noqa: E402
    HighBaseBreakout52w, compute_shared_indicators,
)


# Surfaced for CLI display + symmetry with `tools.ryner_pullback_scan.DEFAULTS`
DEFAULTS = {
    "trend_sma_period":      252,           # 52 weeks
    "tight_base_window":     20,            # days
    "near_high_pct":         HighBaseBreakout52w.NEAR_HIGH_PCT,        # 0.97
    "max_range_20":          HighBaseBreakout52w.MAX_RANGE_20,         # 0.10
    "atr_period":            14,
    "atr_stop_mult":         HighBaseBreakout52w.ATR_STOP_MULT,        # 3.0
    "time_stop_days":        HighBaseBreakout52w.TIME_STOP,            # 30
    "min_avg_volume_20d":    HighBaseBreakout52w.MIN_VOL,              # 200_000
    "min_price":             HighBaseBreakout52w.MIN_PRICE,            # 50.0
    "exit_ema_period":       21,
}


# ANSI codes for the comparison banner
_C = {
    "green":  "\033[92m", "yellow": "\033[93m",
    "cyan":   "\033[96m", "bold":   "\033[1m", "reset": "\033[0m",
}


def _color(text: str, c: str) -> str:
    return f"{_C.get(c, '')}{text}{_C['reset']}"


def _evaluate_one(ticker: str, df: pd.DataFrame,
                   today: pd.Timestamp, strat: HighBaseBreakout52w) -> dict | None:
    """Run the gate via the shared `HighBaseBreakout52w` class. If qualifying,
    return a candidate dict with display fields. Otherwise None."""
    if not strat.should_enter(df, today):
        return None
    r = df.loc[today]
    close = float(r["close"])
    high_252 = float(r["high_252"])
    atr_val = float(r["atr_14"])
    return {
        "ticker":              ticker,
        "close":               close,
        "high_252":            high_252,
        "dist_from_high_pct":  (close / high_252 - 1) * 100,  # negative = below
        "range_20_pct":        float(r["range_20"]) * 100,
        "atr14":               atr_val,
        "suggested_stop":      close - DEFAULTS["atr_stop_mult"] * atr_val,
        "stop_pct":            DEFAULTS["atr_stop_mult"] * atr_val / close * 100,
        "avg_vol_20d":         float(r["avg_vol_20"]),
        "ret_60_pct":          (float(r["ret_60"]) * 100) if pd.notna(r.get("ret_60")) else float("nan"),
        "rank_key":            (high_252 - close) / close,
    }


def run_scan(
    *,
    as_of: date | None = None,
    top: int = 20,
    config_path: str = "config.yaml",
) -> list[dict]:
    """Programmatic entry point. Returns the list of candidate dicts; prints
    a full banner + table to stdout. Used by both the argparse `main()` and
    the fortress menu Option-V."""
    from fortress.config import load_config
    from fortress.nse_data_loader import load_historical_for_backtest
    from fortress.universe import Universe

    cfg_app = load_config(config_path)
    if as_of is None:
        as_of = date.today()
    rank_range = tuple(cfg_app.universe.rank_range)

    print(f"━━━ 52-week-high + Tight-base Scanner (Minervini VCP-lite) ━━━")
    print(f"  As-of date:       {as_of}")
    print(f"  Universe:         v={cfg_app.universe.version}, "
          f"rank_range={list(rank_range)}")
    print(f"  Trend filter:     close >= {DEFAULTS['near_high_pct']:.0%} of "
          f"{DEFAULTS['trend_sma_period']}-day high (52w)")
    print(f"  Base filter:      {DEFAULTS['tight_base_window']}d range "
          f"<= {DEFAULTS['max_range_20']:.0%} of price")
    print(f"  Liquidity:        avg vol 20d >= {DEFAULTS['min_avg_volume_20d']:,}, "
          f"close >= ₹{DEFAULTS['min_price']:.0f}")
    print(f"  Hard stop:        entry - {DEFAULTS['atr_stop_mult']:g} x ATR({DEFAULTS['atr_period']})")
    print(f"  Time stop:        {DEFAULTS['time_stop_days']} trading days")
    print(f"  Exit signal:      close < {DEFAULTS['exit_ema_period']}-EMA  (primary)")
    print()

    # Comparison banner vs Ryner (the other half of the recommended pair)
    print(_color("  ╭─ How this differs from Option-S (Ryner) ────────────────────────────╮", "cyan"))
    print(_color("  │  Ryner buys WEAKNESS  in uptrend (oversold dips, RSI(2) <= 7)        │", "cyan"))
    print(_color("  │  This  buys STRENGTH  near 52w high resolving from tight base        │", "cyan"))
    print(_color("  │  Both run in parallel, no slot competition (separate ₹ pools)        │", "cyan"))
    print(_color("  │  5y bake-off Calmar: PAIR 0.73 > rsi2 solo 0.57 > high_base solo 0.56│", "cyan"))
    print(_color("  ╰──────────────────────────────────────────────────────────────────────╯", "cyan"))
    print()

    uni = Universe(as_of=as_of, rank_range=rank_range, version=cfg_app.universe.version)
    tickers = [s.ticker for s in uni.get_all_stocks()]
    print(f"  Universe size:    {len(tickers)} stocks")

    # Need ~252+14+buffer days of history to compute high_252 and ATR(14).
    fetch_start = as_of - timedelta(days=int((DEFAULTS["trend_sma_period"]
                                                + DEFAULTS["atr_period"] + 30) * 1.6))
    print(f"  Loading prices:   {fetch_start} → {as_of} ...")
    prices_data = load_historical_for_backtest(
        start=fetch_start, end=as_of, rank_range=rank_range,
        version=cfg_app.universe.version,
    )
    print(f"  Loaded:           {len(prices_data)} symbols")

    compute_shared_indicators(prices_data)
    strat = HighBaseBreakout52w()
    strat.precompute(prices_data)

    # Find the last trading day at or before as_of present in any df.
    candidates: list[dict] = []
    target = pd.Timestamp(as_of)
    for t in tickers:
        df = prices_data.get(t)
        if df is None or df.empty:
            continue
        # Use the most recent trading day at-or-before target
        valid = df.index[df.index <= target]
        if len(valid) == 0:
            continue
        today = valid[-1]
        result = _evaluate_one(t, df, today, strat)
        if result is None:
            continue
        candidates.append(result)

    # Rank: closest to 52w high first (smallest dist_from_high) — same rank_key
    # the bake-off uses for slot selection
    candidates.sort(key=lambda x: x["rank_key"])

    print(f"\n━━━ {len(candidates)} qualifying setup(s) ━━━\n")
    if not candidates:
        print("  No qualifying setups today. The strategy fires sparingly by "
              "design — patient waiting is part of the edge.")
        print("  Recent annual trade count: ~85/year over 2021-2026.")
        return []

    n_show = min(top, len(candidates))
    print(f"{'#':>3} {'Ticker':12s} {'Close':>9s} {'52wHigh':>9s} "
          f"{'<High':>7s} {'Rng20':>7s} {'ATR14':>8s} "
          f"{'Stop':>9s} {'Stop%':>7s} {'12wRet':>7s}")
    print("-" * 90)
    for i, c in enumerate(candidates[:n_show], 1):
        print(f"{i:>3d} {c['ticker']:12s} ₹{c['close']:>8.2f} "
              f"₹{c['high_252']:>8.2f} {c['dist_from_high_pct']:>+6.1f}% "
              f"{c['range_20_pct']:>6.1f}% ₹{c['atr14']:>6.2f} "
              f"₹{c['suggested_stop']:>7.2f} {c['stop_pct']:>6.2f}% "
              f"{c['ret_60_pct']:>+6.1f}%")
    if len(candidates) > n_show:
        print(f"\n  … ({len(candidates) - n_show} more — request top={len(candidates)} to see all)")

    # Save CSV (same convention as Ryner scanner)
    plans_dir = REPO_ROOT / "plans"
    plans_dir.mkdir(exist_ok=True)
    out_path = plans_dir / f"high_base_{as_of}.csv"
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "ticker", "close", "high_252", "dist_from_high_pct",
            "range_20_pct", "atr14", "suggested_stop", "stop_pct",
            "avg_vol_20d", "ret_60_pct",
        ])
        w.writeheader()
        for c in candidates:
            w.writerow({k: c[k] for k in w.fieldnames})
    print(f"\n  Signals saved:    {out_path}")
    print(f"\n  Exit rules to track manually:")
    print(f"    - close < {DEFAULTS['exit_ema_period']}-EMA (primary; 90% of "
          f"successful exits hit this first)")
    print(f"    - Hard stop at suggested level if breached intraday")
    print(f"    - {DEFAULTS['time_stop_days']}-day time stop if neither fires")
    return candidates


def main() -> None:
    p = argparse.ArgumentParser(description="52w-high + tight-base scanner")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--top", type=int, default=20, help="Max candidates to print")
    p.add_argument("--as-of", type=str, default=None,
                    help="ISO date (YYYY-MM-DD); default today")
    args = p.parse_args()
    run_scan(
        as_of=date.fromisoformat(args.as_of) if args.as_of else None,
        top=args.top,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
