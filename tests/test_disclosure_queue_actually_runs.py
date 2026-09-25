"""The semantic disclosure pass must actually RUN on a real /api/chat turn.

⚠️ Why this file exists.

`test_disclosure_queue.py` asserted the submit's POSITION in the source:

    assert src.index("_disclosure_queue().submit(") < src.index(...)

That pinned the submit ABOVE the tutor call -- which is also above where
`fwd_headers` was assigned. So every submit raised UnboundLocalError, the
`except Exception` logged it as "non-fatal", and the semantic pass NEVER RAN:
18 turns from deploy until 2026-09-25, every one of them. Child disclosures
fell back to the regex detector alone (9 of 23 on a cold set) while the
classifier behind it was right on 18 of 19.

A test of WHERE the code sits cannot see that the code THROWS. And because the
failure is swallowed by design -- correctly, a child is waiting -- there was no
symptom anywhere except a WARNING line nobody was asserting on.

So this file drives the real endpoint and asserts two things no source-order
test can:

  1. a job is actually submitted;
  2. the "enqueue failed" warning is NOT emitted.

(2) is the one that would have caught the bug. Keep it even if (1) is
refactored away.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from tests.test_ollama_proxy import _chat_body, _make_app, _safe_result

_ENQUEUE_FAILED = "disclosure enqueue failed"


_APP_LOGGER = "snflwr"


def _capture_app_warnings(caplog):
    """Attach caplog's handler to the app logger.

    ⚠️ REQUIRED, and its absence already bit this file once. `utils.logger`
    sets `logging.getLogger("snflwr").propagate = False`, and caplog's handler
    lives on the ROOT logger -- so `caplog.at_level(WARNING)` alone captures
    NOTHING from any `snflwr.*` module. The enqueue-failure assertion below
    passed against the broken code until this was added, which made it
    decorative: a test that cannot see the log line it asserts on.

    `test_the_harness_can_actually_see_an_app_warning` certifies this wiring,
    so it cannot silently regress again.
    """
    logger = logging.getLogger(_APP_LOGGER)
    logger.addHandler(caplog.handler)
    return logger


def _drive_one_turn(caplog):
    """POST one student turn through the real proxy route with the queue on."""
    from fastapi.testclient import TestClient

    client = TestClient(_make_app())
    mock_pipeline = MagicMock()
    mock_pipeline.check_input.return_value = _safe_result()
    mock_pipeline.check_output.return_value = _safe_result()
    queue = MagicMock()

    with (
        patch(
            "api.routes.ollama_proxy.profile._get_profile_for_user",
            new=AsyncMock(return_value="12"),
        ),
        patch(
            "api.routes.ollama_proxy.transport._forward_request",
            new=AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={
                        "model": "test-model",
                        "done": True,
                        "message": {"role": "assistant", "content": "Let's work it out."},
                    },
                )
            ),
        ),
        patch("api.routes.ollama_proxy.chat._disclosure_queue", return_value=queue),
        patch("safety.pipeline.safety_pipeline", mock_pipeline),
    ):
        app_logger = _capture_app_warnings(caplog)
        try:
            with caplog.at_level(logging.WARNING):
                resp = client.post("/api/chat", json=_chat_body())
        finally:
            app_logger.removeHandler(caplog.handler)
    return resp, queue


def test_a_real_turn_submits_a_disclosure_job(caplog):
    resp, queue = _drive_one_turn(caplog)
    assert resp.status_code == 200
    assert queue.submit.call_count == 1, (
        "the semantic disclosure pass did not run on a real turn; "
        f"submit calls={queue.submit.call_count}"
    )


def test_a_real_turn_logs_NO_enqueue_failure(caplog):
    """⭐ The assertion that would have caught the UnboundLocalError.

    The submit is wrapped in `except Exception` because a child is waiting, so
    a broken submit is INVISIBLE except for this warning. Any test that does
    not assert on it cannot distinguish "ran" from "raised and was swallowed".
    """
    _drive_one_turn(caplog)
    offending = [r.getMessage() for r in caplog.records if _ENQUEUE_FAILED in r.getMessage()]
    assert not offending, f"disclosure submit raised and was swallowed: {offending}"


def test_the_generate_callable_is_built_without_raising(caplog):
    """`_make_disclosure_generate(fwd_headers, model)` must be constructible at
    the submit point -- i.e. `fwd_headers` is already assigned there.

    Asserted through the submitted job rather than by reading the source, so it
    stays true under reordering.
    """
    _, queue = _drive_one_turn(caplog)
    assert queue.submit.call_count == 1
    job = queue.submit.call_args.args[0] if queue.submit.call_args.args else None
    kwargs = queue.submit.call_args.kwargs
    generate = getattr(job, "generate", None) or kwargs.get("generate")
    assert callable(generate), f"no callable generate on the submitted job: {job!r}"


def test_the_harness_can_actually_see_an_app_warning(caplog):
    """Certify the instrument, not just the subject.

    `test_a_real_turn_logs_NO_enqueue_failure` is an assertion that something
    is ABSENT. Such an assertion passes trivially when the harness is blind,
    which is exactly what happened here: `snflwr` sets `propagate = False`, so
    caplog saw nothing and the test passed against code that was raising on
    every turn.

    So prove the harness can see a warning from the very logger the subject
    uses. If this fails, every absence-assertion in this file is void.
    """
    logger = logging.getLogger("snflwr.api.routes.ollama_proxy.chat")
    app_logger = _capture_app_warnings(caplog)
    try:
        with caplog.at_level(logging.WARNING):
            logger.warning("%s (canary)", _ENQUEUE_FAILED)
    finally:
        app_logger.removeHandler(caplog.handler)
    seen = [r.getMessage() for r in caplog.records if _ENQUEUE_FAILED in r.getMessage()]
    assert seen, (
        "caplog cannot see warnings from the app logger, so the "
        "no-enqueue-failure assertion in this file proves nothing"
    )
