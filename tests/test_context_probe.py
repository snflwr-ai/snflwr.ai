"""Install-time context probing: find the biggest window this card really holds.

Measured on the reference deployment (RTX 3090 Ti, 22.5 GiB usable, 31b tutor):
    16384 -> loads, ~21.0 GiB in use
    24576 -> loads, ~21.6 GiB in use
    32768 -> OOM, the model unloads

Note what that means: a formula would have said 32k fits (21.6 + 0.6 GiB of KV
is under 22.5) and it did not, because transient compute buffers are not in the
resident figure. So the probe LOADS the model and makes it answer, rather than
predicting from arithmetic.
"""

import pytest

from core import context_probe


class FakeEngine:
    """Loads succeed up to a ceiling, as a real card behaves."""

    def __init__(self, ceiling, fail_with=None):
        self.ceiling = ceiling
        self.fail_with = fail_with
        self.tried = []

    def try_context(self, model, num_ctx, timeout_s=None):
        self.tried.append(num_ctx)
        if self.fail_with:
            raise self.fail_with
        return num_ctx <= self.ceiling


class TestProbe:
    def test_picks_the_largest_window_that_loads(self):
        engine = FakeEngine(ceiling=24576)
        assert context_probe.probe(engine, "m", candidates=(32768, 24576, 16384)) == 24576

    def test_stops_at_the_first_success(self):
        engine = FakeEngine(ceiling=32768)
        context_probe.probe(engine, "m", candidates=(32768, 24576, 16384))
        assert engine.tried == [32768]

    def test_returns_none_when_nothing_loads(self):
        engine = FakeEngine(ceiling=0)
        assert context_probe.probe(engine, "m", candidates=(32768, 16384)) is None

    def test_an_engine_error_is_not_a_fit(self):
        engine = FakeEngine(ceiling=99999, fail_with=RuntimeError("engine down"))
        assert context_probe.probe(engine, "m", candidates=(16384,)) is None

    def test_candidates_are_tried_largest_first(self):
        engine = FakeEngine(ceiling=16384)
        context_probe.probe(engine, "m", candidates=(16384, 32768, 24576))
        assert engine.tried == [32768, 24576, 16384]


class TestCapAtValidated:
    """A bigger window than anything measured is not on offer: the certified
    table records the largest context a tutoring run actually validated."""

    def test_probe_result_is_capped(self):
        assert context_probe.choose(probed=24576, validated_max=16384) == 16384

    def test_a_smaller_card_keeps_its_probed_value(self):
        assert context_probe.choose(probed=8192, validated_max=16384) == 8192

    def test_equal_values_pass_through(self):
        assert context_probe.choose(probed=16384, validated_max=16384) == 16384

    def test_no_probe_result_falls_back_to_the_sealed_window(self):
        assert context_probe.choose(probed=None, validated_max=16384) == 16384


def _install_config(**extra):
    """The minimum an install config needs for the env writer."""
    config = {
        "DATABASE_TYPE": "sqlite",
        "SNFLWR_DATA_DIR": "/tmp/snflwr-test",
        "ENCRYPTION_KEY_PATH": "/tmp/snflwr-test/key",
        "LOG_PATH": "/tmp/snflwr-test/logs",
        "JWT_SECRET_KEY": "test-only-not-a-secret",
        "PARENT_DASHBOARD_PASSWORD": "test-only-not-a-secret",
        "OLLAMA_DEFAULT_MODEL": "snflwr.ai-31b",
    }
    config.update(extra)
    return config


class TestInstallerWiring:
    """The probe's result has to reach the env file, or it was decoration."""

    def test_the_env_writer_emits_the_probed_window(self, tmp_path, monkeypatch):
        from installer.config import create_env_file

        monkeypatch.chdir(tmp_path)
        create_env_file(_install_config(INFERENCE_NUM_CTX=24576))
        written = (tmp_path / ".env").read_text()
        assert "INFERENCE_NUM_CTX=24576" in written

    def test_no_probe_result_writes_no_window(self, tmp_path, monkeypatch):
        from installer.config import create_env_file

        monkeypatch.chdir(tmp_path)
        create_env_file(_install_config())
        assert "INFERENCE_NUM_CTX" not in (tmp_path / ".env").read_text()

    def test_a_probe_failure_does_not_break_the_install(self, monkeypatch):
        from installer import ollama_setup

        monkeypatch.setattr("core.context_probe.probe",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert ollama_setup.probe_context_window("snflwr.ai-31b") is None
