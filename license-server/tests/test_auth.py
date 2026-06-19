import pytest
from fastapi.testclient import TestClient
from app import auth, store


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "_gen_code", lambda: "123456")
    sent = {}
    monkeypatch.setattr(auth.email, "send_code", lambda to, code: sent.update({to: code}))
    from app.main import create_app
    app = create_app()
    return TestClient(app), sent


def test_start_then_verify_returns_session(client):
    c, sent = client
    r = c.post("/auth/start", json={"email": "p@x.com"})
    assert r.status_code == 200
    assert sent["p@x.com"] == "123456"
    r2 = c.post("/auth/verify", json={"email": "p@x.com", "code": "123456"})
    assert r2.status_code == 200
    token = r2.json()["session"]
    assert auth.read_session_token(token) == store.account_id_for_email("p@x.com")


def test_verify_wrong_code_401(client):
    c, _ = client
    c.post("/auth/start", json={"email": "p@x.com"})
    r = c.post("/auth/verify", json={"email": "p@x.com", "code": "000000"})
    assert r.status_code == 401
