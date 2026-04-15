"""
Additional coverage tests for safety/pipeline.py.

Targets the specific uncovered lines identified by coverage analysis:
    - 223-224: _strip_invisible error fallback
    - 599, 604-605: Regex compilation failures in keyword patterns
    - 738, 745, 761: Shared pattern allowlist skip, educational exemption, substring evasion
    - 851-856, 861: Semantic classifier _find_model and ImportError
    - 878-881: Fallback model selection in _find_model
    - 905-906, 922-923: Email alert error handling in _transition_state
    - 928, 937-939: _probe_ollama null client and model-not-found
    - 943-961: run_health_probe loop (recovery, degraded, cancelled, exception)
"""

import asyncio
import json
import re
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_logger():
    """Patch logger and log_safety_incident for all tests."""
    with patch("safety.pipeline.get_logger") as mock_get_logger, \
         patch("safety.pipeline.log_safety_incident"):
        mock_get_logger.return_value = MagicMock()
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_classifier():
    """Create a bare _SemanticClassifier without running __init__."""
    from safety.pipeline import _SemanticClassifier
    obj = _SemanticClassifier.__new__(_SemanticClassifier)
    obj._available = False
    obj._model = None
    obj._client = None
    obj._state = "disabled"
    obj._state_since = None
    obj._probe_task = None
    obj._OllamaError = Exception
    return obj


# ============================================================================
# _strip_invisible error fallback (lines 223-224)
# ============================================================================

class TestStripInvisibleErrorFallback:
    """Cover the except branch in _strip_invisible."""

    def test_returns_original_on_non_iterable(self):
        """If the generator expression raises, return the original text."""
        from safety.pipeline import _strip_invisible, _STRIP_CHARS

        # Patch _STRIP_CHARS to something that raises on `in` checks
        class BrokenSet:
            def __contains__(self, item):
                raise RuntimeError("deliberate")

        with patch("safety.pipeline._STRIP_CHARS", BrokenSet()):
            result = _strip_invisible("hello")
            assert result == "hello"


# ============================================================================
# Keyword pattern regex compilation failure (lines 599, 604-605)
# ============================================================================

class TestKeywordPatternCompilationFailure:
    """Cover the re.error branch when compiling keyword patterns."""

    def test_bad_regex_keyword_is_skipped(self):
        """If re.compile raises re.error for a keyword, it is skipped."""
        from safety.pipeline import _PatternMatcher

        original_compile = re.compile

        # Make re.compile raise re.error for a specific keyword
        def patched_compile(pattern, flags=0):
            if "badkeyword" in pattern:
                raise re.error("deliberate test error")
            return original_compile(pattern, flags)

        # Inject a bad keyword via config
        fake_config_keywords = {
            "violence": ["badkeyword"],
        }

        with patch("safety.pipeline.safety_config") as mock_config:
            mock_config.PROHIBITED_KEYWORDS = fake_config_keywords
            with patch("safety.pipeline.re.compile", side_effect=patched_compile):
                # _build_prohibited_patterns is called during __init__
                matcher = _PatternMatcher()
                # The bad keyword should be silently skipped; matcher should work
                # Check that "badkeyword" is NOT in the compiled patterns
                kw_list = [kw for _, kw, _ in matcher._prohibited_patterns]
                assert "badkeyword" not in kw_list

    def test_empty_keyword_is_skipped(self):
        """Empty string keywords should be skipped (line 598-599 'not kw' branch)."""
        from safety.pipeline import _PatternMatcher

        fake_config_keywords = {
            "violence": ["", "gun"],
        }

        with patch("safety.pipeline.safety_config") as mock_config:
            mock_config.PROHIBITED_KEYWORDS = fake_config_keywords
            matcher = _PatternMatcher()
            kw_list = [kw for _, kw, _ in matcher._prohibited_patterns]
            assert "" not in kw_list
            # "gun" should still be present (from extended or config)
            assert "gun" in kw_list


# ============================================================================
# Shared pattern allowlist skip (line 738)
# ============================================================================

class TestSharedPatternAllowlistSkip:
    """Cover the allowlist skip path in shared bilingual pattern matching."""

    def test_allowlisted_word_skips_shared_pattern(self):
        """When matched text is in an allowlisted word, the match is skipped.

        We must mock _SHARED_COMPILED with a regex whose match text is a
        substring of an allowlisted word, AND ensure the prohibited-keyword
        stage (2a) does not catch the input first.  We achieve this by using
        a nonsense fake allowlisted word/pattern so nothing in the real
        prohibited list interferes.
        """
        from safety.pipeline import _PatternMatcher
        import safety.pipeline as pipeline_mod

        matcher = _PatternMatcher()

        # Fake allowlist containing "foobarx", fake shared pattern matching "foobar"
        fake_allowlist = frozenset({"foobarx"})
        fake_pattern = re.compile(r"foobar", re.IGNORECASE)

        with patch.object(pipeline_mod, "_SHARED_COMPILED", {
            "HATE_SPEECH": [(fake_pattern, "fake slur")],
        }), patch.object(pipeline_mod, "_SHARED_SUBSTR", {}), \
             patch.object(pipeline_mod, "_SHARED_ALLOWLIST", fake_allowlist):
            # input_words = {"foobarx"}, allowlisted_words = {"foobarx"}
            # regex matches "foobar" from "foobarx", "foobar" in "foobarx" -> True
            result = matcher.check("foobarx", "foobarx")
            assert result is None  # skipped due to allowlist


# ============================================================================
# Shared pattern educational exemption for VIOLENCE (lines 740-744)
# ============================================================================

class TestSharedPatternEducationalExemption:
    """Cover the educational exemption branch in shared pattern matching."""

    def test_violence_term_with_educational_context_skipped(self):
        """A shared VIOLENCE pattern with desc starting 'violence term'
        should be skipped if educational context is present."""
        from safety.pipeline import _PatternMatcher
        import safety.pipeline as pipeline_mod

        matcher = _PatternMatcher()

        # Patch _SHARED_COMPILED to have a VIOLENCE category pattern with
        # desc starting "violence term" that matches our input
        fake_pattern = re.compile(r"\bkill\b", re.IGNORECASE)
        with patch.object(pipeline_mod, "_SHARED_COMPILED", {
            "VIOLENCE": [(fake_pattern, "violence term: kill")],
        }), patch.object(pipeline_mod, "_SHARED_SUBSTR", {}):
            # With educational context ("biology"), should be exempt
            result = matcher.check(
                "in biology class, animals kill for food",
                "inbiologyclassanimalskillforpood",
            )
            # Should be None (exempted) because educational context present
            assert result is None

    def test_violence_term_without_educational_context_blocked(self):
        """A shared VIOLENCE pattern without educational context blocks."""
        from safety.pipeline import _PatternMatcher
        import safety.pipeline as pipeline_mod

        matcher = _PatternMatcher()

        fake_pattern = re.compile(r"\bkill\b", re.IGNORECASE)
        with patch.object(pipeline_mod, "_SHARED_COMPILED", {
            "VIOLENCE": [(fake_pattern, "violence term: kill")],
        }), patch.object(pipeline_mod, "_SHARED_SUBSTR", {}):
            # Without educational context, should block
            # But "kill" will first be caught by prohibited keywords.
            # Use a word not in the prohibited list.
            pass

        # Use a direct approach: patch to bypass prohibited keywords
        fake_pattern2 = re.compile(r"\bstomp\b", re.IGNORECASE)
        with patch.object(pipeline_mod, "_SHARED_COMPILED", {
            "VIOLENCE": [(fake_pattern2, "violence term: stomp")],
        }), patch.object(pipeline_mod, "_SHARED_SUBSTR", {}):
            result = matcher.check("stomp on things", "stomponthings")
            assert result is not None
            assert result.is_safe is False
            assert "Shared pattern matched" in result.reason


# ============================================================================
# Substring evasion detection (line 760-761)
# ============================================================================

class TestSubstringEvasionDetection:
    """Cover the substring evasion check path (lines 753-767)."""

    def test_substring_evasion_detected(self):
        """When a prohibited substring is found in normalized text, block."""
        from safety.pipeline import _PatternMatcher, Category
        import safety.pipeline as pipeline_mod

        matcher = _PatternMatcher()

        # Patch _SHARED_SUBSTR to have a known substring
        with patch.object(pipeline_mod, "_SHARED_SUBSTR", {
            "HATE_SPEECH": [("xyzevil", "test evasion phrase")],
        }), patch.object(pipeline_mod, "_SHARED_COMPILED", {}):
            result = matcher.check(
                "some random text",
                "containsxyzevilevasion",
            )
            assert result is not None
            assert result.is_safe is False
            assert "Substring evasion" in result.reason
            assert result.category == Category.HATE_SPEECH


# ============================================================================
# Semantic classifier __init__ paths (lines 851-856, 861)
# ============================================================================

class TestSemanticClassifierInit:
    """Cover init branches: model found, model not found, ImportError."""

    def test_init_model_found(self):
        """When Ollama is reachable and model is found, state -> available."""
        from safety.pipeline import _SemanticClassifier

        mock_client_cls = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.check_connection.return_value = (True, "0.1")
        mock_client_instance.list_models.return_value = (
            True,
            [{"name": "llama-guard3:8b"}],
            None,
        )
        mock_client_cls.return_value = mock_client_instance

        mock_error_cls = type("OllamaError", (Exception,), {})

        with patch.dict("sys.modules", {
            "utils.ollama_client": MagicMock(
                OllamaClient=mock_client_cls,
                OllamaError=mock_error_cls,
            ),
        }):
            cls = _SemanticClassifier()
            assert cls._available is True
            assert cls._state == "available"
            assert cls._model == "llama-guard3:8b"

    def test_init_model_not_found(self):
        """When Ollama is reachable but no model found, stays disabled."""
        from safety.pipeline import _SemanticClassifier

        mock_client_cls = MagicMock()
        mock_client_instance = MagicMock()
        mock_client_instance.check_connection.return_value = (True, "0.1")
        mock_client_instance.list_models.return_value = (True, [], None)
        mock_client_cls.return_value = mock_client_instance

        mock_error_cls = type("OllamaError", (Exception,), {})

        with patch.dict("sys.modules", {
            "utils.ollama_client": MagicMock(
                OllamaClient=mock_client_cls,
                OllamaError=mock_error_cls,
            ),
        }):
            cls = _SemanticClassifier()
            assert cls._available is False
            assert cls._model is None

    def test_init_import_error(self):
        """When ollama_client import fails, classifier stays disabled."""
        from safety.pipeline import _SemanticClassifier

        with patch("builtins.__import__", side_effect=ImportError("no module")):
            cls = _SemanticClassifier()
            assert cls._available is False

    def test_init_generic_exception(self):
        """When __init__ hits a non-ImportError exception, classifier stays disabled."""
        from safety.pipeline import _SemanticClassifier

        mock_client_cls = MagicMock()
        mock_client_cls.return_value.check_connection.side_effect = RuntimeError(
            "unexpected init boom"
        )
        mock_error_cls = type("OllamaError", (Exception,), {})

        with patch.dict("sys.modules", {
            "utils.ollama_client": MagicMock(
                OllamaClient=mock_client_cls,
                OllamaError=mock_error_cls,
            ),
        }):
            cls = _SemanticClassifier()
            assert cls._available is False
            assert cls._state == "disabled"


# ============================================================================
# _find_model fallback selection (lines 878-881)
# ============================================================================

class TestFindModelFallback:
    """Cover fallback model selection in _find_model."""

    def test_preferred_model_found(self):
        """When preferred model is available, return it."""
        cls = _make_classifier()
        mock_client = MagicMock()
        mock_client.list_models.return_value = (
            True,
            [{"name": "llama-guard3:8b"}, {"name": "llama-guard3:1b"}],
            None,
        )
        cls._client = mock_client

        result = cls._find_model()
        assert result == "llama-guard3:8b"

    def test_fallback_model_used(self):
        """When preferred is missing but a fallback is available, return it."""
        cls = _make_classifier()
        mock_client = MagicMock()
        mock_client.list_models.return_value = (
            True,
            [{"name": "some-other-model"}, {"name": "llama-guard3:1b"}],
            None,
        )
        cls._client = mock_client

        result = cls._find_model()
        assert result == "llama-guard3:1b"

    def test_no_model_available(self):
        """When neither preferred nor fallbacks are available, return None."""
        cls = _make_classifier()
        mock_client = MagicMock()
        mock_client.list_models.return_value = (
            True,
            [{"name": "unrelated-model"}],
            None,
        )
        cls._client = mock_client

        result = cls._find_model()
        assert result is None

    def test_list_models_fails(self):
        """When list_models returns success=False, return None."""
        cls = _make_classifier()
        mock_client = MagicMock()
        mock_client.list_models.return_value = (False, None, "error")
        cls._client = mock_client

        result = cls._find_model()
        assert result is None


# ============================================================================
# _transition_state email alert error handling (lines 905-906, 922-923)
# ============================================================================

class TestTransitionStateAlerts:
    """Cover email alert exception handling in _transition_state."""

    def test_recovery_alert_exception_suppressed(self):
        """Email error during recovery alert is silently suppressed."""
        cls = _make_classifier()
        cls._state = "degraded"
        cls._model = "test-model"

        with patch.dict("sys.modules", {
            "core": MagicMock(),
            "core.email_service": MagicMock(
                email_service=MagicMock(
                    send_operator_alert=MagicMock(
                        side_effect=RuntimeError("SMTP down")
                    )
                )
            ),
        }):
            # Should not raise despite the email error
            cls._transition_state("available")
            assert cls._state == "available"
            assert cls._available is True

    def test_degraded_alert_exception_suppressed(self):
        """Email error during degradation alert is silently suppressed."""
        cls = _make_classifier()
        cls._state = "available"
        cls._available = True

        with patch.dict("sys.modules", {
            "core": MagicMock(),
            "core.email_service": MagicMock(
                email_service=MagicMock(
                    send_operator_alert=MagicMock(
                        side_effect=RuntimeError("SMTP down")
                    )
                )
            ),
        }):
            cls._transition_state("degraded")
            assert cls._state == "degraded"
            assert cls._available is False

    def test_same_state_noop(self):
        """Transition to same state is a no-op."""
        cls = _make_classifier()
        cls._state = "available"
        cls._available = True

        cls._transition_state("available")
        assert cls._state == "available"


# ============================================================================
# _probe_ollama (lines 928, 937-939)
# ============================================================================

class TestProbeOllama:
    """Cover _probe_ollama branches."""

    def test_probe_no_client_returns_false(self):
        """When _client is None, probe returns False immediately."""
        cls = _make_classifier()
        cls._client = None
        assert cls._probe_ollama() is False

    def test_probe_connection_fails(self):
        """When check_connection returns (False, ...), probe returns False."""
        cls = _make_classifier()
        mock_client = MagicMock()
        mock_client.check_connection.return_value = (False, None)
        cls._client = mock_client

        assert cls._probe_ollama() is False

    def test_probe_model_not_found_returns_false(self):
        """When _find_model returns None, probe returns False."""
        cls = _make_classifier()
        mock_client = MagicMock()
        mock_client.check_connection.return_value = (True, "0.1")
        mock_client.list_models.return_value = (True, [], None)
        cls._client = mock_client

        assert cls._probe_ollama() is False

    def test_probe_model_found_returns_true(self):
        """When _find_model returns a model name, probe returns True."""
        cls = _make_classifier()
        mock_client = MagicMock()
        mock_client.check_connection.return_value = (True, "0.1")
        mock_client.list_models.return_value = (
            True,
            [{"name": "llama-guard3:8b"}],
            None,
        )
        cls._client = mock_client

        assert cls._probe_ollama() is True
        assert cls._model == "llama-guard3:8b"

    def test_probe_exception_returns_false(self):
        """When check_connection raises, probe returns False."""
        cls = _make_classifier()
        mock_client = MagicMock()
        mock_client.check_connection.side_effect = RuntimeError("boom")
        cls._client = mock_client

        assert cls._probe_ollama() is False


# ============================================================================
# run_health_probe async loop (lines 943-961)
# ============================================================================

class TestRunHealthProbe:
    """Cover the async health probe loop."""

    @pytest.mark.asyncio
    async def test_probe_recovers_from_degraded(self):
        """When probe succeeds and state is degraded, transitions to available."""
        cls = _make_classifier()
        cls._state = "degraded"

        call_count = 0

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        mock_client = MagicMock()
        mock_client.check_connection.return_value = (True, "0.1")
        mock_client.list_models.return_value = (
            True,
            [{"name": "llama-guard3:8b"}],
            None,
        )
        cls._client = mock_client

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("asyncio.get_event_loop") as mock_loop:
            async def fake_executor(executor, fn):
                return fn()

            mock_loop.return_value.run_in_executor = fake_executor

            await cls.run_health_probe()

        assert cls._state == "available"

    @pytest.mark.asyncio
    async def test_probe_degrades_from_available(self):
        """When probe fails and state is available, transitions to degraded."""
        cls = _make_classifier()
        cls._state = "available"
        cls._available = True

        call_count = 0

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        cls._client = None  # _probe_ollama returns False when client is None

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("asyncio.get_event_loop") as mock_loop:
            async def fake_executor(executor, fn):
                return fn()

            mock_loop.return_value.run_in_executor = fake_executor

            await cls.run_health_probe()

        assert cls._state == "degraded"

    @pytest.mark.asyncio
    async def test_probe_stays_degraded_logs_debug(self):
        """When probe fails and state is already degraded, stays degraded."""
        cls = _make_classifier()
        cls._state = "degraded"
        cls._client = None  # probe returns False

        call_count = 0

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("asyncio.get_event_loop") as mock_loop:
            async def fake_executor(executor, fn):
                return fn()

            mock_loop.return_value.run_in_executor = fake_executor

            await cls.run_health_probe()

        assert cls._state == "degraded"

    @pytest.mark.asyncio
    async def test_probe_handles_generic_exception(self):
        """Generic exceptions in probe loop are caught and logged."""
        cls = _make_classifier()
        cls._state = "degraded"

        call_count = 0

        async def mock_sleep(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep), \
             patch("asyncio.get_event_loop") as mock_loop:
            async def fake_executor(executor, fn):
                raise RuntimeError("executor boom")

            mock_loop.return_value.run_in_executor = fake_executor

            # Should not raise
            await cls.run_health_probe()

    @pytest.mark.asyncio
    async def test_probe_cancelled_exits_cleanly(self):
        """CancelledError breaks the loop immediately."""
        cls = _make_classifier()
        cls._state = "disabled"

        async def mock_sleep(duration):
            raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await cls.run_health_probe()
        # Should exit without error


# ============================================================================
# Pattern matcher fail-closed exception handler
# ============================================================================

class TestPatternMatcherFailClosed:
    """Cover the broad except at the end of _PatternMatcher.check (line 782)."""

    def test_exception_in_check_returns_block(self):
        """If pattern matching raises unexpectedly, result is a BLOCK."""
        from safety.pipeline import _PatternMatcher, Category

        matcher = _PatternMatcher()
        # Force an exception by making _danger_phrases iteration blow up
        matcher._danger_phrases = None  # iterating None raises TypeError

        result = matcher.check("hello", "hello")
        assert result is not None
        assert result.is_safe is False
        assert result.category == Category.CLASSIFIER_ERROR
        assert "fail closed" in result.reason.lower()
