"""Test the pure alpha/beta regression in the climbers backtest harness."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.climbers_backtest import alpha_beta


def test_alpha_beta_recovers_known_line():
    # strat = 2*bench + 0.01 exactly -> beta=2, alpha=0.01, R2=1
    bench = [-0.02, 0.01, 0.03, -0.01, 0.05, 0.00]
    strat = [2 * b + 0.01 for b in bench]
    alpha, beta, r2 = alpha_beta(strat, bench)
    assert abs(beta - 2.0) < 1e-6
    assert abs(alpha - 0.01) < 1e-6
    assert abs(r2 - 1.0) < 1e-6


def test_alpha_beta_degenerate_returns_zeros():
    # constant benchmark (no variance) -> can't regress
    assert alpha_beta([0.01, 0.02], [0.0, 0.0]) == (0.0, 0.0, 0.0)
