"""Emerging Momentum Strategy for FORTRESS MOMENTUM.

Catches stocks **transitioning into momentum** — fresh breakouts above 52W
high with volume confirmation, scored on a velocity-weighted blend of 1m,
3m, 6m, 12m vol-adjusted returns. Same regime / exits / stops machinery as
AdaptiveDualMomentumStrategy; only the scoring layer differs.

Score formula:
    velocity_nms = w1·R1m/σ + w3·R3m/σ + w6·R6m/σ + w12·R12m/σ
                   (skip-5 applied to 12m component only; vol-adjusted)
    breakout_boost = 1.20  if proximity ≥ 0.95 AND days_since_52w_high ≤ 10
    volume_boost   = 1.10  if vol_ratio(20d/50d) ≥ 1.5 AND close > 50d_high
    score          = velocity_nms × breakout_boost × volume_boost
                     × (1 + rs_weight·(RS-1))    # RS overlay inherited

Rationale: classic 12-1 momentum is *lagging* by design — it captures
established trends. The velocity weighting + breakout/volume boosts surface
names that are *just entering* the high-momentum regime, complementary to
the dual-momentum signal.

Tradeoff: more sensitive = higher whipsaw risk in sideways markets. The
inherited regime detection + tighter `max_days_without_gain` (45 vs 60)
guard the downside.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional

import numpy as np
import pandas as pd

from ..indicators import (
    calculate_exhaustion_score,
    calculate_momentum_acceleration,
    calculate_relative_strength,
)
from .adaptive_dual_momentum import AdaptiveDualMomentumStrategy
from .base import ExitSignal, StockScore
from .registry import StrategyRegistry

if TYPE_CHECKING:
    from ..config import Config
    from ..market_data import MarketDataProvider
    from ..universe import Universe

logger = logging.getLogger(__name__)


def _compute_emerging_score(
    prices: pd.Series,
    volumes: pd.Series,
    em_cfg: dict,
) -> Optional[dict]:
    """Compute the emerging-momentum score + diagnostic fields for one stock.

    Returns a dict with score + intermediate metrics, or None if data is
    insufficient. Mirrors the shape of NMSResult so the rest of rank_stocks
    can consume it interchangeably.

    em_cfg expects keys:
        weight_1m, weight_3m, weight_6m, weight_12m
        lookback_1m, lookback_3m, lookback_6m, lookback_12m
        lookback_volatility, skip_recent_days_12m, min_volatility_floor
        breakout_proximity_min, breakout_max_days_since_high,
        breakout_score_multiplier
        volume_ratio_20_50_min, volume_score_multiplier
    """
    lb_12m = em_cfg["lookback_12m"]
    skip_12m = em_cfg["skip_recent_days_12m"]
    if len(prices) < lb_12m + skip_12m:
        return None

    # Two return windows: skip-5 series (for 12m anti-reversal) and full series
    # (for short windows that we WANT recent action in).
    full_prices = prices
    skipped_prices = prices.iloc[:-skip_12m] if skip_12m > 0 else prices

    current_price = full_prices.iloc[-1]

    # Compute returns
    def _ret(p: pd.Series, window: int) -> float:
        if len(p) < window:
            return 0.0
        past = p.iloc[-window]
        if past <= 0:
            return 0.0
        return (p.iloc[-1] / past) - 1.0

    ret_1m = _ret(full_prices, em_cfg["lookback_1m"])
    ret_3m = _ret(full_prices, em_cfg["lookback_3m"])
    ret_6m = _ret(full_prices, em_cfg["lookback_6m"])
    ret_12m = _ret(skipped_prices, em_cfg["lookback_12m"])  # skip-5 applied here

    # Volatility (annualized, log returns, single window — keeps math comparable)
    log_returns = np.log(full_prices / full_prices.shift(1)).dropna()
    lb_vol = em_cfg["lookback_volatility"]
    if len(log_returns) >= lb_vol:
        vol_ann = log_returns.iloc[-lb_vol:].std() * np.sqrt(252)
    elif len(log_returns) > 0:
        vol_ann = log_returns.std() * np.sqrt(252)
    else:
        vol_ann = 0.20
    vol_floor = em_cfg["min_volatility_floor"]
    vol_eff = max(float(vol_ann) if vol_ann is not None else vol_floor, vol_floor)

    # Velocity NMS
    velocity_nms = (
        em_cfg["weight_1m"]  * ret_1m  / vol_eff
        + em_cfg["weight_3m"]  * ret_3m  / vol_eff
        + em_cfg["weight_6m"]  * ret_6m  / vol_eff
        + em_cfg["weight_12m"] * ret_12m / vol_eff
    )

    # 52W high proximity + days since 52w high
    if len(full_prices) >= 252:
        last_52w = full_prices.iloc[-252:]
    else:
        last_52w = full_prices
    high_52w = last_52w.max()
    high_idx = last_52w.idxmax()
    days_since_high = int((last_52w.index[-1] - high_idx).days) if high_52w > 0 else 999
    prox_52w = float(current_price / high_52w) if high_52w > 0 else 0.0

    # 50-day EMA + 200-day SMA flags
    ema_50 = full_prices.ewm(span=50, adjust=False).mean().iloc[-1]
    above_50ema = bool(current_price > ema_50)
    if len(full_prices) >= 200:
        sma_200 = full_prices.iloc[-200:].mean()
        above_200sma = bool(current_price > sma_200)
    else:
        above_200sma = True

    # Volume metrics
    if len(volumes) >= 50:
        avg_vol_20 = volumes.iloc[-20:].mean()
        avg_vol_50 = volumes.iloc[-50:].mean()
        volume_surge = float(avg_vol_20 / avg_vol_50) if avg_vol_50 > 0 else 1.0
    else:
        volume_surge = 1.0

    # 20-day average daily turnover
    if len(full_prices) >= 20 and len(volumes) >= 20:
        daily_turnover = float((full_prices.iloc[-20:] * volumes.iloc[-20:]).mean())
    else:
        daily_turnover = 0.0

    # Boost flags (force Python bool — numpy scalars compare with `is` weirdly)
    breakout_boost_applies = bool(
        prox_52w >= em_cfg["breakout_proximity_min"]
        and days_since_high <= em_cfg["breakout_max_days_since_high"]
    )

    # 50d_high test for volume boost
    high_50d = full_prices.iloc[-50:].max() if len(full_prices) >= 50 else high_52w
    volume_boost_applies = bool(
        volume_surge >= em_cfg["volume_ratio_20_50_min"]
        and current_price > high_50d * 0.99  # within 1% of 50d high counts as "above"
    )

    breakout_mult = em_cfg["breakout_score_multiplier"] if breakout_boost_applies else 1.0
    volume_mult = em_cfg["volume_score_multiplier"] if volume_boost_applies else 1.0
    score = velocity_nms * breakout_mult * volume_mult

    return {
        "score": float(score),
        "velocity_nms": float(velocity_nms),
        "breakout_boost": breakout_boost_applies,
        "volume_boost": volume_boost_applies,
        "ret_1m": float(ret_1m),
        "ret_3m": float(ret_3m),
        "ret_6m": float(ret_6m),
        "ret_12m": float(ret_12m),
        "volatility": float(vol_eff),
        "high_52w_proximity": prox_52w,
        "days_since_52w_high": days_since_high,
        "above_50ema": above_50ema,
        "above_200sma": above_200sma,
        "volume_surge": volume_surge,
        "daily_turnover": daily_turnover,
        "current_price": float(current_price),
    }


class EmergingMomentumStrategy(AdaptiveDualMomentumStrategy):
    """Velocity-weighted momentum with breakout + volume-confirmed scoring.

    Inherits from AdaptiveDualMomentumStrategy to reuse:
      - regime detection (CAUTION/DEFENSIVE/BULLISH + stress score)
      - recovery / bull-recovery / crash-recovery state machines
      - tiered stop-loss configuration
      - trend-break exits
      - sector momentum filter
      - falling-knife pre-screen + 12m-SMA gate
      - partial-filter-pass logic in bullish/recovery regimes

    Overrides only:
      - `name` / `description`
      - `rank_stocks` (custom scoring; identical entry-filter + portfolio logic)
    """

    aliases = ("vanguard",)  # short alias

    @property
    def name(self) -> str:
        return "emerging_momentum"

    @property
    def description(self) -> str:
        return (
            "Velocity-weighted momentum (1m+3m+6m+12m) with breakout + "
            "volume-confirmed score boosts — catches early-stage momentum"
        )

    def _get_emerging_config_values(self) -> dict:
        """Return emerging_momentum config block as a dict."""
        defaults = {
            "weight_1m": 0.20, "weight_3m": 0.30, "weight_6m": 0.30, "weight_12m": 0.20,
            "skip_recent_days_12m": 5,
            "lookback_1m": 21, "lookback_3m": 63, "lookback_6m": 126, "lookback_12m": 252,
            "lookback_volatility": 126,
            "min_volatility_floor": 0.10,
            "breakout_proximity_min": 0.95,
            "breakout_max_days_since_high": 10,
            "breakout_score_multiplier": 1.20,
            "volume_ratio_20_50_min": 1.5,
            "volume_score_multiplier": 1.10,
            "min_score_percentile": 80.0,
            "min_52w_high_prox": 0.85,
            "min_daily_turnover": 20_000_000,
            "max_days_without_gain": 45,
            "min_gain_threshold": 0.10,
            "min_hold_percentile": 50.0,
        }
        if self._config is not None and hasattr(self._config, "emerging_momentum"):
            em = self._config.emerging_momentum
            for key in defaults:
                if hasattr(em, key):
                    defaults[key] = getattr(em, key)
        return defaults

    def check_exit_triggers(
        self,
        ticker: str,
        entry_price: float,
        current_price: float,
        peak_price: float,
        days_held: int,
        stock_score: Optional[StockScore],
        nms_percentile: float,
    ) -> ExitSignal:
        """Inherit dual_momentum's exits (hard stop, trailing, trend break,
        RS floor) and add a time-decay rule on top:

            days_held >= max_days_without_gain (45)  AND  gain < min_gain_threshold (10%)
            → exit at next rebalance

        Rationale: emerging-momentum scoring is more sensitive than classic
        12-1 NMS, so candidates that fail to deliver in 45 days are likely
        false-positives (whipsaws from volume/breakout boosts that didn't
        compound). Cutting them frees capital for the next breakout cycle.
        """
        # 1. Run dual_momentum's exit ladder first — keeps the proven
        #    hard-stop / trailing-stop / trend-break / RS-floor semantics.
        base_signal = super().check_exit_triggers(
            ticker=ticker,
            entry_price=entry_price,
            current_price=current_price,
            peak_price=peak_price,
            days_held=days_held,
            stock_score=stock_score,
            nms_percentile=nms_percentile,
        )
        if base_signal.should_exit:
            return base_signal

        # 2. Time-decay check (only if super() said hold).
        em_cfg = self._get_emerging_config_values()
        max_days = em_cfg["max_days_without_gain"]
        min_gain = em_cfg["min_gain_threshold"]
        gain = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0
        if days_held >= max_days and gain < min_gain:
            return ExitSignal(
                should_exit=True,
                reason=(
                    f"Time decay: {days_held}d held, gain {gain:.1%} "
                    f"< target {min_gain:.0%}"
                ),
                exit_type="time_decay",
                urgency="next_rebalance",
            )
        return base_signal

    def rank_stocks(
        self,
        as_of_date: datetime,
        universe: "Universe",
        market_data: "MarketDataProvider",
        filter_entry: bool = True,
    ) -> List[StockScore]:
        """Rank stocks using emerging-momentum scoring.

        Pipeline is identical to AdaptiveDualMomentumStrategy.rank_stocks
        except the per-stock score is computed via _compute_emerging_score
        (velocity NMS × breakout boost × volume boost) instead of NMS.
        """
        cfg = self._get_config_values()
        em_cfg = self._get_emerging_config_values()
        adapted = self._get_adapted_parameters()

        # Fetch window must cover the longest lookback (12m) + skip + buffer
        max_lookback = max(em_cfg["lookback_12m"], 378)
        lookback_days = max_lookback + em_cfg["skip_recent_days_12m"] + 30
        from_date = as_of_date - timedelta(days=int(lookback_days * 1.5))

        # Sector momentum filter — soft penalty for bottom sectors
        bottom_sectors = self._get_bottom_sectors(as_of_date, universe, market_data)

        # Benchmark prices for RS overlay (inherited from dual_momentum)
        benchmark_prices = None
        try:
            bench_df = market_data.get_historical(
                symbol="NIFTY 50",
                from_date=from_date,
                to_date=as_of_date,
                interval="day",
                check_quality=False,
            )
            if bench_df is not None and len(bench_df) >= 252:
                benchmark_prices = bench_df["close"]
        except Exception as e:
            logger.warning(f"Could not get benchmark data: {e}")

        scored_stocks: List[StockScore] = []

        for stock in universe.get_all_stocks():
            ticker = stock.ticker
            if ticker in self._excluded_symbols:
                continue
            try:
                df = market_data.get_historical(
                    symbol=stock.zerodha_symbol,
                    from_date=from_date,
                    to_date=as_of_date,
                    interval="day",
                    check_quality=False,
                )
                if df is None or df.empty:
                    continue
                prices = df["close"]
                volumes = df["volume"]
                if len(prices) < em_cfg["lookback_12m"]:
                    continue
            except Exception:
                continue

            # Falling-knife pre-screen — inherited config knob
            fk_cutoff = cfg.get("falling_knife_6m_cutoff", -0.30)
            if fk_cutoff < 0 and len(prices) >= em_cfg["lookback_6m"]:
                ret_6m_raw = prices.iloc[-1] / prices.iloc[-em_cfg["lookback_6m"]] - 1
                if ret_6m_raw < fk_cutoff:
                    continue

            # Absolute-momentum gate (12m SMA) — keep if dual_momentum has it
            if cfg.get("require_above_12m_sma", False) and len(prices) >= em_cfg["lookback_12m"]:
                sma_12m = prices.iloc[-em_cfg["lookback_12m"]:].mean()
                if prices.iloc[-1] < sma_12m:
                    continue

            # ---- The DIFF: compute emerging score instead of NMS ----
            em_result = _compute_emerging_score(prices, volumes, em_cfg)
            if em_result is None:
                continue

            # RS overlay (inherited from dual_momentum)
            rs_result = None
            if benchmark_prices is not None:
                rs_result = calculate_relative_strength(
                    stock_prices=prices,
                    benchmark_prices=benchmark_prices,
                )
            rs_composite = rs_result.rs_composite if rs_result else 1.0

            # Distance from 50 EMA for trend-break exit (inherited)
            exhaustion_result = calculate_exhaustion_score(prices, volumes)
            distance_from_50ema = (
                exhaustion_result.distance_from_50ema
                if exhaustion_result is not None else 0.0
            )

            # ---- Entry filters (mirror dual_momentum) ----
            filter_reasons: list[str] = []
            filter_passes = {"score": True, "rs": True, "ema": True,
                              "high_52w": True, "turnover": True, "sma200": True}

            # 1. Absolute: emerging score > 0
            if em_result["score"] <= 0:
                filter_passes["score"] = False
                filter_reasons.append(f"emerging_score {em_result['score']:.2f} <= 0")

            # 2. RS threshold (adaptive)
            rs_threshold = adapted.min_rs_threshold
            if rs_composite < rs_threshold:
                filter_passes["rs"] = False
                filter_reasons.append(f"RS {rs_composite:.2f} < {rs_threshold:.2f}")

            # 3. 50-EMA with adaptive buffer
            if distance_from_50ema < -adapted.ema_buffer:
                filter_passes["ema"] = False
                filter_reasons.append(
                    f"Below 50-EMA: {distance_from_50ema:.1%} (buffer: {adapted.ema_buffer * 100:.0f}%)"
                )
            elif not em_result["above_50ema"] and adapted.ema_buffer == 0:
                filter_passes["ema"] = False
                filter_reasons.append("Below 50-EMA")

            # 4. 52W high proximity (adaptive)
            if em_result["high_52w_proximity"] < adapted.min_52w_high_prox:
                filter_passes["high_52w"] = False
                filter_reasons.append(
                    f"52W high: {em_result['high_52w_proximity']:.0%} < {adapted.min_52w_high_prox:.0%}"
                )

            # 5. Liquidity
            if em_result["daily_turnover"] < em_cfg["min_daily_turnover"]:
                filter_passes["turnover"] = False
                filter_reasons.append(
                    f"Turnover {em_result['daily_turnover'] / 1e6:.1f}M < "
                    f"{em_cfg['min_daily_turnover'] / 1e6:.1f}M"
                )

            # 6. 200-SMA trend (skipped during crash recovery — inherited)
            if not adapted.skip_200sma_check and not em_result["above_200sma"]:
                filter_passes["sma200"] = False
                filter_reasons.append("Below 200-SMA")

            # 7. Sector momentum soft penalty (inherited)
            in_bottom_sector = bool(bottom_sectors and stock.sector in bottom_sectors)
            if in_bottom_sector:
                filter_reasons.append(
                    f"Sector '{stock.sector}' in bottom {len(bottom_sectors)} (penalty)"
                )

            # Partial-filter-pass logic in bullish/recovery
            core_passed = sum(
                [filter_passes["score"], filter_passes["rs"], filter_passes["ema"]]
            )
            all_passed = all(filter_passes.values())
            is_b_or_r = self._is_in_bullish_or_recovery()
            passes = False
            if all_passed:
                passes = True
            elif cfg.get("use_partial_filter_passing", True) and is_b_or_r:
                min_required = cfg.get("partial_filter_min_passed", 2)
                if core_passed >= min_required and filter_passes["turnover"]:
                    passes = True
                    filter_reasons.append(f"Partial pass: {core_passed}/3 core filters")

            # ---- Final score: emerging × RS overlay ----
            base_score = em_result["score"]
            rs_adjustment = (rs_composite - 1.0) * cfg.get("rs_weight", 0.25)
            score = base_score * (1.0 + rs_adjustment)

            # Penalties
            if passes and not all_passed:
                score *= 1.0 - cfg.get("partial_filter_score_penalty", 0.04)
            if in_bottom_sector:
                score *= 1.0 - cfg.get("sector_momentum_penalty", 0.15)

            # Momentum-deceleration penalty (I3, inherited)
            decel_penalty = cfg.get("deceleration_penalty", 0.0)
            if decel_penalty > 0:
                accel = calculate_momentum_acceleration(prices, short_period=21, medium_period=63)
                if accel < cfg.get("deceleration_threshold", 0.85):
                    score *= 1.0 - decel_penalty

            stock_score = StockScore(
                ticker=ticker,
                sector=stock.sector,
                sub_sector=stock.sub_sector,
                zerodha_symbol=stock.zerodha_symbol,
                name=stock.name,
                score=score,
                passes_entry_filters=passes,
                filter_reasons=filter_reasons,
                return_6m=em_result["ret_6m"],
                return_12m=em_result["ret_12m"],
                volatility=em_result["volatility"],
                high_52w_proximity=em_result["high_52w_proximity"],
                above_50ema=em_result["above_50ema"],
                above_200sma=em_result["above_200sma"],
                volume_surge=em_result["volume_surge"],
                daily_turnover=em_result["daily_turnover"],
                current_price=em_result["current_price"],
                extra_metrics={
                    "emerging_score": em_result["score"],
                    "velocity_nms": em_result["velocity_nms"],
                    "breakout_boost": float(em_result["breakout_boost"]),
                    "volume_boost": float(em_result["volume_boost"]),
                    "days_since_52w_high": float(em_result["days_since_52w_high"]),
                    "ret_1m": em_result["ret_1m"],
                    "ret_3m": em_result["ret_3m"],
                    "rs_composite": rs_composite,
                    "rs_21d": rs_result.rs_21d if rs_result else 1.0,
                    "rs_63d": rs_result.rs_63d if rs_result else 1.0,
                    "distance_from_50ema": distance_from_50ema,
                    "adapted_rs_threshold": rs_threshold,
                    "adapted_52w_threshold": adapted.min_52w_high_prox,
                },
            )
            scored_stocks.append(stock_score)

        # Sort by score (descending)
        scored_stocks.sort(key=lambda x: x.score, reverse=True)

        if filter_entry:
            scored_stocks = [s for s in scored_stocks if s.passes_entry_filters]

        # Assign ranks and percentiles
        total = len(scored_stocks)
        for i, stock in enumerate(scored_stocks):
            stock.rank = i + 1
            stock.percentile = 100 * (total - i) / total if total > 0 else 0
        return scored_stocks


# Auto-register
StrategyRegistry.register(EmergingMomentumStrategy)
