"""Every canned child-facing reply must be a sentinel the latency bar rejects.

The sentinel list in scripts/latency_bar.py was hand-written and drifted. It
carried "too many requests" while the rate limiter actually serves
_BUSY_MESSAGE ("Lots of learners are asking questions right now"), so a
rate-limited reply scored as a real latency measurement. _TIMEOUT_MESSAGE was
missing as well -- a reply that says in words that it took too long, counted as
a fast turn.

That is the same false green the latency bar was built to remove, one level up:
a check certifying a canned string as a passing measurement. A hand-maintained
copy of another module's strings drifts, so this test pins the invariant instead
of trusting the copy. Add a canned reply to the app and this test names it.
"""

import importlib.util
import pathlib

import pytest

_spec = importlib.util.spec_from_file_location(
    "latency_bar",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "latency_bar.py",
)
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)


def _canned_replies() -> dict:
    """Every named constant that can reach a child as a chat reply."""
    from api.routes.ollama_proxy import chat
    from core.pedagogy.guidance_enforcer import _WITHHOLDING_FALLBACK
    from core.profile_gate import NO_PROFILE_MESSAGE

    return {
        "chat._BUSY_MESSAGE": chat._BUSY_MESSAGE,
        "chat._TIMEOUT_MESSAGE": chat._TIMEOUT_MESSAGE,
        "chat._UNSUPPORTED_MESSAGE": chat._UNSUPPORTED_MESSAGE,
        "profile_gate.NO_PROFILE_MESSAGE": NO_PROFILE_MESSAGE,
        "guidance_enforcer._WITHHOLDING_FALLBACK": _WITHHOLDING_FALLBACK,
    }


@pytest.mark.parametrize("name", sorted(_canned_replies()))
def test_every_canned_reply_is_caught_by_a_sentinel(name):
    """A canned reply must never score as a genuine tutor turn."""
    text = _canned_replies()[name]
    assert any(s in text for s in lb._SENTINELS), (
        f"{name} is served to children with HTTP 200 but no entry in "
        f"scripts/latency_bar.py:_SENTINELS matches it, so the latency bar "
        f"would score it as a real measurement. Add a distinctive fragment of "
        f"it to _SENTINELS. Text: {text[:90]!r}"
    )


def test_the_rate_limiter_string_specifically_is_covered():
    """The exact drift that was found, pinned so it cannot recur."""
    from api.routes.ollama_proxy import chat

    assert any(s in chat._BUSY_MESSAGE for s in lb._SENTINELS)
    # And the stale fragment that gave false confidence matches nothing real.
    assert "too many requests" not in chat._BUSY_MESSAGE


def test_a_real_tutor_reply_is_not_mistaken_for_canned():
    """The sentinels must not be so broad they reject genuine replies."""
    genuine = [
        "Think about how you drink water through a straw. Roots do something "
        "similar for the plant. Where do you think the roots find water?",
        "You can check this by skip-counting by nines. Try counting up and see "
        "which number you land on.",
        "Break 13 into 10 and 3. What is 10 times 5, and what is 3 times 5?",
    ]
    for reply in genuine:
        assert not any(s in reply for s in lb._SENTINELS), reply


# ---------------------------------------------------------------------------
# The drift above is pinned per-constant. This is the hole that was left: a
# SECOND copy of a canned reply, in a module the pinning test never looks at.
#
# api/exception_handlers.py carried its own byte-identical copy of
# chat._BUSY_MESSAGE. Both tests above passed, because they compare
# _BUSY_MESSAGE against _SENTINELS and both were correct. Editing
# _BUSY_MESSAGE would have updated the constant and the sentinel and left the
# handler serving the OLD text -- matching no sentinel, so every busy reply
# would score as a real latency measurement. Safe only because two constants
# were equal, which is not a safety property.
# ---------------------------------------------------------------------------

_SOURCE_DIRS = ("api", "core", "scripts")

# Where each canned reply is allowed to appear as a literal: the module that
# DEFINES it, plus latency_bar's sentinel list, which must hold fragments by
# design and is pinned by the tests above.
_LITERAL_HOMES = {
    "chat._BUSY_MESSAGE": {"api/routes/ollama_proxy/chat.py"},
    "chat._TIMEOUT_MESSAGE": {"api/routes/ollama_proxy/chat.py"},
    "chat._UNSUPPORTED_MESSAGE": {"api/routes/ollama_proxy/chat.py"},
    "profile_gate.NO_PROFILE_MESSAGE": {"core/profile_gate.py"},
    "guidance_enforcer._WITHHOLDING_FALLBACK": {
        "core/pedagogy/guidance_enforcer.py"},
}
_SENTINEL_LIST = "scripts/latency_bar.py"


def _python_sources() -> list:
    root = pathlib.Path(__file__).resolve().parent.parent
    out = []
    for folder in _SOURCE_DIRS:
        out.extend(sorted((root / folder).rglob("*.py")))
    return out


@pytest.mark.parametrize("name", sorted(_canned_replies()))
def test_no_module_retypes_a_canned_reply(name):
    """A canned child-facing reply lives in ONE place and is imported.

    A duplicated literal is invisible to every per-constant check, because
    each copy is individually correct until one of them is edited.
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    # A fragment long enough to be unmistakable, short enough to survive an
    # edit to the tail of the sentence.
    fragment = _canned_replies()[name][:45]
    allowed = _LITERAL_HOMES[name] | {_SENTINEL_LIST}

    offenders = []
    for path in _python_sources():
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        if fragment in path.read_text(encoding="utf-8"):
            offenders.append(rel)

    assert not offenders, (
        f"{name} is re-typed as a literal in {offenders}. Import the constant "
        f"instead. A second copy is byte-identical until someone edits the "
        f"original, and then this path serves text that matches no entry in "
        f"{_SENTINEL_LIST}:_SENTINELS -- so a canned reply scores as a real "
        f"measurement, which is the false green these tests exist to stop."
    )


def test_the_busy_handler_serves_the_canonical_constant():
    """The specific duplicate that was found, pinned so it cannot come back."""
    from api.routes.ollama_proxy.chat import _BUSY_MESSAGE

    src = (pathlib.Path(__file__).resolve().parent.parent
           / "api" / "exception_handlers.py").read_text(encoding="utf-8")
    assert "_BUSY_MESSAGE" in src, (
        "api/exception_handlers.py must IMPORT the busy message, not re-type it"
    )
    assert _BUSY_MESSAGE[:45] not in src, (
        "api/exception_handlers.py has a literal copy of _BUSY_MESSAGE again"
    )
