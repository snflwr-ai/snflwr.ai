"""A certificate must name what it certifies.

`CERTIFIED_BACKBONES` pins the model name, its base, its window and its VRAM
footprint. It did not pin the SYSTEM PROMPT -- and the tutor's pedagogy, its
homework integrity rules and its safety posture all live in that prompt. A
rebuild with a different prompt kept the name, matched the certificate, and went
on reporting "certified" for a configuration nobody had measured.

Not hypothetical: the reveal confirm collapsed to 0/20 recall in production when
an unrelated PR added a paragraph to the Modelfile. No error, healthy container,
green deploy. Nothing in the system knew what the prompt was supposed to be.
"""

import hashlib
import inspect
import pathlib
import re

import pytest

from core.serving_plan import (
    CERTIFIED_BACKBONES,
    certified_prompt_for,
    fingerprint_system_prompt,
)

MODELFILE = pathlib.Path(__file__).resolve().parent.parent / "models" / "Snflwr_AI_Kids.modelfile"


def _code_only(fn) -> str:
    """Source with comments and the docstring removed.

    Guards that grep source must read CODE. A docstring explaining the failure a
    check prevents will contain the very words the guard forbids.
    """
    src = inspect.getsource(fn)
    body = re.sub(r'"""(?:.|\n)*?"""', "", src, count=1)
    return "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))


def _repo_system_prompt() -> str:
    m = re.search(r'SYSTEM """(.*?)"""', MODELFILE.read_text(), re.S)
    assert m, "could not find the SYSTEM block in the tutor Modelfile"
    return m.group(1)


class TestEveryCertifiedBackboneNamesItsPrompt:
    def test_no_backbone_is_certified_without_a_prompt_fingerprint(self):
        for entry in CERTIFIED_BACKBONES:
            assert entry.system_sha256, (
                f"{entry.model} is certified with no system_sha256 -- its prompt "
                "can be replaced without anything noticing"
            )

    def test_the_fingerprint_matches_the_prompt_in_the_repo(self):
        """The Modelfile and the SHIPPING fingerprint must agree.

        This compares against what the build ships, not against what the sealed
        run measured -- those are different facts since 2026-09-21. A deliberate
        prompt change updates `shipped_system_sha256` (and must then declare the
        grade stale); it does not touch `system_sha256`.

        If this fails, the Modelfile and the certificate disagree: someone edited
        the prompt without updating the fingerprint, or vice versa.
        """
        expected = certified_prompt_for("snflwr.ai-31b")
        actual = fingerprint_system_prompt(_repo_system_prompt())
        assert actual == expected, (
            f"the tutor prompt in the repo fingerprints as {actual}, but this "
            f"build declares it ships {expected}. Update "
            "shipped_system_sha256, or put the prompt back."
        )


class TestTheFingerprintItself:
    def test_it_is_a_short_sha256(self):
        fp = fingerprint_system_prompt("hello")
        assert fp == hashlib.sha256(b"hello").hexdigest()[:12]

    def test_surrounding_whitespace_is_ignored(self):
        """Ollama round-trips the prompt through a Modelfile and can add or drop
        a trailing newline. A certificate that trips on a newline gets turned
        off within a week."""
        assert fingerprint_system_prompt("abc") == fingerprint_system_prompt("\n  abc \n\n")

    def test_an_interior_change_DOES_move_it(self):
        """The whole point. One added paragraph is what broke the confirm."""
        base = "You are a tutor. Do not give the answer."
        assert fingerprint_system_prompt(base) != fingerprint_system_prompt(
            base + "\nAlways be concise."
        )

    def test_an_unknown_model_has_no_certified_prompt(self):
        assert certified_prompt_for("some-model-nobody-sealed") == ""

    @pytest.mark.parametrize("empty", ["", "   ", "\n"])
    def test_empty_prompts_fingerprint_alike_and_are_caught_elsewhere(self, empty):
        """An empty prompt is a broken tutor, not a prompt change. The deploy
        check rejects it by emptiness before it ever compares fingerprints."""
        assert fingerprint_system_prompt(empty) == fingerprint_system_prompt("")


class TestTheDeployCheckIsWiredIn:
    def test_the_smoke_test_runs_the_fingerprint_check(self):
        import scripts.postdeploy_smoke as smoke

        assert hasattr(smoke, "_check_the_running_prompt_is_the_certified_one")
        whole = pathlib.Path(inspect.getfile(smoke)).read_text()
        assert "_check_the_running_prompt_is_the_certified_one()" in whole, (
            "the fingerprint check exists but nothing calls it"
        )

    def test_it_reads_the_RUNNING_prompt_not_the_repo_one(self):
        """Verifying the artifact that runs. A check that re-reads the Modelfile
        would pass while the container serves a model built from something else
        -- which is how a repo fix that never deployed left a watchdog
        restart-looping for five weeks behind a green history."""
        import scripts.postdeploy_smoke as smoke

        src = _code_only(smoke._check_the_running_prompt_is_the_certified_one)
        assert "/api/show" in src, "the check does not ask the running model for its prompt"
        # Comments are stripped first: the docstring DESCRIBES the Modelfile
        # incident that motivated the check, and a naive substring search
        # matches the explanation rather than the behaviour.
        assert "Modelfile" not in src, "the check reads the repo instead of the container"

    def test_DRIFT_fails_the_deploy(self):
        """The running prompt not matching what the build ships is a failure.

        This is the integrity half. It fires when the container serves a model
        built from something other than this repo's Modelfile -- the shape that
        let a confirm drop to 0/20 recall in production behind a green deploy.
        """
        import scripts.postdeploy_smoke as smoke

        src = inspect.getsource(smoke._check_the_running_prompt_is_the_certified_one)
        assert "tutor prompt does not match the build" in src
        assert "drift failure" in src

    def test_a_STALE_GRADE_is_reported_every_deploy_but_does_not_fail_it(self):
        """The currency half, split out from integrity on 2026-09-21.

        These were one check, so the only way to ship a deliberate prompt change
        was to overwrite the sealed hash -- which silently converts "I shipped a
        prompt" into "I re-measured the bars". Now a changed prompt still ships,
        and every deploy prints that the sealed grade does not describe it.

        A stale grade must NOT fail the deploy (a prompt can be changed on
        narrower evidence than a sealed run), and must NOT be silent (nobody may
        quote the tutoring-A grade for a prompt the sealed run never saw).
        """
        import scripts.postdeploy_smoke as smoke

        src = inspect.getsource(smoke._check_the_running_prompt_is_the_certified_one)
        assert "grade_carries_for" in src, (
            "the deploy no longer checks whether the sealed grade describes the "
            "prompt it is serving"
        )
        assert "[NOTE]" in src, "a stale grade must be reported"
        assert "Do not quote the tutoring-A grade" in src
        # the stale path returns [] -- reported, not fatal
        stale_block = src[src.index("grade_carries_for"):]
        assert "return [" not in stale_block.split("except")[0], (
            "a stale sealed grade must not fail the deploy"
        )


class TestSealedAndShippedAreSeparateFacts:
    """One field carried two facts until 2026-09-21, and shipping exposed it.

    * INTEGRITY -- is the running model serving the prompt this build ships?
    * CURRENCY  -- were the tutoring bars measured against that prompt?

    With a single `system_sha256`, the only way to ship a prompt change was to
    overwrite it, which silently asserts a re-measurement that did not happen.
    The sealed prereg forbids re-running set S ("no re-runs on a sealed set"),
    so re-certification is a genuine cost, not a hash edit -- which is exactly
    why the cheap path must not look like the honest one.
    """

    def test_the_sealed_hash_records_what_was_SEALED(self):
        from core.serving_plan import sealed_prompt_for

        # The 2026-09-17 sealed tutoring-A run. This value may only change when
        # a new sealed run has actually been done.
        assert sealed_prompt_for("snflwr.ai-31b") == "43a76481684d"

    def test_integrity_checks_compare_against_what_SHIPS_not_what_was_sealed(self):
        """Otherwise a deliberate prompt change makes the drift guard cry wolf,
        and a guard that cries wolf gets switched off."""
        from core.serving_plan import certified_prompt_for, sealed_prompt_for

        shipping = certified_prompt_for("snflwr.ai-31b")
        assert shipping, "no shipping fingerprint -- the drift guard has no target"
        if shipping != sealed_prompt_for("snflwr.ai-31b"):
            from core.serving_plan import grade_carries_for

            carries, why = grade_carries_for("snflwr.ai-31b")
            assert not carries, (
                "the shipping prompt differs from the sealed one, so the grade "
                "cannot carry -- something is claiming it does"
            )
            assert why, "a stale grade must say WHY and what re-certifying costs"

    def test_a_changed_prompt_cannot_silently_keep_the_grade(self):
        """The property that matters, stated as an invariant rather than a value.

        If shipped != sealed then grade_carries MUST be False. A future edit that
        bumps the shipped hash and forgets the staleness note fails here.
        """
        from core.serving_plan import CERTIFIED_BACKBONES

        for e in CERTIFIED_BACKBONES:
            if e.shipped_system_sha256 and e.shipped_system_sha256 != e.system_sha256:
                assert not e.grade_carries, (
                    f"{e.model} ships {e.shipped_system_sha256} but was sealed at "
                    f"{e.system_sha256} and still claims its grade carries"
                )
                assert e.sealed_grade_stale, (
                    f"{e.model} ships a prompt the sealed run never saw with no "
                    "explanation of what changed or what re-certifying costs"
                )

    def test_the_staleness_note_names_the_evidence_and_the_cost(self):
        """A note that just says "changed" is a shrug. It has to carry the
        evidence the change shipped on, and what re-certification would take --
        otherwise the next person cannot tell a measured decision from a guess.
        """
        from core.serving_plan import CERTIFIED_BACKBONES

        for e in CERTIFIED_BACKBONES:
            if not e.sealed_grade_stale:
                continue
            note = e.sealed_grade_stale
            assert any(d in note for d in ("2026-", "2027-")), "note has no date"
            assert "set R" in note or "sealed run" in note, (
                "the note must say what re-certifying would cost"
            )
