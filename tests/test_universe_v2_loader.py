"""Tests that fortress's Universe routes version through to nse-universe."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from fortress.universe import Universe, _NSE_SINGLETONS, _nse_universe_singleton


@pytest.fixture(autouse=True)
def _reset_nse_singletons():
    """Each test gets a fresh singleton cache to avoid cross-pollination."""
    _NSE_SINGLETONS.clear()
    yield
    _NSE_SINGLETONS.clear()


def test_default_version_is_v1():
    """Without explicit version, fortress.Universe builds the v1 nse-universe singleton."""
    with patch("nse_universe.Universe") as MockNSEU:
        instance = MagicMock()
        instance.universe_at.return_value = pd.DataFrame(
            columns=["rank", "symbol", "metric_value"]
        )
        MockNSEU.return_value = instance

        u = Universe(as_of=date(2024, 1, 1), rank_range=(201, 600))

        assert u.version == "v1"
        MockNSEU.assert_called_with(version="v1")


def test_v2_routes_to_v2_nse_universe():
    with patch("nse_universe.Universe") as MockNSEU:
        instance = MagicMock()
        instance.universe_at.return_value = pd.DataFrame(
            columns=["rank", "symbol", "metric_value"]
        )
        MockNSEU.return_value = instance

        u = Universe(as_of=date(2024, 1, 1), rank_range=(201, 600), version="v2")

        assert u.version == "v2"
        MockNSEU.assert_called_with(version="v2")


def test_invalid_version_raises():
    with pytest.raises(ValueError):
        Universe(as_of=date(2024, 1, 1), version="v3")


def test_v1_and_v2_singletons_are_independent():
    with patch("nse_universe.Universe") as MockNSEU:
        instance = MagicMock()
        instance.universe_at.return_value = pd.DataFrame(
            columns=["rank", "symbol", "metric_value"]
        )
        MockNSEU.return_value = instance

        Universe(as_of=date(2024, 1, 1), version="v1")
        Universe(as_of=date(2024, 1, 1), version="v2")

        # Two distinct constructor calls — one per version.
        versions_called = {call.kwargs.get("version") for call in MockNSEU.call_args_list}
        assert versions_called == {"v1", "v2"}
