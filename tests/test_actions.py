"""Tests for the pure actions layer."""
import pytest

from fortress.config import load_config
from fortress.actions import apply_selection


def test_apply_selection_is_pure_and_valid():
    cfg = load_config("config.yaml")
    original_strategy = cfg.active_strategy
    new = apply_selection(cfg, strategy="emerging_momentum", version="v2", rank_range=[101, 500])
    assert new.active_strategy == "emerging_momentum"
    assert new.universe.rank_range == [101, 500]
    # original config is untouched (frozen model -> pure copy)
    assert cfg.active_strategy == original_strategy


def test_apply_selection_rejects_bad_inputs():
    cfg = load_config("config.yaml")
    with pytest.raises(ValueError):
        apply_selection(cfg, strategy="does_not_exist")
    with pytest.raises(ValueError):
        apply_selection(cfg, rank_range=[500, 100])   # hi < lo
