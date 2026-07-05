"""Tests for the pure actions layer."""
import pytest

from fortress.config import load_config
from fortress.actions import apply_selection, save_credentials


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


def test_save_credentials_writes_env(tmp_path):
    p = tmp_path / ".env"
    save_credentials("apikey_abc", "apisecret_xyz", str(p))
    txt = p.read_text()
    assert "ZERODHA_API_KEY=apikey_abc" in txt
    assert "ZERODHA_API_SECRET=apisecret_xyz" in txt


def test_save_credentials_requires_both(tmp_path):
    with pytest.raises(ValueError):
        save_credentials("", "secret", str(tmp_path / ".env"))
