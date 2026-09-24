"""Tests for the 3-hour break reminder on the Open WebUI proxy path.

California SB 243 (Bus. & Prof. Code §22602(c)(2)) requires an operator, for a
user it knows is a minor, to "provide by default a clear and conspicuous
notification to the user at least every three hours for continuing companion
chatbot interactions that reminds the user to take a break and that the
companion chatbot is artificially generated and not human."

The persistent disclosure banner covers the "not human" half but never tells a
child to take a break, and the proxy path has no session cap (the native route
stops at 4 hours; the proxy does not). So the reminder is enforced per child.
"""

import pytest

from api.routes.ollama_proxy import break_reminder as br


class _Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def rem(clock):
    r = br.BreakReminder(interval_seconds=3 * 3600, idle_reset_seconds=1800, cache=None)
    r._now = clock
    return r


def _chat_for(rem, clock, seconds, step=600):
    """Simulate continuous turns every `step` seconds; return the due times."""
    fired = []
    end = clock.t + seconds
    while clock.t <= end:
        if rem.due("p1"):
            fired.append(clock.t)
        clock.t += step
    return fired


def test_first_turn_is_not_due(rem):
    # Session start is covered by the persistent disclosure banner.
    assert rem.due("p1") is False


def test_fires_once_at_three_hours_of_continuous_chat(rem, clock):
    start = clock.t
    fired = _chat_for(rem, clock, 3 * 3600 + 1200)
    assert len(fired) == 1
    assert fired[0] - start >= 3 * 3600


def test_recurs_every_three_hours(rem, clock):
    start = clock.t
    fired = _chat_for(rem, clock, 9 * 3600 + 600)
    assert len(fired) == 3
    gaps = [b - a for a, b in zip([start] + fired, fired)]
    # Never later than one turn step past the statutory 3 hours.
    assert all(3 * 3600 <= g <= 3 * 3600 + 600 for g in gaps)


def test_idle_gap_starts_a_new_stretch(rem, clock):
    rem.due("p1")
    clock.t += 2 * 3600
    rem.due("p1")
    clock.t += 1801  # a real break
    assert rem.due("p1") is False
    clock.t += 1.5 * 3600
    assert rem.due("p1") is False  # only 1.5h into the new stretch


def test_children_are_tracked_independently(rem, clock):
    start = clock.t
    fired = {"p1": [], "p2": []}
    while clock.t <= start + 4 * 3600 + 600:
        if rem.due("p1"):
            fired["p1"].append(clock.t - start)
        if clock.t >= start + 3600 and rem.due("p2"):  # p2 starts an hour later
            fired["p2"].append(clock.t - start)
        clock.t += 600
    assert len(fired["p1"]) == 1 and 3 * 3600 <= fired["p1"][0] < 3 * 3600 + 600
    assert len(fired["p2"]) == 1 and 4 * 3600 <= fired["p2"][0] < 4 * 3600 + 600


def test_interval_env_is_clamped_to_three_hours(monkeypatch):
    monkeypatch.setenv("BREAK_REMINDER_INTERVAL_S", str(5 * 3600))
    assert br._interval_from_env() == 3 * 3600
    monkeypatch.setenv("BREAK_REMINDER_INTERVAL_S", "garbage")
    assert br._interval_from_env() == 3 * 3600
    monkeypatch.setenv("BREAK_REMINDER_INTERVAL_S", "7200")
    assert br._interval_from_env() == 7200


def test_cache_failure_falls_back_to_local_state(clock):
    class _Broken:
        def get(self, *a, **k):
            raise RuntimeError("redis down")

        def set(self, *a, **k):
            raise RuntimeError("redis down")

    r = br.BreakReminder(
        interval_seconds=3 * 3600, idle_reset_seconds=1800, cache=_Broken()
    )
    r._now = clock
    fired = _chat_for(r, clock, 3 * 3600 + 600)
    assert len(fired) == 1  # still enforced — an outage must not silence it


def test_uses_shared_cache_when_available(clock):
    class _Dict:
        def __init__(self):
            self.d = {}

        def get(self, key, namespace="x"):
            return self.d.get((namespace, key))

        def set(self, key, value, ttl=None, namespace="x"):
            self.d[(namespace, key)] = value
            return True

    shared = _Dict()
    a = br.BreakReminder(
        interval_seconds=3 * 3600, idle_reset_seconds=1800, cache=shared
    )
    b = br.BreakReminder(
        interval_seconds=3 * 3600, idle_reset_seconds=1800, cache=shared
    )
    a._now = b._now = clock
    start = clock.t
    fired = []
    for i in range(20):  # workers alternate every 10 minutes
        if (a if i % 2 else b).due("p1"):
            fired.append(clock.t - start)
        clock.t += 600
    assert shared.d  # state lived in the shared store
    assert fired == [3 * 3600]  # one reminder, on time, despite switching workers


def test_reminder_text_has_both_statutory_elements():
    text = br.REMINDER_TEXT.lower()
    assert "break" in text
    assert "ai" in text and "not a human" in text


def test_with_reminder_prefixes_text():
    out = br.with_reminder("Here is the answer.")
    assert out.startswith(br.REMINDER_TEXT)
    assert out.endswith("Here is the answer.")
