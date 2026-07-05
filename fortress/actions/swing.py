"""Swing scanners — run the adopted swing strategies and return suggestions.

Wraps the two live swing scanners (ryner RSI(2) pullback, high_base 52w-high
VCP) so the CLI can *show the actual candidate stocks* as of a date — not just
tell you how to run them. Each returns the candidate list; the underlying
scanner also prints a formatted table.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import List, Optional

# Ensure the repo root (holding the `tools/` package) is importable even when
# the CLI runs via the installed console script from another directory.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def run_ryner_scan(
    as_of: Optional[date] = None, top: int = 20, config_path: str = "config.yaml"
) -> List[dict]:
    """RSI(2) pullback-in-uptrend candidates as of `as_of` (default: today)."""
    from tools.ryner_pullback_scan import run_scan
    return run_scan(as_of=as_of, top=top, config_path=config_path)


def run_high_base_scan(
    as_of: Optional[date] = None, top: int = 20, config_path: str = "config.yaml"
) -> List[dict]:
    """52-week-high + tight-base (Minervini VCP-lite) breakout candidates."""
    from tools.high_base_scan import run_scan
    return run_scan(as_of=as_of, top=top, config_path=config_path)
