import base64
import json
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key


class TokenError(Exception):
    pass


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encode_token(payload: dict, private_key) -> str:
    body = _canonical(payload)
    sig = private_key.sign(body)
    return _b64u_encode(body) + "." + _b64u_encode(sig)


def verify_token(token: str, public_key) -> dict:
    try:
        body_b64, sig_b64 = token.split(".")
        body = _b64u_decode(body_b64)
        sig = _b64u_decode(sig_b64)
    except Exception as exc:  # malformed in any way
        raise TokenError("malformed token") from exc
    try:
        public_key.verify(sig, body)
    except InvalidSignature as exc:
        raise TokenError("bad signature") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise TokenError("bad payload") from exc


def load_private_key(path: str):
    with open(path, "rb") as f:
        return load_pem_private_key(f.read(), password=None)


def load_public_key(path: str):
    with open(path, "rb") as f:
        return load_pem_public_key(f.read())
