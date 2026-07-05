"""Three sophisticated regime signals for the Ryner pullback gate.

The naive breadth-gate (v1/v2) couldn't distinguish "distribution decline"
from "early recovery rally" because both have low absolute breadth. These
three signals attack the differentiation from different angles:

  (1) BREADTH SLOPE — rate of change of breadth over N days.
      Falling breadth + below threshold = distribution (bad)
      Rising breadth + below threshold = recovery starting (good, allow)

  (2) SECTOR BREADTH RATIO — defensives vs cyclicals.
      Defensives outperforming cyclicals = fear, sector rotation OUT of risk
      Cyclicals outperforming defensives = risk-on, even at low total breadth

  (3) VIX TREND — VIX vs its own SMA + level.
      VIX rising AND elevated = fear, suppress
      VIX flat-or-falling = calm, allow

Each is computed as a pure function returning a date-indexed boolean Series
that says "is this signal saying ALLOW entries?". The combined gate ANDs/ORs
them per the cfg.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd


# Sector classification — defensive vs cyclical.
# Defensives have stable earnings across cycles; cyclicals are GDP-sensitive.
# Mapping based on the sector labels in stock-sectors.json (4166 mapped names).
DEFENSIVE_SECTORS = {
    "CONSUMER_STAPLES",
    "HEALTHCARE",
    "UTILITIES",
    "TELECOM",
    "DEFENSIVE",        # already-flagged defensive ETFs/instruments
    "COMMODITIES",      # gold/precious is risk-off proxy
    "DEBT",             # liquid funds, debt ETFs
}
CYCLICAL_SECTORS = {
    "INDUSTRIALS",
    "FINANCIALS",
    "CONSUMER_DISCRETIONARY",
    "MATERIALS",
    "INFORMATION_TECHNOLOGY",
    "AUTOMOBILES",
    "INFRASTRUCTURE",
    "METALS_MINING",
    "MEDIA",
    "ENERGY",
    "REAL_ESTATE",
}
# UNCLASSIFIED / INTERNATIONAL → neither bucket (excluded from ratio).


def compute_breadth_slope(breadth: pd.Series, window: int = 10) -> pd.Series:
    """Rate of change of breadth over the last `window` days, expressed as
    a percentage-point delta (e.g., +5 means breadth rose 5 pp in window).

    Used in combination with absolute level:
      breadth_slope > 0  AND  breadth < min_threshold → "recovery" (allow)
      breadth_slope < 0  AND  breadth < min_threshold → "distribution" (block)
    """
    if breadth.empty:
        return pd.Series(dtype=float)
    return (breadth - breadth.shift(window)) * 100.0


def load_sectors_map(sectors_path: str | Path = "stock-sectors.json") -> Dict[str, str]:
    """Load ticker → sector mapping. Returns empty dict if file missing."""
    p = Path(sectors_path)
    if not p.exists():
        return {}
    doc = json.loads(p.read_text()).get("symbols", {})
    return {sym: info.get("sector", "UNCLASSIFIED") for sym, info in doc.items()}


def compute_sector_breadth_ratio(
    prices: Dict[str, pd.DataFrame], universe: list[str], cfg: dict,
    sectors_map: Optional[Dict[str, str]] = None,
) -> pd.Series:
    """For each date: cyclical-breadth − defensive-breadth (in pp).

    Positive  = risk-on (cyclicals stronger), allow Ryner entries
    Negative  = risk-off (defensives outperforming), market in distribution
    Zero/None = neutral or insufficient data

    Each "breadth" here = fraction of that sector's stocks above own
    `trend_sma_period`-SMA.
    """
    if sectors_map is None:
        sectors_map = load_sectors_map()
    if not sectors_map:
        return pd.Series(dtype=float)

    period = cfg.get("trend_sma_period", 200)
    cyc_flags: Dict[str, pd.Series] = {}
    def_flags: Dict[str, pd.Series] = {}

    for ticker in universe:
        df = prices.get(ticker)
        if df is None or len(df) < period:
            continue
        sec = sectors_map.get(ticker, "UNCLASSIFIED")
        if sec not in CYCLICAL_SECTORS and sec not in DEFENSIVE_SECTORS:
            continue
        # `sma_trend` column added by _precompute_indicators
        if "sma_trend" in df.columns:
            above = df["close"] > df["sma_trend"]
        else:
            sma = df["close"].rolling(period).mean()
            above = df["close"] > sma
        if sec in CYCLICAL_SECTORS:
            cyc_flags[ticker] = above
        else:
            def_flags[ticker] = above

    if not cyc_flags or not def_flags:
        return pd.Series(dtype=float)

    cyc_breadth = pd.DataFrame(cyc_flags).mean(axis=1, skipna=True)
    def_breadth = pd.DataFrame(def_flags).mean(axis=1, skipna=True)
    # Align and subtract — units: percentage points (cyclical minus defensive)
    return ((cyc_breadth - def_breadth) * 100.0).dropna()


def load_vix(vix_path: str | Path = "data/benchmarks/india_vix.parquet") -> pd.DataFrame:
    """Load India VIX OHLC from cached parquet. Returns empty df if missing."""
    p = Path(vix_path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def compute_vix_trend(vix_df: pd.DataFrame, sma_period: int = 21,
                       max_calm_level: float = 22.0) -> pd.Series:
    """For each date: True if VIX is "calm" (allow entries), False if elevated.

    Calm signature: VIX < `max_calm_level` AND VIX < its own `sma_period`-SMA
    (declining trend). One condition softens the other — but BOTH satisfied =
    strong calm signal.
    """
    if vix_df.empty or "close" not in vix_df.columns:
        return pd.Series(dtype=bool)
    close = vix_df["close"]
    sma = close.rolling(sma_period).mean()
    # Calm = below absolute ceiling AND below own SMA (no rising fear)
    calm = (close < max_calm_level) & (close < sma)
    return calm.fillna(False)


def combine_signals(
    breadth: pd.Series,
    breadth_slope: pd.Series,
    sector_ratio: pd.Series,
    vix_calm: pd.Series,
    cfg: dict,
) -> pd.Series:
    """Combine the three signals + raw breadth into a single gate-open Series.

    Logic (any single relax-path opens the gate):
      A. Strong breadth path:  breadth >= regime_min_breadth
      B. Breadth-slope recovery: breadth >= regime_min_breadth_slope_floor
                                  AND breadth_slope >= regime_slope_min
      C. Sector risk-on:       sector_ratio >= regime_sector_min
      D. Calm VIX:             vix_calm == True
                                  AND breadth >= regime_vix_relax_breadth

    Any one true → gate open. All false → closed.

    Tuning intent:
      - A = the original v1 gate (50% threshold)
      - B = catches "early recovery starting" (Q1 2026 case)
      - C = catches "rotation into risk" before total breadth rises
      - D = catches "complacent uptrend at moderate breadth"
    """
    min_breadth = cfg.get("regime_min_breadth", 0.50)
    slope_floor = cfg.get("regime_min_breadth_slope_floor", 0.20)
    slope_min = cfg.get("regime_slope_min", 5.0)  # +5 pp over the slope window
    sector_min = cfg.get("regime_sector_min", 10.0)  # cyclicals ≥10 pp ahead of def
    vix_relax_b = cfg.get("regime_vix_relax_breadth", 0.40)

    # Align all series on a common index = breadth's index
    idx = breadth.index
    bs = breadth_slope.reindex(idx).fillna(0)
    sr = sector_ratio.reindex(idx).fillna(0)
    vc = vix_calm.reindex(idx).fillna(False)

    A = breadth >= min_breadth
    B = (breadth >= slope_floor) & (bs >= slope_min)
    C = sr >= sector_min
    D = vc & (breadth >= vix_relax_b)

    return (A | B | C | D).fillna(False)


def compute_sector_breadth_now(
    prices: Dict[str, pd.DataFrame], universe: list[str],
    sectors_map: Dict[str, str], sector_name: str, period: int = 200,
) -> Optional[float]:
    """Latest-day breadth for a specific sector. None if no qualifying members.

    Used by the 2018/2022-stress concern flag to check if financials are
    underperforming the overall universe.
    """
    flags = []
    for t in universe:
        if sectors_map.get(t, "UNCLASSIFIED") != sector_name:
            continue
        df = prices.get(t)
        if df is None or len(df) < period:
            continue
        sma = df["close"].iloc[-period:].mean()
        if pd.notna(sma) and sma > 0:
            flags.append(bool(df["close"].iloc[-1] > sma))
    if not flags:
        return None
    return sum(flags) / len(flags)


def compute_concern_signal(
    breadth: pd.Series, breadth_slope: pd.Series,
    vix_df: pd.DataFrame,
    prices: Dict[str, pd.DataFrame], universe: list[str],
    sectors_map: Dict[str, str], cfg: dict,
) -> dict:
    """Detect 2018/2022-style setup signature. Returns concern level 0-3 + flag list.

    Three independent stress indicators:
      F1 BREADTH ACCELERATING DOWN — breadth slope ≤ −10pp / 10d
      F2 VIX RISING + ELEVATED      — VIX > 22 AND VIX > own 21-SMA
      F3 FINANCIALS SECTOR STRESS   — financials breadth ≥ 15pp below total breadth

    Used as a non-blocking awareness signal in the scanner. Does NOT alter
    gate behavior.
    """
    flags: list[str] = []

    # F1: Breadth slope sharply down
    slope_thresh = cfg.get("concern_slope_threshold", -10.0)
    if not breadth_slope.empty:
        latest_slope = float(breadth_slope.iloc[-1])
        if latest_slope <= slope_thresh:
            flags.append(
                f"breadth slope {latest_slope:+.1f}pp/10d "
                f"(sharply declining ≤ {slope_thresh:.0f})"
            )

    # F2: VIX rising and elevated
    if not vix_df.empty and "close" in vix_df.columns:
        sma_period = cfg.get("vix_sma_period", 21)
        if len(vix_df) >= sma_period:
            vix_now = float(vix_df["close"].iloc[-1])
            vix_sma = float(vix_df["close"].iloc[-sma_period:].mean())
            vix_thr = cfg.get("concern_vix_threshold", 22.0)
            if vix_now > vix_thr and vix_now > vix_sma:
                flags.append(
                    f"VIX {vix_now:.1f} > {vix_thr:.0f} AND rising vs 21-SMA {vix_sma:.1f}"
                )

    # F3: Financials sector specifically stressed
    if not breadth.empty:
        total_now = float(breadth.iloc[-1])
        fin_breadth = compute_sector_breadth_now(
            prices, universe, sectors_map, "FINANCIALS",
            period=cfg.get("trend_sma_period", 200),
        )
        if fin_breadth is not None:
            gap_thr = cfg.get("concern_fin_gap_threshold", 0.15)
            if (total_now - fin_breadth) >= gap_thr:
                flags.append(
                    f"Financials breadth {fin_breadth:.0%} "
                    f"vs total {total_now:.0%} (≥{gap_thr*100:.0f}pp gap = sector stress)"
                )

    return {"level": len(flags), "flags": flags}


def explain_gate(
    date_ts: pd.Timestamp,
    breadth: pd.Series, breadth_slope: pd.Series,
    sector_ratio: pd.Series, vix_calm: pd.Series, cfg: dict,
) -> dict:
    """Return diagnostics for a single date: which path opened the gate
    (or which were all closed). Useful for the scanner awareness line."""
    out: dict = {}
    if date_ts in breadth.index:
        out["breadth"] = float(breadth.loc[date_ts])
        out["A_strong_breadth"] = out["breadth"] >= cfg.get("regime_min_breadth", 0.50)
    if date_ts in breadth_slope.index:
        out["slope"] = float(breadth_slope.loc[date_ts])
        out["B_recovery"] = (
            out.get("breadth", 0) >= cfg.get("regime_min_breadth_slope_floor", 0.20)
            and out["slope"] >= cfg.get("regime_slope_min", 5.0)
        )
    if date_ts in sector_ratio.index:
        out["sector_ratio"] = float(sector_ratio.loc[date_ts])
        out["C_risk_on"] = out["sector_ratio"] >= cfg.get("regime_sector_min", 10.0)
    if date_ts in vix_calm.index:
        out["vix_calm"] = bool(vix_calm.loc[date_ts])
        out["D_vix_relax"] = (
            out["vix_calm"]
            and out.get("breadth", 0) >= cfg.get("regime_vix_relax_breadth", 0.40)
        )
    out["any_open"] = any(out.get(k, False) for k in
                          ("A_strong_breadth", "B_recovery", "C_risk_on", "D_vix_relax"))
    return out
