"""Thin shim importing the tools/ryner_backtest.py module so fortress.cli can
reach it without adding tools/ to PYTHONPATH or doing relative-import gymnastics.

The actual backtest logic lives in tools/ryner_backtest.py — keep that file
the source of truth and tweak it for rule changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.ryner_backtest import BACKTEST_DEFAULTS, run_backtest  # noqa: E402

__all__ = ["BACKTEST_DEFAULTS", "run_backtest"]
