"""
Regression guards: Qwen3.5 thinking-mode leak.

Background
----------
Ollama 0.17+ auto-attaches `RENDERER qwen3.5` and `PARSER qwen3.5` to any
model built on top of qwen3.5 (visible via `ollama show snflwr.ai
--modelfile`). The parser splits model output into a separate `thinking`
field, which Open WebUI renders as a collapsible "Thinking for X seconds"
block — exposing raw chain-of-thought ("Thinking Process:", "Wait, I need
to reconsider", "Final Decision:") to whoever opens that block.

For a K-12 product this is a UX *and* trust failure: parents and educators
trialling on the admin account see the model second-guessing itself, and
the legacy `PARAMETER stop "Thinking Process:"` lines in the Modelfile do
nothing because the parser strips thinking out of the `content` stream
before stop sequences are evaluated. (Worse: under some conditions the
parser's content/thinking handoff results in the first content tokens
matching one of those stops and the API returning an empty response.)

The fix is to disable thinking mode at the request level (`think: false`)
on every code path that talks to Ollama:

  • snflwr backend  → api/routes/chat.py calls
                      ollama_client.chat(..., think=False)
  • OWUI fork       → frontend/open-webui/backend/open_webui/routers/
                      ollama.py admin-bypass branch sets
                      payload["think"] = False before forwarding to Ollama

These tests lock both call sites in. The first is a behavioural mock
test; the second and third are structural text checks because the OWUI
fork imports from `open_webui.*` (not available in the snflwr Python test
env) and the Modelfile is not Python.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
OWUI_FORK_PATH = (
    REPO_ROOT
    / "frontend"
    / "open-webui"
    / "backend"
    / "open_webui"
    / "routers"
    / "ollama.py"
)
MODELFILE_PATH = REPO_ROOT / "models" / "Snflwr_AI_Kids.modelfile"


# ---------------------------------------------------------------------------
# Behavioural: snflwr backend calls ollama_client.chat with think=False
# ---------------------------------------------------------------------------


class TestSnflwrBackendDisablesThinking:
    """The student chat path must call ollama_client.chat with think=False."""

    def _make_auth_session(self, role="parent", user_id="user-123"):
        s = MagicMock()
        s.role = role
        s.user_id = user_id
        return s

    def _make_profile(self, parent_id="user-123"):
        p = MagicMock()
        p.parent_id = parent_id
        p.is_active = True
        p.age = 10
        p.grade = "5"
        p.name = "Test Child"
        p.learning_level = "adaptive"
        return p

    def test_send_chat_message_passes_think_false(self):
        """
        Lock in api/routes/chat.py:474 → ollama_client.chat(..., think=False).

        Without this kwarg the response would include a `thinking` field
        populated by Ollama's qwen3.5 parser, which the snflwr backend
        does not currently strip (it only strips inline <think>...</think>
        blocks from the content string).
        """
        from api.routes.chat import send_chat_message, ChatRequest
        from safety.pipeline import SafetyResult, Severity, Category

        auth_session = self._make_auth_session()
        profile = self._make_profile(parent_id=auth_session.user_id)

        safe_in = SafetyResult(
            is_safe=True, severity=Severity.NONE, category=Category.VALID,
            reason="", triggered_keywords=(), stage="none",
        )
        safe_out = SafetyResult(
            is_safe=True, severity=Severity.NONE, category=Category.VALID,
            reason="", triggered_keywords=(), stage="none",
        )

        with patch("api.routes.chat.ProfileManager") as mock_pm, \
             patch("api.routes.chat.safety_pipeline") as mock_sp, \
             patch("api.routes.chat.session_manager") as mock_sm, \
             patch("api.routes.chat.safety_monitor"), \
             patch("api.routes.chat.incident_logger"), \
             patch("api.routes.chat.audit_log"), \
             patch("api.routes.chat.ollama_client") as mock_oc, \
             patch("api.routes.chat.conversation_store"), \
             patch(
                 "api.routes.chat._get_or_create_conversation_id",
                 return_value="conv-1",
             ):

            mock_pm.return_value.get_profile.return_value = profile
            mock_sp.check_input.return_value = safe_in
            mock_sp.check_output.return_value = safe_out

            mock_session = MagicMock()
            mock_session.session_id = "sess-test-001"
            mock_sm.get_session.return_value = mock_session
            mock_sm.get_active_session.return_value = mock_session

            mock_oc.chat.return_value = (True, "Four!", {})

            request = ChatRequest(
                message="What is 2+2?",
                profile_id="a" * 32,
                model="snflwr.ai",
            )
            asyncio.run(
                send_chat_message(
                    request=request,
                    auth_session=auth_session,
                    rate_limit_info={},
                )
            )

            assert mock_oc.chat.called, "ollama_client.chat was never called"
            kwargs = mock_oc.chat.call_args.kwargs
            assert "think" in kwargs, (
                "ollama_client.chat must be called with the `think` kwarg — "
                "without it, Qwen3.5 thinking mode is enabled by default and "
                "the response includes a `thinking` field that leaks reasoning "
                "to the UI."
            )
            assert kwargs["think"] is False, (
                f"think kwarg must be False, got {kwargs['think']!r}"
            )


# ---------------------------------------------------------------------------
# Structural: OWUI fork admin-bypass branch sets payload["think"] = False
# ---------------------------------------------------------------------------


class TestOwuiForkAdminBypassDisablesThinking:
    """
    The admin-bypass branch in the OWUI fork must set payload["think"] = False
    before forwarding the request directly to Ollama. This is a structural
    text check because the fork file lives at
    frontend/open-webui/backend/open_webui/routers/ollama.py and imports
    from open_webui.*, which is not available inside the snflwr test
    environment — we cannot exercise it as a Python module here.
    """

    @pytest.fixture(scope="class")
    def fork_source(self) -> str:
        assert OWUI_FORK_PATH.is_file(), (
            f"OWUI fork not found at {OWUI_FORK_PATH} — has the file been "
            f"moved or renamed? If so, update OWUI_FORK_PATH in this test."
        )
        return OWUI_FORK_PATH.read_text()

    def test_admin_bypass_sets_think_false(self, fork_source: str):
        """
        Confirm that the admin/parent direct-Ollama branch sets
        payload["think"] = False. The exact assignment line lives near the
        end of the `else:` arm of the `if not bypass_safety:` block (~lines
        1422-1450 of ollama.py).
        """
        assert 'payload["think"] = False' in fork_source, (
            "OWUI fork at frontend/open-webui/backend/open_webui/routers/"
            "ollama.py must set payload['think'] = False on the admin "
            "bypass branch. Without this, admin/parent users sending chat "
            "requests through Open WebUI receive a response with a "
            "populated `thinking` field which OWUI renders as a collapsible "
            "block, exposing raw model self-deliberation. See "
            "tests/test_no_thinking_leak.py module docstring for context."
        )

    def test_admin_bypass_branch_still_present(self, fork_source: str):
        """
        Sanity check that the bypass branch we are guarding still exists.
        If the OWUI router is rewritten to remove the admin bypass entirely
        this test will fail loudly so the assertion above can be re-evaluated.
        """
        assert "Direct Ollama: no student profile" in fork_source, (
            "The admin-bypass log marker is missing from the OWUI fork. "
            "If the bypass branch has been intentionally removed, delete "
            "TestOwuiForkAdminBypassDisablesThinking. Otherwise the fork "
            "may have been overwritten by an upstream re-rebase — see "
            "docker/compose/docker-compose.home.yml for the bind mount path."
        )


# ---------------------------------------------------------------------------
# Structural: Modelfile no longer carries the obsolete thinking-related stops
# ---------------------------------------------------------------------------


class TestModelfileObsoleteThinkingStopsRemoved:
    """
    The Modelfile previously declared stop sequences for "Thinking Process:",
    "Thinking:", "Analyze the Request:", "Let me think", and "<think>" to
    suppress reasoning leaks. Those stops are inert under Ollama 0.17+
    because the auto-attached qwen3.5 PARSER extracts thinking into a
    separate field before the Modelfile stops are evaluated. Worse, under
    some conditions the parser's content handoff causes the first content
    tokens to match one of these stops and the API returns an empty
    response. The fix relies on `think: false` at the request layer; these
    stops should not be re-added.
    """

    @pytest.fixture(scope="class")
    def modelfile_source(self) -> str:
        assert MODELFILE_PATH.is_file(), (
            f"Modelfile not found at {MODELFILE_PATH}"
        )
        return MODELFILE_PATH.read_text()

    @pytest.mark.parametrize(
        "obsolete_stop",
        [
            'PARAMETER stop "Thinking Process:"',
            'PARAMETER stop "Thinking:"',
            'PARAMETER stop "Analyze the Request:"',
            'PARAMETER stop "Let me think"',
            'PARAMETER stop "<think>"',
        ],
    )
    def test_obsolete_thinking_stop_not_present(
        self, modelfile_source: str, obsolete_stop: str
    ):
        assert obsolete_stop not in modelfile_source, (
            f"{obsolete_stop} is back in the Modelfile. Under Ollama 0.17+ "
            f"this stop is inert (the qwen3.5 PARSER strips thinking out of "
            f"the content stream before stops are evaluated) and can cause "
            f"the API to return an empty response. Disable thinking via "
            f"`think: false` at the request layer instead."
        )
