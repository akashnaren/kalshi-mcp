import base64
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_readonly.auth import AUTH_ERROR
from kalshi_readonly.tools import cash_or_positions, registered_tools


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def _header(req: urllib.request.Request, name: str) -> str | None:
    for key, value in req.header_items():
        if key.lower() == name.lower():
            return value
    return None


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_read_tools_stay_registered_when_safe_mode_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)
    assert [tool["name"] for tool in registered_tools()] == [
        "exchange_status",
        "list_markets",
        "find_best_bets",
        "cash_or_positions",
        "fe_routine",
        "list_open_orders",
    ]


def test_cash_or_positions_signs_get_and_reports_official_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    pem = _pem()
    private = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", pem)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("KALSHI_API_BASE", raising=False)

    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        assert req.get_method() == "GET"
        assert timeout == 20
        seen.append(req)
        path = urllib.parse.urlparse(req.full_url).path
        if path.endswith("/portfolio/balance"):
            return _Body({"balance": 250, "balance_dollars": "2.5000", "portfolio_value": 80, "updated_ts": 9})
        if path.endswith("/portfolio/positions"):
            return _Body(
                {
                    "cursor": "",
                    "market_positions": [
                        {
                            "ticker": "KXTEST",
                            "position_fp": "-2.00",
                            "market_exposure_dollars": "1.2500",
                            "average_price_dollars": "0.5600",
                        }
                    ],
                    "event_positions": [
                        {"event_ticker": "KXEVENT", "total_cost_shares_fp": "2.00", "event_exposure_dollars": "1.2500"}
                    ],
                }
            )
        raise AssertionError(path)

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    out = cash_or_positions({"include": "both", "limit": 50})

    assert [urllib.parse.urlparse(req.full_url).path for req in seen] == [
        "/trade-api/v2/portfolio/balance",
        "/trade-api/v2/portfolio/positions",
    ]
    assert urllib.parse.parse_qs(urllib.parse.urlparse(seen[1].full_url).query)["limit"] == ["50"]
    for req in seen:
        assert _header(req, "KALSHI-ACCESS-KEY") == "key-123"
        timestamp = _header(req, "KALSHI-ACCESS-TIMESTAMP")
        signature = _header(req, "KALSHI-ACCESS-SIGNATURE")
        assert timestamp and timestamp.isdigit()
        assert signature
        path = urllib.parse.urlparse(req.full_url).path
        private.public_key().verify(
            base64.b64decode(signature),
            f"{timestamp}GET{path}".encode("utf-8"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        assert pem not in json.dumps(dict(req.header_items()))

    balance = out["balance"]
    assert balance["balance"] == 250
    assert balance["balance_cents"] == 250
    assert balance["balance_dollars"] == "2.5000"
    assert balance["cash"] == "2.5000"
    assert balance["portfolio_value"] == 80
    market = out["positions"]["market_positions"][0]
    assert market["ticker"] == "KXTEST"
    assert market["qty"] == "-2.00"
    assert market["side"] == "no"
    assert market["avg"] == "0.5600"
    assert "mark" not in market
    event = out["positions"]["event_positions"][0]
    assert event["ticker"] == "KXEVENT"
    assert event["qty"] == "2.00"
    assert "side" not in event
    assert "remaining_to_recover" not in json.dumps(out)
    assert "fills" not in out


def test_missing_auth_does_not_call_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    with pytest.raises(RuntimeError) as caught:
        cash_or_positions({"include": "balance"})
    assert str(caught.value) == AUTH_ERROR


def test_http_error_does_not_return_key_material(monkeypatch: pytest.MonkeyPatch) -> None:
    pem = _pem()
    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", pem)

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        body = b'-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----'
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", Message(), io.BytesIO(body))

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    with pytest.raises(RuntimeError, match="failed: 401") as caught:
        cash_or_positions({"include": "cash"})
    assert "PRIVATE KEY" not in str(caught.value)
    assert "SECRET" not in str(caught.value)


def test_fills_are_a_separate_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", _pem())
    seen: list[str] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        seen.append(urllib.parse.urlparse(req.full_url).path)
        return _Body(
            {
                "fills": [
                    {
                        "ticker": "KXTEST",
                        "outcome_side": "yes",
                        "count_fp": "1.00",
                        "yes_price_dollars": "0.4000",
                        "no_price_dollars": "0.6000",
                    }
                ],
                "cursor": "",
            }
        )

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    out = cash_or_positions({"include": "fills", "limit": 10})
    assert seen == ["/trade-api/v2/portfolio/fills"]
    fill = out["fills"]["fills"][0]
    assert fill["side"] == "yes"
    assert fill["qty"] == "1.00"
    assert fill["yes_price_dollars"] == "0.4000"
    assert "balance" not in out
