import pytest
from app.keygen import generate_keypair
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from app import tokens


def _keys():
    priv_pem, pub_pem = generate_keypair()
    return load_pem_private_key(priv_pem, None), load_pem_public_key(pub_pem)


def test_encode_verify_roundtrip():
    priv, pub = _keys()
    payload = {"sub": "acct_1", "plan": "family", "status": "active",
               "iat": 1750200000, "exp": 1752792000, "grace_days": 14, "device_id": None}
    tok = tokens.encode_token(payload, priv)
    assert tokens.verify_token(tok, pub) == payload


def test_tampered_payload_rejected():
    priv, pub = _keys()
    tok = tokens.encode_token({"sub": "a", "iat": 1}, priv)
    head, sig = tok.split(".")
    bad = tokens.encode_token({"sub": "b", "iat": 1}, priv).split(".")[0] + "." + sig
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(bad, pub)


def test_wrong_key_rejected():
    priv, _ = _keys()
    _, other_pub = _keys()
    tok = tokens.encode_token({"sub": "a", "iat": 1}, priv)
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(tok, other_pub)


def test_malformed_rejected():
    _, pub = _keys()
    with pytest.raises(tokens.TokenError):
        tokens.verify_token("garbage-no-dot", pub)
