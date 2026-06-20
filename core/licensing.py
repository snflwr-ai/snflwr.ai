"""Offline license verification + state evaluation for the self-hosted tutor.

Mirrors the license-server token codec (see
docs/superpowers/specs/2026-06-19-billing-design.md). The token format is the
contract shared with license-server/app/tokens.py.

NEVER raises out of evaluate()/current_state(): any problem -> unlicensed
(fail-safe gate, never a crash).
"""
import base64
import json
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class LicenseError(Exception):
    pass


# ---------------------------------------------------------------------------
# Token verification (byte-identical to license-server app.tokens.verify_token)
# ---------------------------------------------------------------------------


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_token(token: str, public_key) -> dict:
    from cryptography.exceptions import InvalidSignature
    try:
        body_b64, sig_b64 = token.split(".")
        body = _b64u_decode(body_b64)
        sig = _b64u_decode(sig_b64)
    except Exception as exc:
        raise LicenseError("malformed token") from exc
    try:
        public_key.verify(sig, body)
    except InvalidSignature as exc:
        raise LicenseError("bad signature") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LicenseError("bad payload") from exc


# ---------------------------------------------------------------------------
# State evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LicenseState:
    state: str          # active | trialing | grace | expired | unlicensed
    allowed: bool
    plan: "str | None"
    exp: "int | None"
    reason: str


def _unlicensed(reason: str) -> LicenseState:
    return LicenseState(state="unlicensed", allowed=False, plan=None, exp=None, reason=reason)


def evaluate(token, public_key, now: int) -> LicenseState:
    if not token:
        return _unlicensed("no token")
    try:
        payload = verify_token(token, public_key)
    except LicenseError as exc:
        logger.info("License token rejected: %s", exc)
        return _unlicensed(str(exc))
    exp = int(payload.get("exp", 0))
    grace_secs = int(payload.get("grace_days", 0)) * 86400
    plan = payload.get("plan")
    status = payload.get("status", "")
    if now <= exp:
        state = "trialing" if status == "trialing" else "active"
        return LicenseState(state=state, allowed=True, plan=plan, exp=exp, reason="valid")
    if now <= exp + grace_secs:
        return LicenseState(state="grace", allowed=True, plan=plan, exp=exp, reason="in grace")
    return LicenseState(state="expired", allowed=False, plan=plan, exp=exp, reason="grace exhausted")


# ---------------------------------------------------------------------------
# Token + session storage (under the app data dir)
# ---------------------------------------------------------------------------


def _token_path():
    from config import system_config
    return os.path.join(str(system_config.APP_DATA_DIR), "license.token")


def _session_path():
    from config import system_config
    return os.path.join(str(system_config.APP_DATA_DIR), "license.session")


def _write_secret_file(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_file(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def store_token(token: str) -> None:
    _write_secret_file(_token_path(), token)


def load_token():
    return _read_file(_token_path())


def store_session(token: str) -> None:
    _write_secret_file(_session_path(), token)


def load_session():
    return _read_file(_session_path())


# ---------------------------------------------------------------------------
# Public key + current state
# ---------------------------------------------------------------------------

_public_key_cache = None


def load_public_key():
    global _public_key_cache
    if _public_key_cache is None:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from config import system_config
        with open(system_config.LICENSE_PUBLIC_KEY_PATH, "rb") as f:
            _public_key_cache = load_pem_public_key(f.read())
    return _public_key_cache


def current_state(now: int) -> LicenseState:
    try:
        pub = load_public_key()
    except Exception as exc:  # missing/corrupt bundled key -> fail safe
        logger.error("Could not load license public key: %s", exc)
        return _unlicensed("public key unavailable")
    return evaluate(load_token(), pub, now)


# ---------------------------------------------------------------------------
# Online refresh
# ---------------------------------------------------------------------------


def refresh_once(client=None, now=None) -> bool:
    """POST /license/refresh with the stored session; swap in a fresh token.

    Returns True only on a 200 that yields a new token. Any other outcome
    (no config, no session, 402, network error) returns False and keeps the
    existing token. Never raises.
    """
    from config import system_config
    base = system_config.LICENSE_SERVER_URL
    session = load_session()
    if not base or not session:
        return False
    try:
        owns = client is None
        client = client or httpx.Client(timeout=10.0)
        try:
            resp = client.post(
                base.rstrip("/") + "/license/refresh",
                headers={"Authorization": f"Bearer {session}"}, timeout=10.0)
        finally:
            if owns:
                client.close()
        if resp.status_code == 200:
            store_token(resp.json()["token"])
            return True
        logger.info("License refresh returned %s", resp.status_code)
        return False
    except Exception as exc:  # network / parse — keep existing token
        logger.info("License refresh failed (offline?): %s", exc)
        return False
