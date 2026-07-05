"""Actions layer — pure, reusable functions behind the CLI.

Each module here does ONE thing, takes explicit inputs (a `Config` and plain
values), returns typed results, and performs no interactive prompting or menu
rendering. The CLI (`fortress.cli`) is a thin shell that gathers inputs, calls
one action, and renders the result. Keeping logic here (not in the menu) makes
every feature unit-testable and reusable from scripts or notebooks.
"""
from .selection import apply_selection
from .backtest import run_backtest
from .phases import run_market_phases, MARKET_PHASES
from .market_state import current_market_state
from .rebalance import plan_rebalance
from .credentials import save_credentials
from .universe_update import update_universe

__all__ = [
    "apply_selection",
    "run_backtest",
    "run_market_phases",
    "MARKET_PHASES",
    "current_market_state",
    "plan_rebalance",
    "save_credentials",
    "update_universe",
]
