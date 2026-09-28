import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_readonly.auth import AUTH_ERROR, load_auth, sign_request, signed_headers
from kalshi_readonly.stdio import public_error


def _pem() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return key, pem


@pytest.mark.parametrize(
    ("method", "path", "message"),
    [
        ("post", "/trade-api/v2/portfolio/events/orders", b"1703123456789POST/trade-api/v2/portfolio/events/orders"),
        (
            "delete",
            "/trade-api/v2/portfolio/events/orders/ord-1?market_ticker=KXTEST",
            b"1703123456789DELETE/trade-api/v2/portfolio/events/orders/ord-1",
        ),
    ],
)
def test_post_and_delete_use_the_same_pss_rule(method: str, path: str, message: bytes) -> None:
    key, pem = _pem()
    signature = sign_request(pem, "1703123456789", method, path)
    key.public_key().verify(
        base64.b64decode(signature),
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_signature_covers_timestamp_method_and_path_without_query() -> None:
    key, pem = _pem()
    signature = sign_request(pem, "1703123456789", "get", "/trade-api/v2/portfolio/balance?limit=5")
    key.public_key().verify(
        base64.b64decode(signature),
        b"1703123456789GET/trade-api/v2/portfolio/balance",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_signed_header_shape_omits_the_private_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _key, pem = _pem()
    pem_path = tmp_path / "private.pem"
    pem_path.write_text(pem, encoding="utf-8")
    monkeypatch.setenv("KALSHI_API_KEY_ID", " key-123 ")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(pem_path))
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)

    headers = signed_headers("GET", "/trade-api/v2/portfolio/balance", now_ms=1703123456789)

    assert headers["KALSHI-ACCESS-KEY"] == "key-123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1703123456789"
    assert headers["KALSHI-ACCESS-SIGNATURE"]
    base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"], validate=True)
    blob = json.dumps(headers)
    assert "PRIVATE KEY" not in blob
    assert pem not in blob
    assert "BEGIN" not in blob


def test_missing_auth_fails_closed_without_echoing_a_path_body(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID_PATH", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
    with pytest.raises(RuntimeError, match="unable to read KALSHI_PRIVATE_KEY_PATH"):
        load_auth()

    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-123")
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    with pytest.raises(RuntimeError) as caught:
        load_auth()
    assert str(caught.value) == AUTH_ERROR
    assert "PRIVATE KEY" not in str(caught.value)


def _isolate_home(monkeypatch: pytest.MonkeyPatch, home) -> None:
    import kalshi_readonly.auth as auth

    auth._AUTH = None
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_API_KEY_ID_PATH", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)


def test_key_id_path_signs_when_the_id_env_is_unset(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _isolate_home(monkeypatch, home)
    _key, pem = _pem()
    key_path = tmp_path / "key_id"
    key_path.write_text(" path-key \n", encoding="utf-8")
    pem_path = tmp_path / "private.pem"
    pem_path.write_text(pem, encoding="utf-8")
    monkeypatch.setenv("KALSHI_API_KEY_ID_PATH", str(key_path))
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(pem_path))

    headers = signed_headers("GET", "/trade-api/v2/portfolio/balance", now_ms=1703123456789)

    assert headers["KALSHI-ACCESS-KEY"] == "path-key"
    assert headers["KALSHI-ACCESS-SIGNATURE"]
    blob = json.dumps(headers)
    assert pem not in blob
    assert "PRIVATE KEY" not in blob
    assert "BEGIN" not in blob
    assert "KALSHI_API_KEY_ID_PATH" in AUTH_ERROR


def test_default_secret_files_load_when_env_is_unset(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    home = tmp_path / "home"
    secret = home / ".secrets" / "kalshi"
    secret.mkdir(parents=True)
    _isolate_home(monkeypatch, home)
    _key, pem = _pem()
    (secret / "key_id").write_text("default-key\n", encoding="utf-8")
    (secret / "private.pem").write_text(pem, encoding="utf-8")

    key_id, loaded = load_auth()

    assert key_id == "default-key"
    assert "PRIVATE KEY" in loaded
    assert pem not in key_id


def test_explicit_key_id_wins_over_path_and_default_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    home = tmp_path / "home"
    secret = home / ".secrets" / "kalshi"
    secret.mkdir(parents=True)
    _isolate_home(monkeypatch, home)
    _key, pem = _pem()
    (secret / "key_id").write_text("default-key\n", encoding="utf-8")
    (secret / "private.pem").write_text(pem, encoding="utf-8")
    path_file = tmp_path / "other_id"
    path_file.write_text("path-key\n", encoding="utf-8")
    monkeypatch.setenv("KALSHI_API_KEY_ID", " explicit-key ")
    monkeypatch.setenv("KALSHI_API_KEY_ID_PATH", str(path_file))

    assert load_auth()[0] == "explicit-key"


def test_missing_key_id_path_does_not_fall_through_or_echo_the_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    home = tmp_path / "home"
    secret = home / ".secrets" / "kalshi"
    secret.mkdir(parents=True)
    _isolate_home(monkeypatch, home)
    _key, pem = _pem()
    (secret / "key_id").write_text("default-key\n", encoding="utf-8")
    pem_path = tmp_path / "private.pem"
    pem_path.write_text(pem, encoding="utf-8")
    missing = tmp_path / "missing-id"
    monkeypatch.setenv("KALSHI_API_KEY_ID_PATH", str(missing))
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(pem_path))

    with pytest.raises(RuntimeError, match="unable to read KALSHI_API_KEY_ID_PATH") as caught:
        load_auth()
    assert str(missing) not in str(caught.value)
    assert "default-key" not in str(caught.value)
    assert pem not in str(caught.value)


def test_key_id_file_that_contains_a_pem_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _isolate_home(monkeypatch, home)
    _key, pem = _pem()
    key_path = tmp_path / "key_id"
    key_path.write_text(pem, encoding="utf-8")
    pem_path = tmp_path / "private.pem"
    pem_path.write_text(pem, encoding="utf-8")
    monkeypatch.setenv("KALSHI_API_KEY_ID_PATH", str(key_path))
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(pem_path))

    with pytest.raises(RuntimeError) as caught:
        load_auth()
    assert str(caught.value) == AUTH_ERROR
    assert "PRIVATE KEY" not in str(caught.value)
    assert pem not in str(caught.value)


def test_public_error_strips_key_material() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    assert public_error(RuntimeError(pem)) == "Kalshi request failed"
    assert public_error(RuntimeError(AUTH_ERROR)) == AUTH_ERROR
