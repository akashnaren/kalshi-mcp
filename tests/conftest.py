"""Keep fee lookups off the network unless a test installs its own series payload."""

from __future__ import annotations

import pytest

from kalshi_readonly.fees import clear_series_fee_cache


@pytest.fixture(autouse=True)
def _offline_series_fees(monkeypatch: pytest.MonkeyPatch):
    clear_series_fee_cache()

    def _offline(_path: str, _query=None) -> dict:
        raise RuntimeError("series lookup offline")

    monkeypatch.setattr("kalshi_readonly.fees.public_get", _offline)
    yield
    clear_series_fee_cache()
