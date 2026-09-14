"""The reveal confirm must not run on the tutor model.

A safety classifier running on the tutor inherits the tutor's SYSTEM PROMPT.
Measured 2026-09-13 on 20 known reveals from a sealed set:

    confirm on snflwr.ai (tutor)   recall  0/20 =   0%   <- non-functional
    confirm on gemma4:e4b (base)   recall 13/20 =  65%

The tutor persona caps length hard ("LENGTH IS A HARD LIMIT ... write LESS"), so
the confirm emitted `{"` and stopped: unparseable -> fail open -> every reveal
served, with no error and a healthy container. It broke when an unrelated PR
added a paragraph to the Modelfile.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _app():
    from fastapi import FastAPI

    import api.routes.ollama_proxy as proxy_mod
    from core.authentication import AuthSession

    app = FastAPI()
    app.include_router(proxy_mod.router)
    app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
        user_id="internal_service", role="user", session_token="t",
        email="internal@snflwr.ai")
    return app


def _safe():
    from safety.pipeline import Category, SafetyResult, Severity

    return SafetyResult(is_safe=True, severity=Severity.NONE,
                        category=Category.VALID, reason="")


def _models_used(confirm_model: str, gate_model: str) -> list[str]:
    """Run one homework turn and return the model of every upstream call."""
    from fastapi.testclient import TestClient

    from config import system_config

    seen: list[str] = []

    async def _fwd(method, path, content=None, headers=None, **kw):
        import json as _j

        seen.append(_j.loads(content).get("model", ""))
        # 1 = the tutor's answer, 2 = the input gate (must say HOMEWORK or the
        # confirm never runs), 3+ = the confirm / regeneration.
        if len(seen) == 1:
            body = "The answer is 56."
        elif len(seen) == 2:
            body = '{"asks_for_assigned_work": true}'
        else:
            body = '{"revealed": false}'  
        return httpx.Response(200, json={
            "model": "snflwr.ai", "done": True,
            "message": {"role": "assistant", "content": body}})

    pipeline = MagicMock()
    pipeline.check_input.return_value = _safe()
    pipeline.check_output.return_value = _safe()

    with (
        patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", True),
        patch.object(system_config, "GUIDANCE_ENFORCER_CONFIRM_MODEL", confirm_model),
        patch.object(system_config, "GUIDANCE_GATE_MODEL", gate_model),
        patch("api.routes.ollama_proxy.access._get_user_from_headers",
              return_value=("u", "user")),
        patch("api.routes.ollama_proxy.profile._get_profile_for_user",
              new=AsyncMock(return_value="p")),
        patch("api.routes.ollama_proxy.transport._forward_request", new=_fwd),
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        TestClient(_app()).post(
            "/api/chat",
            json={"model": "snflwr.ai", "stream": False,
                  "messages": [{"role": "user", "content":
                                "Just give me the answer to 7 times 8."}]},
            headers={"X-OpenWebUI-User-Id": "u", "X-OpenWebUI-User-Role": "user"})
    return seen


class TestConfirmModelResolution:
    def test_confirm_falls_back_to_the_gate_model_not_the_tutor(self):
        used = _models_used(confirm_model="", gate_model="gemma4:e4b")
        assert len(used) > 1, "the confirm never ran"
        assert "gemma4:e4b" in used[1:], (
            f"confirm did not use the gate model; calls were {used}")
        assert used[1:].count("snflwr.ai") == 0 or "gemma4:e4b" in used[1:], (
            "the confirm fell back to the tutor, which inherits its system prompt")

    def test_an_explicit_confirm_model_wins(self):
        used = _models_used(confirm_model="llama-guard3-cpu:latest",
                            gate_model="gemma4:e4b")
        assert "llama-guard3-cpu:latest" in used[1:]

    def test_the_tutor_is_only_the_last_resort(self):
        # With neither configured there is nothing else to use, so the tutor is
        # allowed -- but the deploy-time self-test then fails loudly.
        used = _models_used(confirm_model="", gate_model="")
        assert used[0] == "snflwr.ai"
