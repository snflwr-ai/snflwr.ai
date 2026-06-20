import base64
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from core import licensing


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_token(priv, payload):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return _b64u(body) + "." + _b64u(priv.sign(body))


def _setup():
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


# --- Task 9: evaluate() ----------------------------------------------------


def test_active_allowed():
    priv, pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "active",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, pub, now=500)
    assert st.allowed and st.state == "active"


def test_trialing_allowed():
    priv, pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "trialing",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, pub, now=500)
    assert st.allowed and st.state == "trialing"


def test_in_grace_allowed():
    priv, pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "active",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, pub, now=1000 + 5 * 86400)  # past exp, within 14d grace
    assert st.allowed and st.state == "grace"


def test_grace_exhausted_gated():
    priv, pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "active",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, pub, now=1000 + 20 * 86400)
    assert not st.allowed and st.state == "expired"


def test_missing_token_unlicensed():
    _, pub = _setup()
    st = licensing.evaluate(None, pub, now=0)
    assert not st.allowed and st.state == "unlicensed"


def test_corrupt_token_unlicensed():
    _, pub = _setup()
    st = licensing.evaluate("not.a.valid.token", pub, now=0)
    assert not st.allowed and st.state == "unlicensed"


def test_bad_signature_unlicensed():
    priv, _ = _setup()
    _, other_pub = _setup()
    tok = _make_token(priv, {"sub": "a", "plan": "family", "status": "active",
                             "iat": 0, "exp": 1000, "grace_days": 14, "device_id": None})
    st = licensing.evaluate(tok, other_pub, now=500)
    assert not st.allowed and st.state == "unlicensed"


# --- Task 10: storage ------------------------------------------------------


def test_store_and_load_token(tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "APP_DATA_DIR", tmp_path)
    licensing.store_token("abc.def")
    assert licensing.load_token() == "abc.def"


def test_load_token_missing_returns_none(tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "APP_DATA_DIR", tmp_path)
    assert licensing.load_token() is None


# --- Task 12: refresh ------------------------------------------------------


def test_refresh_once_stores_new_token(tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(system_config, "LICENSE_SERVER_URL", "https://ls.test")
    licensing.store_session("sess-token")

    class _Resp:
        status_code = 200

        def json(self):
            return {"token": "new.token"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, timeout=None):
            assert headers["Authorization"] == "Bearer sess-token"
            return _Resp()

        def close(self):
            pass

    monkeypatch.setattr(licensing.httpx, "Client", _Client)
    assert licensing.refresh_once() is True
    assert licensing.load_token() == "new.token"


def test_refresh_once_offline_keeps_token(tmp_path, monkeypatch):
    from config import system_config
    monkeypatch.setattr(system_config, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(system_config, "LICENSE_SERVER_URL", "https://ls.test")
    licensing.store_session("sess-token")
    licensing.store_token("old.token")

    class _Client:
        def __init__(self, *a, **k):
            pass

        def post(self, *a, **k):
            raise licensing.httpx.ConnectError("offline")

        def close(self):
            pass

    monkeypatch.setattr(licensing.httpx, "Client", _Client)
    assert licensing.refresh_once() is False
    assert licensing.load_token() == "old.token"
