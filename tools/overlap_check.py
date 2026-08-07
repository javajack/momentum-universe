"""Is the short-term rotation a REAL diversifier vs dual_momentum, or redundant?

Runs the live dual_momentum engine and the validated rotation config
(L=126 · N=5 · H=60 · -15% stop) on the same window, then measures:
  * monthly-return CORRELATION of the two equity curves
  * holdings OVERLAP (Jaccard + % of dual_mom names also held) at each
    dual_momentum rebalance date

High correlation + high overlap => redundant (same momentum bet, don't add).
Run:  .venv/bin/python tools/overlap_check.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/rakesh/work/momentum-universe")

from tools.short_term_sweep import _load, _precompute, CAPITAL, COST, START_I, TURNOVER_FLOOR

START, END = date(2016, 1, 1), date(2026, 8, 5)
L, N, H, STOP = 126, 5, 60, 0.15


def _rotation(C, retv, sigv, tradev):
    """The rotation config, recording daily equity + daily holdings (symbol sets)."""
    syms = list(C.columns)
    dates = list(C.index.date)
    n, nsym = retv.shape
    cash = float(CAPITAL); positions = {}
    eq_dates, eq_vals, hold_by_date = [], [], {}
    for i in range(START_I, n):
        for col, p in positions.items():
            r = retv[i, col]
            if not np.isnan(r):
                p[0] *= (1 + r); p[2] *= (1 + r); p[3] = max(p[3], p[2])
        for col in list(positions):
            if positions[col][2] - 1.0 <= -STOP:
                cash += positions[col][0] * (1 - COST); del positions[col]
        if (i - START_I) % H == 0:
            row = sigv[i]
            elig = np.where(~np.isnan(row) & tradev[i])[0]
            elig = elig[np.argsort(row[elig])[::-1]]
            target = set(elig[:N])
            for col in list(positions):
                if col not in target:
                    cash += positions[col][0] * (1 - COST); del positions[col]
            free = N - len(positions)
            if free > 0:
                total = cash + sum(p[0] for p in positions.values()); w = total / N
                for col in elig[:N]:
                    if col in positions or free <= 0 or cash <= 1:
                        continue
                    buy = min(w, cash); cash -= buy + buy * COST
                    positions[col] = [buy, i, 1.0, 1.0]; free -= 1
        eq_dates.append(dates[i]); eq_vals.append(cash + sum(p[0] for p in positions.values()))
        hold_by_date[dates[i]] = {syms[c] for c in positions}
    return pd.Series(eq_vals, index=pd.to_datetime(eq_dates)), hold_by_date


def _monthly_corr(a: pd.Series, b: pd.Series) -> tuple[float, int]:
    am = a.resample("ME").last().pct_change().dropna()
    bm = b.resample("ME").last().pct_change().dropna()
    j = am.index.intersection(bm.index)
    if len(j) < 6:
        return float("nan"), len(j)
    return float(np.corrcoef(am[j], bm[j])[0, 1]), len(j)


def main():
    print("overlap check — dual_momentum vs rotation (L=126·N=5·H=60·stop15)\n", flush=True)
    from fortress.config import load_config
    from fortress import actions as A

    cfg = load_config("config.yaml")
    cfg = A.apply_selection(cfg, strategy="dual_momentum")
    print("  running dual_momentum engine ...", flush=True)
    r = A.run_backtest(cfg, START, END)
    dm_eq = r.equity_curve
    dm_eq.index = pd.to_datetime(dm_eq.index)
    dm_holds = {pd.Timestamp(rec.date).date(): {s for s, *_ in rec.equity_picks}
                for rec in r.rebalance_trail if rec.equity_picks}
    print(f"  dual_momentum: CAGR {r.cagr*100:.1f}%  MaxDD {r.max_drawdown*100:.1f}%  "
          f"{len(dm_holds)} rebalances", flush=True)

    print("  running rotation ...", flush=True)
    C, V = _load(START, END)
    retv, momv, tradev = _precompute(C, V)
    rot_eq, rot_holds = _rotation(C, retv, momv[L], tradev)
    rc = (rot_eq.iloc[-1] / CAPITAL) ** (365.25 / (rot_eq.index[-1] - rot_eq.index[0]).days) - 1
    print(f"  rotation:      CAGR {rc*100:.1f}%  {len(rot_holds)} days tracked\n", flush=True)

    corr, nmo = _monthly_corr(dm_eq, rot_eq)
    print(f"MONTHLY-RETURN CORRELATION: {corr:.2f}  (over {nmo} common months)")

    # holdings overlap at each dual_momentum rebalance date (nearest rotation day <= date)
    rot_days = sorted(rot_holds)
    jac, namepct, samples = [], [], 0
    for d, dmset in sorted(dm_holds.items()):
        prior = [x for x in rot_days if x <= d]
        if not prior or not dmset:
            continue
        rset = rot_holds[prior[-1]]
        if not rset:
            continue
        inter = len(dmset & rset)
        jac.append(inter / len(dmset | rset))
        namepct.append(inter / len(dmset))
        samples += 1
    print(f"HOLDINGS OVERLAP (over {samples} dual_momentum rebalances):")
    print(f"  mean Jaccard              : {np.mean(jac):.2f}")
    print(f"  mean % of dual_mom names   also held by rotation: {np.mean(namepct)*100:.0f}%")
    print(f"\nVERDICT INPUT: corr {corr:.2f}, name-overlap {np.mean(namepct)*100:.0f}% — "
          f"{'REDUNDANT (same bet)' if (corr>0.75 or np.mean(namepct)>0.4) else 'some diversification'}")


if __name__ == "__main__":
    main()
