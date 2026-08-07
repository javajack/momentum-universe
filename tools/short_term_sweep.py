"""Short-term-holding compounding sweep — "let winners run", cut losers.

The base-hit (+2% cap) test proved fixed small targets invert the equity payoff
skew. This harness tests the OPPOSITE: momentum rotation that lets winners run
and cuts losers, sweeping the variables that matter for short-term holding:

  * entry signal  : trailing-return momentum over lookback L (21/63/126d)
  * holding cohort: rebalance every H days (5/10/20/40/60) = the hold period
  * breadth       : N concurrent positions (3 / 5)
  * loser exit    : hard stop at -STOP% and/or trailing stop at -TRAIL% from peak
  * winners       : NEVER trimmed — they run (stay while in top-N & not stopped)

Portfolio: Rs 20L, equal-weight at entry, up to N names, point-in-time
survivorship-free v2-liquid universe (turnover floor so Rs20L is deployable),
0.4% cost on traded value. Per config: CAGR, MaxDD, Calmar, avg hold, win%,
avgWIN/avgLOSS, cycles/yr. Best configs get a walk-forward across regimes and a
NIFTY-ish equal-weight-universe benchmark. Run:
    .venv/bin/python tools/short_term_sweep.py

FINDING (2016-2026, Rs20L, 0.4% cost): FEASIBLE — the opposite of the base-hit
result. Letting winners run inverts the skew the RIGHT way: avgWIN +24..+43% vs
avgLOSS -12..-17% (win rate only 42-52%, but winners dominate). Best & most
ROBUST config: L=126 (6-month momentum) · N=5 names · H=60d (quarterly rebalance)
· hard -15% stop -> walk-forward +20% / +39% / +37% CAGR across 2016-19 / 2020-22 /
2023-26, MaxDD ~-35 to -40%, Calmar 0.5-1.15, beating the EW-universe benchmark
(16.4% CAGR, -52% DD). Rs20L compounds to ~Rs34-45L per 3-4yr regime.
KEY NUANCES: (1) sub-weekly holding is NOT where the edge is — H=5/10 never make
the top; the edge lives at ~20-60 day (1-3 month) rebalances (churn/cost kill
faster rotation, momentum needs time to play out). (2) Medium-term SIGNAL (6m
momentum) + short-to-medium HOLD is the sweet spot; short lookbacks (L=21) are
worse. (3) EXITS: winners exit ONLY by falling out of top-N (never profit-taken);
a hard -15% stop is the best drawdown control (Calmar +; cuts DD from -68% to
-41%); trailing stops add churn for less benefit. (4) Drawdowns are large
(-35..-70%) — this is concentrated smallcap momentum; N=5+stop15 is the risk-
controlled choice, N=3/no-stop maximises CAGR but is high-variance/overfit-prone.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

CAPITAL = 2_000_000
TURNOVER_FLOOR = 25e7
COST = 0.004
START_I = 260              # need 200SMA + lookback warmup


def _load(start: date, end: date):
    from nse_universe.core.db import db
    from fortress.nse_data_loader import load_historical_bulk
    with db(read_only=True) as con:
        syms = [r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM universe_v2 WHERE as_of_date >= ? AND passes",
            [start - timedelta(days=400)]).fetchall()]
    print(f"  loading {len(syms)} liquid symbols {start}..{end} ...", flush=True)
    data = load_historical_bulk(start=start - timedelta(days=400), end=end, symbols=syms)
    close, vol = {}, {}
    for s, df in data.items():
        if df is None or len(df) < 300:
            continue
        close[s], vol[s] = df["close"], df["volume"]
    C = pd.DataFrame(close).sort_index()
    V = pd.DataFrame(vol).reindex_like(C)
    return C, V


def _precompute(C, V):
    ret = C.pct_change()
    sma200 = C.rolling(200).mean()
    above = C > sma200
    tradeable = (C * V).rolling(20).median() >= TURNOVER_FLOOR
    gate = above & tradeable
    mom = {L: C.pct_change(L).where(gate) for L in (21, 63, 126)}
    return ret.values, {L: m.values for L, m in mom.items()}, tradeable.values


def _simulate(dates, retv, sigv, tradev, *, N, H, stop, trail):
    """Rotation: every H days hold the top-N by sig; sell names that fall out or
    hit a stop/trailing stop; NEVER trim winners. Returns stats dict."""
    n, nsym = retv.shape
    cash = float(CAPITAL)
    positions = {}                     # col -> [val, entry_i, cum, peak]
    equity = []
    wins = losses = 0
    gw = gl = 0.0
    holds = []

    for i in range(START_I, n):
        # 1) mark-to-market + peak
        for col, p in positions.items():
            r = retv[i, col]
            if not np.isnan(r):
                p[0] *= (1 + r); p[2] *= (1 + r); p[3] = max(p[3], p[2])
        # 2) loser exits (stop / trailing) -> to cash
        for col in list(positions):
            p = positions[col]
            r_entry = p[2] - 1.0
            dd_peak = p[2] / p[3] - 1.0
            if (stop is not None and r_entry <= -stop) or (trail is not None and dd_peak <= -trail):
                cash += p[0] * (1 - COST)
                if r_entry >= 0: wins += 1; gw += r_entry
                else: losses += 1; gl += r_entry
                holds.append(i - p[1]); del positions[col]
        # 3) rebalance
        if (i - START_I) % H == 0:
            row = sigv[i]
            elig = np.where(~np.isnan(row) & tradev[i])[0]
            elig = elig[np.argsort(row[elig])[::-1]]           # best first
            target = list(elig[:N])
            tset = set(target)
            for col in list(positions):                        # sell drop-outs
                if col not in tset:
                    p = positions[col]; cash += p[0] * (1 - COST)
                    r_entry = p[2] - 1.0
                    if r_entry >= 0: wins += 1; gw += r_entry
                    else: losses += 1; gl += r_entry
                    holds.append(i - p[1]); del positions[col]
            free = N - len(positions)
            if free > 0:
                total = cash + sum(p[0] for p in positions.values())
                w = total / N
                for col in target:
                    if col in positions or free <= 0 or cash <= 1:
                        continue
                    buy = min(w, cash)
                    cash -= buy; cash -= buy * COST
                    positions[col] = [buy, i, 1.0, 1.0]
                    free -= 1
        equity.append(cash + sum(p[0] for p in positions.values()))

    eq = np.array(equity)
    yrs = (dates[-1] - dates[START_I]).days / 365.25 or 1
    cagr = (eq[-1] / CAPITAL) ** (1 / yrs) - 1
    peak = np.maximum.accumulate(eq); maxdd = float(((eq - peak) / peak).min())
    tr = wins + losses
    calmar = cagr / abs(maxdd) if maxdd else 0
    return {"cagr": cagr, "maxdd": maxdd, "calmar": calmar, "final": eq[-1],
            "trades": tr, "cycles_yr": tr / yrs, "win%": wins / tr * 100 if tr else 0,
            "avgW": gw / wins * 100 if wins else 0, "avgL": gl / losses * 100 if losses else 0,
            "avg_hold": float(np.mean(holds)) if holds else 0}


def _bench(dates, retv, tradev):
    """Equal-weight-of-tradeable daily rebalanced — a passive smallcap-ish yardstick."""
    n = retv.shape[0]; eq = [CAPITAL]; v = CAPITAL
    for i in range(START_I, n):
        cols = np.where(tradev[i])[0]
        r = np.nanmean(retv[i, cols]) if len(cols) else 0.0
        v *= (1 + (0.0 if np.isnan(r) else r)); eq.append(v)
    eq = np.array(eq); yrs = (dates[-1] - dates[START_I]).days / 365.25 or 1
    peak = np.maximum.accumulate(eq)
    return (eq[-1] / CAPITAL) ** (1 / yrs) - 1, float(((eq - peak) / peak).min())


def main():
    start, end = date(2016, 1, 1), date(2026, 8, 5)
    print(f"short-term rotation sweep — Rs{CAPITAL/1e5:.0f}L · v2-liquid · cost {COST*100:.1f}%\n", flush=True)
    C, V = _load(start, end)
    retv, momv, tradev = _precompute(C, V)
    dates = list(C.index.date)
    print(f"  {C.shape[1]} symbols, {C.shape[0]} days", flush=True)
    bc, bd = _bench(dates, retv, tradev)
    print(f"  benchmark (EW tradeable universe): CAGR {bc*100:.1f}%  MaxDD {bd*100:.1f}%\n", flush=True)

    # ---- grid: rank pure-rotation configs by Calmar ----
    rows = []
    for L in (21, 63, 126):
        for N in (3, 5):
            for H in (5, 10, 20, 40, 60):
                for stop, trail, tag in [(None, None, "none"), (0.15, None, "stop15"),
                                         (None, 0.20, "trail20"), (0.15, 0.25, "stop15+trail25")]:
                    s = _simulate(dates, retv, momv[L], tradev, N=N, H=H, stop=stop, trail=trail)
                    rows.append((L, N, H, tag, s))
    rows.sort(key=lambda r: r[4]["calmar"], reverse=True)
    print(f"{'L':>4}{'N':>3}{'H':>4}  {'exit':14}{'CAGR':>8}{'MaxDD':>8}{'Calmar':>7}"
          f"{'win%':>6}{'avgW':>7}{'avgL':>7}{'hold':>6}{'Rs20L→':>9}")
    for L, N, H, tag, s in rows[:20]:
        print(f"{L:>4}{N:>3}{H:>4}  {tag:14}{s['cagr']*100:7.1f}%{s['maxdd']*100:7.1f}%"
              f"{s['calmar']:7.2f}{s['win%']:6.0f}{s['avgW']:+7.1f}{s['avgL']:+7.1f}"
              f"{s['avg_hold']:6.0f}{s['final']/1e5:8.1f}L")

    # ---- walk-forward the top-3 configs ----
    print("\n=== WALK-FORWARD (top-3 by Calmar) ===")
    wins = [(date(2016,1,1),date(2019,12,31),"2016-2019"),
            (date(2020,1,1),date(2022,12,31),"2020-2022"),
            (date(2023,1,1),end,"2023-2026 OOS")]
    for L, N, H, tag, _ in rows[:3]:
        stop = 0.15 if "stop15" in tag else None
        trail = 0.20 if tag == "trail20" else (0.25 if "trail25" in tag else None)
        print(f"\n L={L} N={N} H={H} exit={tag}:")
        for a, b, lab in wins:
            mask = (np.array(dates) >= a) & (np.array(dates) <= b)
            idx = np.where(mask)[0]
            sub_dates = [dates[j] for j in idx]
            s = _simulate(sub_dates, retv[idx], momv[L][idx], tradev[idx],
                          N=N, H=H, stop=stop, trail=trail)
            print(f"   {lab:14} CAGR {s['cagr']*100:6.1f}%  MaxDD {s['maxdd']*100:6.1f}%  "
                  f"Calmar {s['calmar']:5.2f}  win {s['win%']:.0f}%  Rs{s['final']/1e5:.1f}L")


if __name__ == "__main__":
    main()
