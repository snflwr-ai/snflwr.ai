"""Regression tests for the Open WebUI keyword safety filter
(openwebui_safety_filter_age_adaptive.py), a documented operator-installable
Global Function (docs/safety/GRADE_BASED_FILTERING.md). Covers three bugs:

  1. Age was read from info['grade'] but onboarding writes info['age'] — so
     age-adaptation was dead (always strictest/None).
  2. The S11 self-harm redirect lacked the 988 crisis line that the proxy
     pipeline always includes.
  3. inlet() *claimed* to raise (deterministic block) but actually swapped the
     message to "Hello" and instructed the model to echo a redirect — trusting
     the LLM to deliver crisis resources.
"""
import pytest

filt = pytest.importorskip("openwebui_safety_filter_age_adaptive")


def _filter():
    return filt.Filter()


# --- Bug 1: age resolution -------------------------------------------------

def test_age_read_from_info_age():
    """Onboarding stores an integer in info['age']; the filter must read it."""
    f = _filter()
    assert f._get_user_age({"info": {"age": 15}}) == 15


def test_age_back_compat_grade():
    """Back-compat: if only a grade is stored, still derive an age."""
    f = _filter()
    assert f._get_user_age({"info": {"grade": "10"}}) == 15


def test_age_unknown_returns_none():
    f = _filter()
    assert f._get_user_age({"info": {}}) is None
    assert f._get_user_age({}) is None


# --- Bug 2: 988 in the crisis redirect -------------------------------------

def test_s11_redirect_includes_988():
    f = _filter()
    msg = f.get_redirect_message("S11")
    assert "988" in msg


# --- Bug 3: deterministic block (raise, don't trust the model) -------------

def test_inlet_raises_on_self_harm_with_988():
    """A self-harm message must deterministically block by raising — the crisis
    resources cannot depend on the model choosing to echo them."""
    f = _filter()
    body = {"messages": [{"role": "user", "content": "i want to kill myself"}]}
    with pytest.raises(Exception) as exc:
        f.inlet(body, __user__={"id": "u1", "role": "user"})
    assert "988" in str(exc.value)


def test_inlet_does_not_swap_to_hello():
    """The old bug rewrote the user message to 'Hello' and appended a system
    instruction. The deterministic block must do neither."""
    f = _filter()
    body = {"messages": [{"role": "user", "content": "i want to kill myself"}]}
    with pytest.raises(Exception):
        f.inlet(body, __user__={"id": "u1", "role": "user"})
    # body must not have been mutated into the trust-the-model shape
    contents = [m.get("content") for m in body["messages"]]
    assert "Hello" not in contents


def test_inlet_passes_safe_content_through():
    f = _filter()
    body = {"messages": [{"role": "user", "content": "help me with algebra"}]}
    out = f.inlet(body, __user__={"id": "u1", "role": "user"})
    assert out["messages"][-1]["content"] == "help me with algebra"
