"""The model narrates its internal age-range hint into its own reply.

Sealed set 10 (2026-09-13) measured this on **26 of 49 served replies (53.1%)**.
Root cause: the system prompt promises `Each student message begins with a hint
like "[Student age range: 5-7]" ... if no hint is present, infer age`, but
nothing in api/ or core/ ever prepends that hint, so the model always takes the
infer branch and then states the inference. Nothing stripped it -- the OpenWebUI
filter has an `inlet` and no `outlet`, and the proxy served upstream content
verbatim.
"""

import json

import pytest

from api.routes.ollama_proxy.blocks import (
    _extract_text_from_ndjson_chunks,
    _strip_age_scaffolding_from_ndjson_chunks,
)
from core.response_scaffolding import leading_scaffolding_len, strip_scaffolding


def _chunks(parts):
    return [
        (json.dumps({"model": "m", "message": {"role": "assistant", "content": p}}) + "\n").encode()
        for p in parts
    ]


class TestStripScaffolding:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("[Student age range: 14-18]\n\nHello there.", "Hello there."),
            ("[Student age range: inferred 8-10] Hi.", "Hi."),
            ("  [Student age range: 5-7]\nAnswer.", "Answer."),
        ],
    )
    def test_leading_tag_removed(self, raw, expected):
        assert strip_scaffolding(raw) == expected

    def test_reply_without_a_tag_is_untouched(self):
        text = "Osmosis moves water across a membrane."
        assert strip_scaffolding(text) is text or strip_scaffolding(text) == text

    def test_tag_mid_answer_is_left_alone(self):
        # The defect is the model PREFACING its reply; a match further in is
        # content, and silently deleting content is the worse failure.
        text = "As in [Student age range: 5-7] notation, ..."
        assert strip_scaffolding(text) == text

    def test_tag_only_reply_is_kept_rather_than_emptied(self):
        # An empty bubble is a worse failure for a child than a visible tag.
        text = "[Student age range: 5-7]"
        assert strip_scaffolding(text) == text

    def test_non_string_and_empty_pass_through(self):
        assert strip_scaffolding("") == ""
        assert strip_scaffolding(None) is None

    def test_len_helper_agrees_with_strip(self):
        raw = "[Student age range: 11-13]\n\nBody."
        assert raw[leading_scaffolding_len(raw) :] == "Body."


class TestNdjsonStripper:
    def test_tag_split_across_chunks_is_removed(self):
        # Ollama's chunk boundaries are arbitrary, so the tag routinely straddles
        # several NDJSON lines -- the whole reason this works off assembled text.
        chunks = _chunks(["[Student age ", "range: 14-18]\n\n", "Hello there.", " More."])
        assert _extract_text_from_ndjson_chunks(
            _strip_age_scaffolding_from_ndjson_chunks(chunks)
        ) == "Hello there. More."

    def test_stream_without_a_tag_is_byte_identical(self):
        chunks = _chunks(["Just ", "an answer."])
        assert _strip_age_scaffolding_from_ndjson_chunks(chunks) == chunks

    def test_tag_only_stream_is_byte_identical(self):
        chunks = _chunks(["[Student age range: 5-7]"])
        assert _strip_age_scaffolding_from_ndjson_chunks(chunks) == chunks

    def test_unparseable_lines_pass_through(self):
        chunks = [b"not json\n"] + _chunks(["[Student age range: 5-7] Hi."])
        out = _strip_age_scaffolding_from_ndjson_chunks(chunks)
        assert out[0] == b"not json\n"
        assert _extract_text_from_ndjson_chunks(out) == "Hi."


class TestServedThroughTheRoute:
    """The helpers above are unit-level; this proves the CHILD-FACING bytes are
    clean, through the real proxy_chat route.

    Verifying the edit is not verifying the behaviour: the leak reached set 10
    precisely because every layer looked correct in isolation.
    """

    @staticmethod
    def _app():
        from fastapi import FastAPI

        import api.routes.ollama_proxy as proxy_mod
        from core.authentication import AuthSession

        app = FastAPI()
        app.include_router(proxy_mod.router)
        app.dependency_overrides[proxy_mod.get_current_session] = lambda: AuthSession(
            user_id="internal_service",
            role="user",
            session_token="test-token",
            email="internal@snflwr.ai",
        )
        return app

    @staticmethod
    def _safe():
        from safety.pipeline import Category, SafetyResult, Severity

        return SafetyResult(
            is_safe=True, severity=Severity.NONE, category=Category.VALID, reason=""
        )

    def _post(self, monkeypatch, tagged_reply, stream=False):
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx
        from fastapi.testclient import TestClient

        from config import system_config

        pipeline = MagicMock()
        pipeline.check_input.return_value = self._safe()
        pipeline.check_output.return_value = self._safe()

        resp = httpx.Response(
            200,
            json={
                "model": "snflwr.ai",
                "message": {"role": "assistant", "content": tagged_reply},
                "done": True,
            },
        )
        with (
            patch.object(system_config, "GUIDANCE_ENFORCEMENT_ENABLED", False),
            patch(
                "api.routes.ollama_proxy.access._get_user_from_headers",
                return_value=("uid-scaffold", "user"),
            ),
            patch(
                "api.routes.ollama_proxy.profile._get_profile_for_user",
                new=AsyncMock(return_value="profile-scaffold"),
            ),
            patch(
                "api.routes.ollama_proxy.transport._forward_request",
                new=AsyncMock(return_value=resp),
            ),
            patch("safety.pipeline.safety_pipeline", pipeline),
        ):
            return TestClient(self._app()).post(
                "/api/chat",
                json={
                    "model": "snflwr.ai",
                    "stream": stream,
                    "messages": [{"role": "user", "content": "what is osmosis?"}],
                },
                headers={
                    "X-OpenWebUI-User-Id": "uid-scaffold",
                    "X-OpenWebUI-User-Role": "user",
                },
            )

    def test_child_never_receives_the_tag(self, monkeypatch):
        r = self._post(
            monkeypatch,
            "[Student age range: 14-18]\n\nOsmosis moves water across a membrane.",
        )
        assert r.status_code == 200
        served = r.json()["message"]["content"]
        assert served == "Osmosis moves water across a membrane."
        assert "Student age range" not in served

    def test_untagged_reply_is_served_unchanged(self, monkeypatch):
        body = "Osmosis moves water across a membrane."
        r = self._post(monkeypatch, body)
        assert r.json()["message"]["content"] == body
