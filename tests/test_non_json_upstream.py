"""A non-JSON upstream body must not crash the child's turn.

`upstream.json()` is wrapped and sets `upstream_json = None` on a decode
failure -- a DELIBERATE fallback. But `assistant_text` was assigned only inside
the `isinstance(upstream_json, dict)` branch and read unconditionally by the
history-ledger block, so the fallback was followed by an `UnboundLocalError`:
the child got a 500 instead of the graceful message, and the turn was lost.

Reachable whenever upstream returns a non-JSON body -- a proxy error page, a
truncated response, an OOM message, all of which this box produces under GPU
contention.

⚠️ This is the SECOND instance of the class on this one function, after
`fwd_headers` (which had silenced the entire semantic disclosure pass). Both
were invisible to tests and both were caught statically by pyright. The tests
here exist because the static check is advisory; these make it enforced.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import api.routes.ollama_proxy.chat as chat_mod
from tests.test_ollama_proxy import _chat_body, _make_app, _safe_result


def _drive(upstream_response):
    from fastapi.testclient import TestClient

    client = TestClient(_make_app(), raise_server_exceptions=False)
    pipeline = MagicMock()
    pipeline.check_input.return_value = _safe_result()
    pipeline.check_output.return_value = _safe_result()
    # ⚠️ Patch ONLY `record_turn`, not the whole module. `history_ledger` is
    # also used EARLIER in the same function (dropping unrecognised history
    # messages), and a bare MagicMock there returns a MagicMock where an int is
    # expected -- which 500s the turn and makes every assertion belows the
    # harness instead of the code.
    with (
        patch(
            "api.routes.ollama_proxy.profile._get_profile_for_user",
            new=AsyncMock(return_value="12"),
        ),
        patch(
            "api.routes.ollama_proxy.transport._forward_request",
            new=AsyncMock(return_value=upstream_response),
        ),
        patch.object(chat_mod.history_ledger, "record_turn") as record_turn,
        patch("safety.pipeline.safety_pipeline", pipeline),
    ):
        resp = client.post("/api/chat", json=_chat_body())
    return resp, record_turn


def test_a_non_json_upstream_body_does_not_crash_the_turn():
    """The assertion that fails on the bug: an UnboundLocalError surfaces as a
    500, so status alone is the check."""
    resp, _ = _drive(httpx.Response(200, text="<html>502 Bad Gateway</html>"))
    assert resp.status_code != 500, (
        "a non-JSON upstream body crashed the turn; the deliberate "
        "`upstream_json = None` fallback is followed by an unbound read"
    )


def test_a_truncated_json_body_does_not_crash_the_turn():
    resp, _ = _drive(httpx.Response(200, text='{"message": {"content": "hal'))
    assert resp.status_code != 500


def test_no_empty_turn_is_written_to_the_replay_history():
    """⭐ The judgment half, kept separate from the crash fix.

    With no decodable reply there is no exchange to remember. Recording one
    would put a BLANK assistant turn into the history the client replays on the
    next turn -- a silent corruption that would outlive the request.
    """
    _, record_turn = _drive(httpx.Response(200, text="<html>502 Bad Gateway</html>"))
    assert record_turn.call_count == 0, (
        "recorded a turn with no decodable assistant reply: "
        f"{record_turn.call_args_list}"
    )


def test_a_normal_json_body_still_records_its_turn():
    """The guard above must not suppress ordinary recording."""
    _, record_turn = _drive(
        httpx.Response(
            200,
            json={
                "model": "test-model",
                "done": True,
                "message": {"role": "assistant", "content": "The water cycle is..."},
            },
        )
    )
    assert record_turn.call_count == 1
