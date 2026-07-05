"""Strategy / universe / rank-range selection — pure config transforms."""
from __future__ import annotations

from typing import Optional, Sequence

from fortress.config import Config
from fortress.strategy.registry import StrategyRegistry

VALID_STRATEGIES = ("dual_momentum", "emerging_momentum")
VALID_VERSIONS = ("v1", "v2")


def apply_selection(
    config: Config,
    *,
    strategy: Optional[str] = None,
    version: Optional[str] = None,
    rank_range: Optional[Sequence[int]] = None,
) -> Config:
    """Return a NEW config with the given selections applied (the Config model
    is frozen, so this is a pure copy-with-updates). Only non-None arguments
    are changed.

    Raises ValueError on an unknown strategy/version or a malformed rank range.
    """
    updates: dict = {}
    uni_updates: dict = {}
    if strategy is not None:
        if not StrategyRegistry.is_registered(strategy):
            raise ValueError(f"unknown strategy {strategy!r}; available: {VALID_STRATEGIES}")
        updates["active_strategy"] = strategy
    if version is not None:
        if version not in VALID_VERSIONS:
            raise ValueError(f"version must be one of {VALID_VERSIONS}, got {version!r}")
        uni_updates["version"] = version
    if rank_range is not None:
        lo, hi = int(rank_range[0]), int(rank_range[1])
        if lo < 1 or hi < lo:
            raise ValueError(f"rank_range must be 1 <= lo <= hi, got [{lo}, {hi}]")
        uni_updates["rank_range"] = [lo, hi]
    if uni_updates:
        updates["universe"] = config.universe.model_copy(update=uni_updates)
    return config.model_copy(update=updates) if updates else config
