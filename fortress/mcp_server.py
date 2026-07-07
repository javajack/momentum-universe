"""MCP server — expose the momentum + swing allocation stack to LLM clients.

Thin, read-only schema wrapper over `fortress.actions` so Claude Code, Codex
or any MCP client can query the technical system (picks, quantities, stops,
rotation days, regime, universe ranks) and layer its own diligence on top
(fundamentals, shareholding, news — things this codebase deliberately does
not model).

Run:  .venv/bin/python -m fortress.mcp_server        (stdio transport)

Design notes:
- stdout is the MCP protocol channel; every action call runs with stdout
  redirected to stderr because the scanners print human banners.
- NSE_UNIVERSE_DATA_DIR and the config path are resolved from the repo root
  (this file's location), so the server works from any client working dir.
- All tools are credential-free and read-only (vendored data only).
"""
from __future__ import annotations

import contextlib
import dataclasses
import enum
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = str(REPO_ROOT / "config.yaml")
os.environ.setdefault("NSE_UNIVERSE_DATA_DIR", str(REPO_ROOT / "data"))

if str(REPO_ROOT) not in sys.path:  # `tools.*` imports inside the actions
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# serialization
# ---------------------------------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / dates / numpy scalars to JSON types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name))
                for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v) for v in obj]
    item = getattr(obj, "item", None)  # numpy scalars
    if callable(item):
        try:
            return _to_jsonable(item())
        except Exception:
            pass
    return str(obj)


def _parse_date(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


@contextlib.contextmanager
def _quiet_stdout():
    """Route library prints to stderr — stdout belongs to the MCP protocol."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _load_config():
    from fortress.config import load_config
    return load_config(CONFIG_PATH)


# ---------------------------------------------------------------------------
# snapshot math (pure — unit tested)
# ---------------------------------------------------------------------------

def _snapshot_from_df(symbol: str, df) -> Dict[str, Any]:
    """Per-ticker technical context computed from a daily OHLCV DataFrame."""
    close = df["close"]
    c = float(close.iloc[-1])
    n = len(close)

    def _ret(bars: int) -> Optional[float]:
        if n <= bars:
            return None
        return float((c / close.iloc[-1 - bars] - 1) * 100)

    sma200 = float(close.iloc[-200:].mean()) if n >= 200 else None
    high_252 = float(close.iloc[-252:].max())
    tr = (df["high"] - df["low"]).iloc[-14:]
    atr14 = float(tr.mean()) if len(tr) else 0.0
    turnover = float((close * df["volume"]).iloc[-20:].mean()) if n >= 20 else None
    return {
        "symbol": symbol,
        "as_of": close.index[-1].date().isoformat(),
        "close": c,
        "ret_1m_pct": _ret(21),
        "ret_3m_pct": _ret(63),
        "ret_6m_pct": _ret(126),
        "ret_12m_pct": _ret(252),
        "above_200sma": (c > sma200) if sma200 is not None else None,
        "dist_200sma_pct": ((c / sma200 - 1) * 100) if sma200 is not None else None,
        "high_52w": high_252,
        "prox_52w_high": c / high_252 if high_252 else None,
        "atr14": atr14,
        "avg_turnover_20d": turnover,
        "history_days": n,
    }


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "momentum-universe",
        instructions=(
            "Read-only technical picks for Indian equities (NSE mid/smallcaps, "
            "point-in-time universe). Use swing_allocation_plan and "
            "momentum_allocation/momentum_scan to get the system's picks with "
            "quantities, stops and rotation days, then perform your own "
            "diligence (fundamentals, shareholding, news) on each ticker — "
            "this system is purely technical and models none of that. "
            "market_state gives the current regime; universe_lookup and "
            "stock_snapshot give per-ticker context."
        ),
    )

    @mcp.tool()
    def swing_allocation_plan(
        capital: float = 500_000,
        hb_slots: int = 3,
        rsi_slots: int = 2,
        as_of: Optional[str] = None,
    ) -> dict:
        """Swing sleeve plan: partition `capital` into equal slots across the
        two adopted scanners (high_base_52w breakouts x hb_slots, rsi2_pullback
        mean-reversion x rsi_slots) and return per-slot ticker, quantity,
        rupee allocation, suggested stop and forced-rotation horizon (trading
        days). The 3+2 default is the validated best deployment (nightlog
        Part 13). as_of: YYYY-MM-DD, default today."""
        from fortress import actions as A
        with _quiet_stdout():
            plan = A.swing_allocation_plan(
                capital, hb_slots=hb_slots, rsi_slots=rsi_slots,
                as_of=_parse_date(as_of), config_path=CONFIG_PATH,
            )
        return _to_jsonable(plan)

    @mcp.tool()
    def momentum_scan(top_n: int = 20) -> dict:
        """Top-N momentum-ranked stocks under the active strategy (default:
        regime_switched_momentum) on the v2 point-in-time universe, ranks
        201-600. Returns score, rank, 52w-high proximity, 6m/12m returns,
        volatility, daily turnover and 200SMA status per stock."""
        from fortress import actions as A
        with _quiet_stdout():
            res = A.momentum_scan(_load_config(), top_n=top_n)
        return {
            "strategy": res.strategy,
            "as_of": _to_jsonable(res.as_of),
            "universe_version": res.version,
            "rank_range": list(res.rank_range),
            "total_passing_filters": res.total_passing,
            "stocks": [{
                "ticker": s.ticker,
                "sector": s.sector,
                "score": s.score,
                "rank": s.rank,
                "percentile": s.percentile,
                "price": s.current_price,
                "prox_52w_high": s.high_52w_proximity,
                "ret_6m_pct": s.return_6m * 100,
                "ret_12m_pct": s.return_12m * 100,
                "volatility": s.volatility,
                "daily_turnover": s.daily_turnover,
                "above_200sma": s.above_200sma,
            } for s in res.stocks],
        }

    @mcp.tool()
    def emerging_scan(top_n: int = 15) -> dict:
        """Stocks EARLY in a momentum move (the pre-run complement to
        momentum_scan). Combines a point-in-time LIQUIDITY-RANK climb (turnover
        rank ~2y ago -> now) with EARLY price momentum (above 200SMA, breaking
        toward the 52-week high, 12-month return capped so already-parabolic
        names are excluded). Returns a ranked shortlist with each stock's rank
        trajectory, 3/6/12-month returns, 52w-high proximity, acceleration,
        volatility and turnover. Intended workflow: call this, then run your
        own fundamental/shareholding/news diligence on each pick before acting
        — the scan is purely technical."""
        from fortress import actions as A
        with _quiet_stdout():
            res = A.emerging_scan(_load_config(), top_n=top_n)
        return {
            "universe_version": res.version,
            "as_of": _to_jsonable(res.as_of),
            "rank_band": list(res.band),
            "candidates_scanned": res.candidates_scanned,
            "total_passing": res.total_passing,
            "stocks": [{
                "ticker": s.symbol,
                "sector": s.sector,
                "rank_now": s.rank_now,
                "rank_1y": s.rank_1y,
                "rank_2y": s.rank_2y,
                "new_entrant": s.new_entrant,
                "rank_climb": s.climb,
                "price": s.price,
                "ret_3m_pct": s.ret_3m_pct,
                "ret_6m_pct": s.ret_6m_pct,
                "ret_12m_pct": s.ret_12m_pct,
                "prox_52w_high": s.prox_52w_high,
                "accel_pct": s.accel_pct,
                "volatility": s.volatility,
                "daily_turnover": s.daily_turnover,
                "score": s.score,
            } for s in res.stocks],
        }

    @mcp.tool()
    def momentum_allocation(
        capital: float,
        top_n: Optional[int] = None,
        holdings: Optional[Dict[str, int]] = None,
    ) -> dict:
        """Momentum sleeve allocation: given capital (and optionally current
        holdings {symbol: qty}), return the target portfolio under the active
        strategy — weights, quantities, rupee values — plus the buy/sell
        orders to get there, with the current regime attached."""
        from fortress import actions as A
        with _quiet_stdout():
            plan = A.plan_rebalance(
                _load_config(), capital, holdings=holdings or {}, top_n=top_n)
        return _to_jsonable(plan)

    @mcp.tool()
    def market_state() -> dict:
        """Current market regime from the latest vendored data: regime label
        (BULLISH/NORMAL/CAUTION/DEFENSIVE), NIFTY 52-week position, VIX,
        3-month return, stress score, and the target equity/gold/cash split.
        The momentum default switches scoring on this regime (risk-on ->
        emerging, stress -> dual)."""
        from fortress import actions as A
        with _quiet_stdout():
            ms = A.current_market_state(_load_config())
        return _to_jsonable(ms)

    @mcp.tool()
    def universe_lookup(symbol: str, on_date: Optional[str] = None) -> dict:
        """Point-in-time universe rank for a symbol (turnover-based, v2
        momentum-grade filters) and whether it falls in the strategies' scan
        band. on_date: YYYY-MM-DD, default latest. Rank None = not in the
        ranked universe that day (illiquid, surveillance, or too new)."""
        from nse_universe import Universe
        cfg = _load_config()
        d = _parse_date(on_date) or date.today()
        with _quiet_stdout():
            u = Universe(version=cfg.universe.version)
            rank = u.rank(symbol.upper(), d)
        lo, hi = cfg.universe.rank_range
        return {
            "symbol": symbol.upper(),
            "date": d.isoformat(),
            "universe_version": cfg.universe.version,
            "rank": rank,
            "scan_band": [lo, hi],
            "in_scan_band": (rank is not None and lo <= rank <= hi),
        }

    @mcp.tool()
    def stock_snapshot(symbol: str) -> dict:
        """Per-ticker technical snapshot for diligence context: latest close,
        1/3/6/12-month returns, 200SMA status, 52-week-high proximity, ATR(14)
        and 20-day average turnover, from the vendored daily data."""
        from datetime import timedelta
        from fortress.nse_data_loader import load_historical_bulk
        sym = symbol.upper()
        with _quiet_stdout():
            data = load_historical_bulk(
                start=date.today() - timedelta(days=550),
                end=date.today(), symbols=[sym],
            )
        df = data.get(sym)
        if df is None or df.empty:
            return {"symbol": sym, "error": "no price data for symbol"}
        return _snapshot_from_df(sym, df)

    return mcp


def main() -> None:
    # The engine resolves config-relative files (stock-sectors.json,
    # market-metadata.json, ...) against the cwd; run the server exactly like
    # every other entry point — from the repo root.
    os.chdir(REPO_ROOT)
    build_server().run()


if __name__ == "__main__":
    main()
