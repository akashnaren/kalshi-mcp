"""GET /markets calls share a lock file and stay 1.5s apart."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from kalshi_readonly.http import public_get
from kalshi_readonly.throttle import _FALLBACK_DIR, _LOCK_NAME, _MARKETS_INTERVAL, throttle_dir


class _Body:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


def test_throttle_dir_uses_state_dir_or_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KALSHI_STATE_DIR", raising=False)
    assert throttle_dir() == _FALLBACK_DIR
    monkeypatch.setenv("KALSHI_STATE_DIR", "  ")
    assert throttle_dir() == Path("/tmp/kalshi-mcp")
    monkeypatch.setenv("KALSHI_STATE_DIR", str(tmp_path))
    assert throttle_dir() == tmp_path


def test_markets_get_throttle_serializes_two_rapid_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _MARKETS_INTERVAL == 1.5
    monkeypatch.setenv("KALSHI_STATE_DIR", str(tmp_path))
    clock = {"t": 10_000.0}
    clock_lock = threading.Lock()
    waited: list[float] = []

    def now() -> float:
        with clock_lock:
            return clock["t"]

    def sleep(seconds: float) -> None:
        with clock_lock:
            waited.append(seconds)
            clock["t"] += seconds

    monkeypatch.setattr("kalshi_readonly.throttle._now", now)
    monkeypatch.setattr("kalshi_readonly.throttle._sleep", sleep)
    monkeypatch.setattr("kalshi_readonly.http._open", lambda req, timeout=20: _Body({"markets": []}))

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def call() -> None:
        try:
            barrier.wait(timeout=2)
            assert public_get("/markets", {"limit": 1}) == {"markets": []}
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert errors == []
    assert waited == [1.5]
    assert waited[0] >= 1.5
    lock_path = tmp_path / _LOCK_NAME
    assert lock_path.is_file()
    assert float(lock_path.read_text()) == pytest.approx(10_001.5)


def test_non_markets_get_does_not_take_the_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KALSHI_STATE_DIR", str(tmp_path))
    calls = {"n": 0}

    def slot() -> None:
        calls["n"] += 1

    def fake_open(req: urllib.request.Request, timeout: float = 20) -> _Body:
        return _Body({"ok": True})

    monkeypatch.setattr("kalshi_readonly.http.wait_markets_slot", slot)
    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    assert public_get("/exchange/status") == {"ok": True}
    assert public_get("/markets", {"series_ticker": "KX"}) == {"ok": True}
    assert public_get("markets") == {"ok": True}
    assert calls["n"] == 2
    assert not (tmp_path / _LOCK_NAME).exists()
