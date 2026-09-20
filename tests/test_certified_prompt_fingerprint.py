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
        """The shipped Modelfile and the certificate must agree.

        If this fails, either the prompt was edited without re-measuring the
        tutoring bars, or the bars were re-measured and the certificate was not
        updated. Both are worth stopping for.
        """
        expected = certified_prompt_for("snflwr.ai-31b")
        actual = fingerprint_system_prompt(_repo_system_prompt())
        assert actual == expected, (
            f"the tutor prompt in the repo fingerprints as {actual}, but the "
            f"sealed run certified {expected}. The tutoring grade was measured "
            "against a different prompt and does not carry over."
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

    def test_it_fails_rather_than_disabling_tutoring(self):
        """Pre-launch, changing the prompt is expected and cheap. What must not
        happen is changing it QUIETLY and inheriting a grade the new prompt
        never earned, so the consequence is a loud deploy failure."""
        import scripts.postdeploy_smoke as smoke

        src = inspect.getsource(smoke._check_the_running_prompt_is_the_certified_one)
        assert "tutor prompt is uncertified" in src
        assert "re-measure" in src.lower() or "Re-measure" in src
