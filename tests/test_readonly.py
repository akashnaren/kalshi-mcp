import base64
import json
import os
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import server


def _pem() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return key, pem


class SigningTests(unittest.TestCase):
    def test_signature_is_pss_over_timestamp_method_and_path(self) -> None:
        key, pem = _pem()
        signature = server._signature(
            pem, "1703123456789", "get", "/trade-api/v2/portfolio/balance?limit=5"
        )
        key.public_key().verify(
            base64.b64decode(signature),
            b"1703123456789GET/trade-api/v2/portfolio/balance",
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )

    def test_signed_path_keeps_trade_api_prefix(self) -> None:
        self.assertEqual(server._signed_path("/portfolio/balance"), "/trade-api/v2/portfolio/balance")

    def test_headers_carry_the_key_id_and_omit_the_pem(self) -> None:
        _key, pem = _pem()
        saved = {
            "KALSHI_API_KEY_ID": os.environ.get("KALSHI_API_KEY_ID"),
            "KALSHI_PRIVATE_KEY_PEM": os.environ.get("KALSHI_PRIVATE_KEY_PEM"),
            "KALSHI_PRIVATE_KEY_PATH": os.environ.get("KALSHI_PRIVATE_KEY_PATH"),
        }
        os.environ["KALSHI_API_KEY_ID"] = "key-123"
        os.environ["KALSHI_PRIVATE_KEY_PEM"] = pem
        os.environ.pop("KALSHI_PRIVATE_KEY_PATH", None)
        try:
            headers = server._sign_headers("GET", "/trade-api/v2/portfolio/balance")
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.assertEqual(headers["KALSHI-ACCESS-KEY"], "key-123")
        blob = json.dumps(headers)
        self.assertNotIn("PRIVATE KEY", blob)
        self.assertNotIn(pem, blob)


class ReportTests(unittest.TestCase):
    def test_tools_stay_the_proved_read_set(self) -> None:
        self.assertEqual(
            [tool["name"] for tool in server.TOOLS],
            ["exchange_status", "list_markets", "cash_or_positions"],
        )

    def test_cash_and_positions_alias_official_fields_only(self) -> None:
        calls: list[str] = []

        def fake(path: str, query: dict | None = None) -> dict:
            calls.append(path)
            if path == "/portfolio/balance":
                return {"balance": 100, "balance_dollars": "1.0000", "portfolio_value": 5}
            if path == "/portfolio/positions":
                self.assertEqual(query, {"limit": 50})
                return {
                    "market_positions": [
                        {
                            "ticker": "KXTEST",
                            "position_fp": "-2.00",
                            "market_exposure_dollars": "1.2500",
                            "average_price_dollars": "0.5600",
                        }
                    ],
                    "event_positions": [
                        {"event_ticker": "KXEVENT", "total_cost_shares_fp": "2.00"}
                    ],
                }
            raise AssertionError(path)

        original = server._auth_get
        server._auth_get = fake
        try:
            out = server.cash_or_positions({"include": "both", "limit": 50})
        finally:
            server._auth_get = original

        self.assertEqual(calls, ["/portfolio/balance", "/portfolio/positions"])
        self.assertEqual(out["balance"]["cash"], "1.0000")
        self.assertEqual(out["balance"]["balance_dollars"], "1.0000")
        self.assertEqual(out["balance"]["portfolio_value"], 5)
        market = out["positions"]["market_positions"][0]
        self.assertEqual(market["ticker"], "KXTEST")
        self.assertEqual(market["qty"], "-2.00")
        self.assertEqual(market["avg"], "0.5600")
        self.assertNotIn("side", market)
        self.assertNotIn("mark", market)
        self.assertEqual(out["positions"]["event_positions"][0]["ticker"], "KXEVENT")
        self.assertEqual(out["positions"]["event_positions"][0]["qty"], "2.00")
        self.assertNotIn("remaining_to_recover", json.dumps(out))

    def test_side_and_mark_are_copied_only_when_present(self) -> None:
        row = server._alias_row(
            {"ticker": "KXTEST", "position": 3, "side": "yes", "mark_price_dollars": "0.4200"}
        )
        self.assertEqual(row["side"], "yes")
        self.assertEqual(row["qty"], 3)
        self.assertEqual(row["mark"], "0.4200")
        plain = server._alias_row({"ticker": "KXTEST", "position_fp": "1.00"})
        self.assertNotIn("side", plain)
        self.assertNotIn("avg", plain)
        self.assertNotIn("mark", plain)

    def test_fills_are_optional_and_not_part_of_both(self) -> None:
        calls: list[str] = []

        def fake(path: str, query: dict | None = None) -> dict:
            calls.append(path)
            return {
                "fills": [
                    {
                        "ticker": "KXTEST",
                        "outcome_side": "no",
                        "count_fp": "1.00",
                        "yes_price_dollars": "0.4000",
                        "no_price_dollars": "0.6000",
                    }
                ]
            }

        original = server._auth_get
        server._auth_get = fake
        try:
            out = server.cash_or_positions({"include": "fills", "limit": 10})
        finally:
            server._auth_get = original
        self.assertEqual(calls, ["/portfolio/fills"])
        self.assertEqual(out["fills"]["fills"][0]["side"], "no")
        self.assertEqual(out["fills"]["fills"][0]["qty"], "1.00")
        self.assertNotIn("balance", out)


if __name__ == "__main__":
    unittest.main()
