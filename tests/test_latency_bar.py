"""The latency bar must exist, be a ratchet, and measure the child's wait.

Latency had no bar at all until 2026-09-21. Every bar was about correctness --
reveal rate, fallback rate, wrong content -- so the enforcement pipeline grew to
2.9x the unenforced wait (traffic-weighted p50 10.6s vs 3.7s) and nothing
objected. A flagged turn reaches p90 31.6s, and 3% of flagged ladders exceed the
whole 40s budget on their own, which means part of the measured stonewall rate is
TIMEOUT rather than detection.

These tests do not measure latency -- that needs the GPU and a resident model.
They guard the SHAPE of the check, which is what rots silently.
"""

import inspect
import re

import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "latency_bar",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "latency_bar.py",
)
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)


def test_the_bars_are_above_the_measurement_but_not_far_above():
    """A bar at the measured value flaps; a bar at triple it is decoration.

    Measured 2026-09-21: clean p50 7.7 / p90 14.4, flagged p50 16.8 / p90 31.6.
    """
    measured = {
        "BAR_CLEAN_P50": 7.7,
        "BAR_CLEAN_P90": 14.4,
        "BAR_FLAGGED_P50": 16.8,
        "BAR_FLAGGED_P90": 31.6,
    }
    for name, got in measured.items():
        bar = getattr(lb, name)
        assert bar > got, f"{name} is at or below the measurement; it will flap"
        assert bar <= got * 2.0, (
            f"{name} is more than 2x the measurement, so a change that DOUBLES "
            "the wait would still pass. That is the regression this exists to catch."
        )


def test_the_flagged_p90_bar_does_not_exceed_the_enforcer_budget():
    """Past the total budget the turn is not slow, it is a canned fallback. A
    latency bar above the budget can never fail for the reason that matters."""
    from config import system_config

    budget = float(getattr(system_config, "GUIDANCE_ENFORCER_TOTAL_BUDGET_S", 40.0))
    assert lb.BAR_FLAGGED_P90 <= budget


def test_it_probes_BOTH_clean_and_homework_turns():
    """A clean turn still pays two confirm calls and is ~69% of traffic, so a
    homework-only probe set would miss the regression most children feel."""
    assert len(lb.CLEAN_PROBES) >= 3 and len(lb.HOMEWORK_PROBES) >= 3
    joined = " ".join(lb.CLEAN_PROBES).lower()
    assert "quiz me" in joined or "what does" in joined, (
        "the clean probes no longer look like genuine questions"
    )


def test_contention_reports_UNMEASURED_rather_than_failing():
    """On a shared card the co-tenant decides when a cold load happens. A check
    that reads contention as a regression gets switched off within a week -- and
    a check that reads it as a PASS is worse. It must say it could not measure.
    """
    src = inspect.getsource(lb.main)
    assert "UNMEASURED" in src
    assert "return 0" in src, "an unmeasurable run must not fail the deploy"


def test_a_warm_up_turn_is_discarded():
    """A cold load is a load measurement, not a latency measurement."""
    src = inspect.getsource(lb.main)
    assert "discarded" in src.lower()


def test_a_probe_that_gets_NO_answer_is_a_failure_not_a_fast_turn():
    """The canned-fallback-scores-as-a-pass shape, in latency form: an empty
    reply returns instantly and would look like the fastest turn in the set."""
    src = inspect.getsource(lb.main)
    assert "no answer" in src.lower()
    assert "failures" in src


def test_the_failure_message_forbids_raising_the_bar():
    """The ratchet only works if the instinct to loosen it is named."""
    src = inspect.getsource(lb.main)
    assert "do not raise the bar" in src.lower()


class TestTheBarIsActuallyRUN:
    """A bar nobody runs is decoration.

    This script existed standalone for a day and NOTHING invoked it -- not CI,
    not the postdeploy smoke, not any shell script. "A ratchet nobody tightens
    decays into a no-op" is a lesson this stack has already paid for once
    (`self-tightening-ratchet`), so the wiring gets its own test.

    It lives in the postdeploy smoke rather than CI because it needs a GPU and a
    resident model, which CI does not have.
    """

    def test_the_postdeploy_smoke_invokes_it(self):
        import inspect

        import scripts.postdeploy_smoke as smoke

        whole = pathlib.Path(inspect.getfile(smoke)).read_text()
        assert "latency_bar.py" in whole, (
            "nothing runs the latency bar; it is decoration again"
        )
        assert "_check_the_child_is_not_waiting_longer_than_the_bar()" in whole, (
            "the latency check is defined but never called from main()"
        )

    def test_a_missing_script_FAILS_rather_than_being_skipped(self):
        """The failure mode of a wired-in check: the file goes away and the
        check quietly passes, so the bar is gone and the deploy is still green.
        """
        import inspect

        import scripts.postdeploy_smoke as smoke

        src = inspect.getsource(
            smoke._check_the_child_is_not_waiting_longer_than_the_bar
        )
        assert "is missing" in src and "latency bar missing" in src

    def test_contention_does_not_fail_the_deploy(self):
        """The script reports UNMEASURED and exits 0 when it cannot get the
        model resident. A check that reads a co-tenant holding the card as a
        latency regression gets switched off; one that reads it as a PASS is
        worse, which is why the script says UNMEASURED out loud."""
        import inspect

        import scripts.postdeploy_smoke as smoke

        src = inspect.getsource(
            smoke._check_the_child_is_not_waiting_longer_than_the_bar
        )
        assert "UNMEASURED" in src, (
            "the wiring must document that contention is not a regression"
        )

    def test_the_retracted_figure_is_not_quoted_as_fact(self):
        """This docstring once asserted a "24% false-alarm rate on children who
        are stuck mid-problem". It had no source -- it existed only here, and was
        then quoted back OUT of this file as though measured.

        The real numbers (blind holdout, 2026-09-21): 0% on the production path,
        5% on the regex fallback. The note stays because the failure was a number
        in a docstring being trusted as a measurement.
        """
        src = pathlib.Path(lb.__file__ or "").read_text() if lb.__file__ else ""
        if not src:
            src = (
                pathlib.Path(__file__).resolve().parent.parent
                / "scripts"
                / "latency_bar.py"
            ).read_text()
        assert "RETRACTION" in src, (
            "the retraction note was removed; the next reader will re-trust the "
            "figure this file invented"
        )
        # the bare claim must not reappear without its retraction alongside
        for line in src.splitlines():
            if "24%" in line:
                assert "NO SOURCE" in line or "24%" in line and '"24%"' in line, (
                    f"an unsourced 24% figure is back: {line.strip()!r}"
                )


class TestItCannotPASS_WithoutKnowingWhatToMeasure:
    """The vacuous-pass path, found by actually running it.

    On the host OLLAMA_DEFAULT_MODEL is unset. The bar called generate with
    model="", got nothing back, printed "the card is probably held by the
    co-tenant", and exited 0 -- a pass, with a fabricated diagnosis, from a
    check that did not know which model it was timing.

    UNMEASURED is legitimate for "I know the model and could not get it
    resident". It must never cover "I do not know the model".
    """

    def test_an_empty_model_name_FAILS(self):
        src = inspect.getsource(lb.main)
        assert "OLLAMA_DEFAULT_MODEL is empty" in src
        # and it must return non-zero, not fall through to UNMEASURED
        head = src[: src.index("warm_s")]
        assert "return 1" in head, (
            "an unresolvable model must fail, not report UNMEASURED and pass"
        )

    def test_the_unmeasured_message_offers_causes_as_HYPOTHESES(self):
        """It previously asserted one cause as diagnosed, and that cause was
        wrong on the first real run. A check's explanation of its own verdict is
        a hypothesis, not evidence (`agent-slow-was-a-flapping-check`).

        Comments are stripped before the negative assertion: the comment that
        RECORDS this fix quotes the old wording, so a naive substring search
        matches the explanation rather than the behaviour. Same trap as the
        prompt-fingerprint guard, which needed the same treatment.
        """
        src = inspect.getsource(lb.main)
        code = "\n".join(
            ln for ln in src.splitlines() if not ln.strip().startswith("#")
        )
        assert "Possible causes" in code, (
            "the UNMEASURED branch asserts a cause it has not established"
        )
        assert "probably held by the co-tenant" not in code


def test_it_times_the_PATH_A_CHILD_TAKES_not_the_raw_model():
    """The bar must go through the proxy, where enforcement lives.

    The first version called `ollama_client`, whose host is
    http://ollama:11434 -- bypassing snflwr-api:39150 and therefore the gate,
    the confirm and the rewrite ladder. It timed the raw tutor while its
    docstring claimed to measure "what a CHILD waits", so it could never have
    seen the enforcement growth it exists to catch.

    The tell was in its own output: homework turns 7.5s, IDENTICAL to clean
    turns, when a flagged ladder should roughly double them.

    Two related traps this test has already fallen into, both worth knowing:
      * an earlier version passed think=False to `generate`, which has no such
        parameter and would have raised TypeError -- while a grep for the string
        "think=False" passed. A source-read test can green-light a call that
        cannot run, so the positive assertions below name the ENDPOINT, and the
        real call is exercised by running the script (see the in-container run
        recorded in the module docstring).
      * the negative assertion must run on code with the docstring STRIPPED:
        the docstring records the bypass bug and so names `ollama_client`.
    """
    src = inspect.getsource(lb._turn)
    code = re.sub(r'"""(?:.|\n)*?"""', "", src, count=1)
    assert "/api/chat" in code, "the bar must time the proxy chat endpoint"
    assert "39150" in code or "SNFLWR_PROXY_URL" in code, (
        "the bar is not pointed at the api proxy; it bypasses enforcement and "
        "can never see the latency it exists to guard"
    )
    assert "ollama_client" not in code, (
        "calling ollama directly bypasses the gate, confirm and rewrite ladder"
    )
    assert "Authorization" in code, "the proxy rejects unauthenticated callers"


class TestItCannotPassWithoutAChildPROFILE:
    """The false green this check actually produced.

    The proxy fails CLOSED without a learning profile. Pointed at it, the bar got
    HTTP 200, a non-empty body and 0.0s -- the canned "No learning profile is set
    up yet" string -- scored it a successful fast turn, and printed
    "OK - latency inside every bar" at p50 0.0s.

    Two independent guards now: identify as the canary student so a real profile
    resolves, and refuse to report a verdict at all when that identity is
    missing.
    """

    def test_it_sends_the_student_identity_header(self):
        src = inspect.getsource(lb._turn)
        assert "X-OpenWebUI-User-Id" in src, (
            "without the OWUI identity header the proxy answers every probe with "
            "the no-profile block, which this check once timed as a fast turn"
        )

    def test_an_unset_canary_reports_UNMEASURED_not_a_pass(self):
        src = inspect.getsource(lb.main)
        assert "SNFLWR_CANARY_OWUI_USER_ID" in src
        i = src.index("SNFLWR_CANARY_OWUI_USER_ID")
        block = src[i : i + 900]
        assert "UNMEASURED" in block, (
            "an unconfigured canary must not read as a latency verdict"
        )

    def test_the_canary_id_is_not_hardcoded(self):
        """It keys a child profile. A production canary's identifier does not
        belong in source, and a hardcoded one would also rot silently."""
        src = pathlib.Path(
            pathlib.Path(__file__).resolve().parent.parent
            / "scripts"
            / "latency_bar.py"
        ).read_text()
        assert "os.environ.get(\"SNFLWR_CANARY_OWUI_USER_ID\"" in src
        # a bare uuid literal would be the hardcoding this forbids
        assert not re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", src
        ), "a profile identifier is hardcoded in the script"

    def test_the_SENTINELS_still_catch_the_no_profile_string(self):
        """Belt and braces: even correctly configured, a profile that lapses must
        not silently score as a fast turn."""
        assert any("No learning profile" in s for s in lb._SENTINELS)
