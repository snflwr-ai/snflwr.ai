"""Chat route: POST /api/chat with the full child-safety pipeline."""

from __future__ import annotations

import asyncio
import json as _json
import os
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from api.middleware.auth import get_current_session, is_genuine_admin
from api.routes.ollama_proxy import (
    access,
    admission,
    blocks,
    break_reminder,
    guards,
    history_ledger,
    profile,
    transport,
)
from config import safety_config, system_config
from core import serving_plan, topic_gate
from core.authentication import AuthSession
from core.coppa_gate import coppa_consent_block_reason
from core.profile_gate import no_profile_block_reason
from safety import disclosure_response
from utils import observability
from utils.logger import get_logger, sanitize_log_value

logger = get_logger(__name__)

router = APIRouter()


def _gate_block(model: str, message: str, *, stream: bool) -> Response:
    """Return an early gate/block response in the format the client requested.

    Open WebUI >=0.10 requests ``/api/chat`` with ``stream=True`` and does not
    render a non-streaming ``JSONResponse`` (single object) — the child sees a
    blank bubble instead of the safe-redirect / 988 text. When a stream was
    requested, deliver the block as a single NDJSON chunk; otherwise a JSON
    object. (The later streaming paths already emit NDJSON; this covers the
    early gates: rate-limit, circuit, license, no-profile, COPPA, input-safety.)
    """
    if stream:
        return Response(
            content=blocks._ollama_block_stream_bytes(model, message),
            media_type="application/x-ndjson",
        )
    return JSONResponse(content=blocks._ollama_block_response(model, message))


# Load must never reach a child as a worse answer. Measured 2026-09-17: at 20
# concurrent students on one card, 40 of 60 replies became the canned withholding
# fallback because the reveal confirm spent its 30 s budget QUEUED. The tutor was
# fine; the queue was not. So over-capacity turns say so, plainly, and are not
# recorded in the history ledger.
#
# The three canned messages MOVED to core.proxy_messages, for the same reason
# the classifier system override moved to core.pedagogy (see the note below):
# scripts/latency_bar.py must know every canned reply, and importing an API
# ROUTE from scripts/ makes mypy resolve scripts/ under two module names. They
# are re-exported here so every existing import keeps working.
from core.proxy_messages import BUSY_MESSAGE as _BUSY_MESSAGE
from core.proxy_messages import TIMEOUT_MESSAGE as _TIMEOUT_MESSAGE
from core.proxy_messages import UNSUPPORTED_MESSAGE as _UNSUPPORTED_MESSAGE

__all__ = ["_BUSY_MESSAGE", "_TIMEOUT_MESSAGE", "_UNSUPPORTED_MESSAGE"]


# How long the route will wait for a semantic verdict it has already asked for.
# Small on purpose: the verdict is normally ALREADY THERE (submitted before the
# tutor call, ~6s, against a 13-32s turn), so this is a backstop for a degraded
# queue, not a budget. A miss costs the override, never the reply.
_DISCLOSURE_VERDICT_WAIT_S = float(os.getenv("DISCLOSURE_VERDICT_WAIT_S", "2.0"))


async def _await_disclosure_verdict(fut) -> "str | None":
    """The semantic kind for this turn, or None to leave the reply alone.

    Total: every failure mode -- no future, timeout, cancellation, a raising
    future -- returns None, which means "no override" and so reproduces today's
    behaviour. The failure direction has to be "no improvement", never a
    changed or blank reply.

    `asyncio.wait` rather than `wait_for`: `wait_for` CANCELS the awaitable on
    timeout, which would break the worker's `set_result` on a future it still
    owns.
    """
    if fut is None:
        return None
    try:
        done, _pending = await asyncio.wait({fut}, timeout=_DISCLOSURE_VERDICT_WAIT_S)
        if fut not in done or fut.cancelled():
            return None
        return fut.result()
    except Exception:  # noqa: BLE001 - never raise into a child's turn
        return None


async def _pedagogy_reissue(
    original_body_bytes: bytes,
    fwd_headers: dict,
    assistant_text: str,
    nudge: str,
    model: str,
) -> str:
    """Re-issue the chat with the assistant's response + nudge appended.

    Called by the pedagogy enforcer's ``_regenerate`` closure. Fail-open:
    callers (inside ``enforce_guidance``) catch all exceptions from this helper.
    """
    body = _json.loads(original_body_bytes)
    messages = list(body.get("messages", []))
    # Append the revealing assistant turn so the model has full context,
    # then the nudge so it knows to guide rather than give the answer.
    messages.append({"role": "assistant", "content": assistant_text})
    messages.append({"role": "user", "content": nudge})
    payload_dict: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
    }
    if isinstance(body.get("options"), dict):
        payload_dict["options"] = body["options"]
    payload = _json.dumps(payload_dict).encode()
    upstream = await transport._forward_request(
        "POST", "/api/chat", content=payload, headers=fwd_headers
    )
    return upstream.json().get("message", {}).get("content", "")


# The classifier system override lives in core.pedagogy with the other
# classifier settings. It moved there because the deploy self-test must send the
# SAME override production sends, and importing an API ROUTE from scripts/ made
# mypy resolve scripts/ under two module names. A classifier's system prompt is
# a pedagogy concern, not a routing one.
from core.pedagogy import CLASSIFIER_SYSTEM as _CLASSIFIER_SYSTEM  # noqa: E402

# How long to hold a pedagogy classifier in memory between turns. Long enough
# that ordinary gaps in child traffic do not cause a cold load; not "forever",
# which is how a co-tenant model on this box ended up pinning 17 GB of card
# indefinitely.
_CLASSIFIER_KEEP_ALIVE = "30m"


# ---------------------------------------------------------------------------
# Semantic disclosure pass, OFF the child's critical path.
#
# The regex detector reached 9 of 23 disclosures on a cold sealed set while the
# classifier behind it was right on 18 of the 19 turns it was ever shown. The
# gate was the ceiling, it existed to protect a latency budget nobody measured,
# and this work never had to be on the critical path: it only writes an
# incident row -- `disclosure_detector`'s docstring opens with "This detector
# NEVER blocks".
# ---------------------------------------------------------------------------

_DISCLOSURE_QUEUE = None


def _disclosure_queue():
    """Lazily build the per-process queue. Imports stay off the load path."""
    global _DISCLOSURE_QUEUE
    if _DISCLOSURE_QUEUE is None:
        from safety.disclosure_queue import DisclosureQueue

        _DISCLOSURE_QUEUE = DisclosureQueue(recorder=blocks._record_disclosure_incident)
    return _DISCLOSURE_QUEUE


def _make_disclosure_generate(fwd_headers: dict, tutor_model: str):
    """One-shot call for the disclosure classifier.

    Reuses `_pedagogy_oneshot`, so this call gets the same production shape as
    the reveal confirm: `think: False` (without it gemma4 returns an EMPTY
    response with done_reason=length), `keep_alive` to hold the CPU-pinned
    classifier resident, and `tutor_model` so the CPU pin is applied -- the
    disclosure model is NOT the tutor, so it stays off the contended card.

    `system` is replaced because the model must not inherit a tutor persona; an
    unreadable verdict is what made the reveal confirm fail open.
    """
    from safety.disclosure_semantic import CLASSIFIER_SYSTEM, DISCLOSURE_MODEL

    async def _generate(prompt: str) -> str:
        return await _pedagogy_oneshot(
            prompt,
            DISCLOSURE_MODEL,
            fwd_headers,
            system=CLASSIFIER_SYSTEM,
            tutor_model=tutor_model,
        )

    return _generate


def _make_adjudicator_generate(fwd_headers: dict, tutor_model: str):
    """One-shot call for the speech-act adjudicator.

    Reuses `_pedagogy_oneshot`, so it gets the production shape the hard way
    round: `think: False` at the TOP level (without it gemma4 burns its whole
    budget on thinking tokens and returns an empty response with
    `done_reason=length`), `keep_alive` to hold the CPU-pinned classifier
    resident, and `tutor_model` so the CPU pin applies -- the adjudicator is
    NOT the tutor and must stay off the contended card.

    `system` is replaced so the classifier cannot inherit a tutor persona whose
    brevity rules truncate JSON verdicts to `{"`.
    """
    from safety.speech_act_adjudicator import ADJUDICATOR_MODEL, ADJUDICATOR_SYSTEM

    async def _generate(prompt: str) -> str:
        return await _pedagogy_oneshot(
            prompt,
            ADJUDICATOR_MODEL,
            fwd_headers,
            system=ADJUDICATOR_SYSTEM,
            tutor_model=tutor_model,
        )

    return _generate


async def _pedagogy_oneshot(
    prompt: str,
    model: str,
    fwd_headers: dict,
    *,
    system: str | None = None,
    tutor_model: str | None = None,
) -> str:
    """Single-turn Ollama call for the pedagogy confirm stage.

    Called by the pedagogy enforcer's ``_confirm_generate`` closure. Errors
    PROPAGATE: ``confirm_reveal`` used to swallow them into a "no reveal"
    verdict, which made the enforcer's fail-closed branch dead code for the
    failure that actually happens on this box (the arbiter handing the card to
    the co-tenant mid-turn raises an error, not a timeout). The enforcer now
    sees the exception and withholds.

    ``system`` replaces the model's own system prompt for this call. Pass it
    whenever the model being called is the TUTOR, or the check inherits the
    persona and stops working -- silently, because an unreadable verdict used to
    fail open.

    ``tutor_model`` decides the CPU pin. Pass it whenever it is known: pinning
    the resident tutor to CPU keeps the verdict and throws away the only reason
    to use it.
    """
    # Imported here, like enforce_guidance below, to keep core.pedagogy off this
    # module's import path at load time.
    from core.pedagogy import classifier_options

    payload = _json.dumps(
        {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            # keep_alive: hold the CPU-pinned classifier RESIDENT between turns.
            #
            # The input gate runs on EVERY child turn and its cold load was
            # measured at 29.9s against a 6s timeout, while steady state is
            # p50 0.43s. One idle period therefore times the gate out, and the
            # enforcer silently falls back to the REGEX -- which is 25 points
            # weaker on framed demands (dodge catch 90% -> 65% on a blind
            # holdout, 2026-09-21). At the moment this was added the gate model
            # was NOT resident, so the next child's turn would have taken the
            # weak path.
            #
            # Cheap because this classifier is num_gpu 0: it costs RAM (53 GiB
            # free on this box), not the contended 23 GiB card, so it cannot
            # evict the tutor. A GPU-resident checker would be a different and
            # much worse trade.
            "keep_alive": _CLASSIFIER_KEEP_ALIVE,
            # temperature 0: this is a safety CLASSIFIER, not a generator, and it
            # was the only one in the codebase running at the model default --
            # core/topic_gate.py and safety/pipeline/classifier.py both pin their
            # options. Measured effect on the 64-case set was nil (identical
            # verdicts over 6 runs), but one verdict did flip between runs
            # earlier in the same session, and a reveal check that can flip is a
            # reveal check that can flip the wrong way.
            #
            # num_gpu 0: CPU-PINNED ON PURPOSE, same reasoning that pins
            # llama-guard3-cpu. The placement policy prefers the GPU for any
            # model not already resident, so on a card that holds one big model
            # this classifier competed with the co-tenant brain -- and lost.
            # Measured 2026-09-13 with IronClaw's 17.8 GB model resident:
            #
            #     GPU-preferring   verdict WRONG (load failed, 500)   29.3 s
            #     num_gpu 0        verdict CORRECT                    13.6 s cold
            #                                                          0.8 s warm
            #
            # A classifier trades a little latency for never being evicted. Here
            # it does not even cost latency: 0.8 s warm beats the GPU path.
            "options": classifier_options(model, tutor_model),
        }
    ).encode()
    upstream = await transport._forward_request(
        "POST", "/api/chat", content=payload, headers=fwd_headers
    )
    return upstream.json().get("message", {}).get("content", "")


@router.post("/chat", dependencies=[Depends(admission.inference_slot)])
async def proxy_chat(
    request: Request,
    session: AuthSession = Depends(get_current_session),
) -> Response:
    """POST /api/chat — run safety pipeline for students, pass-through for admins.

    Supports both streaming (``stream=True``) and non-streaming responses.
    """
    try:
        body_bytes = await request.body()
        body = _json.loads(body_bytes)
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    model: str = body.get("model", "")
    messages: list = body.get("messages", [])
    stream: bool = body.get("stream", False)

    # Resolve IDENTITY (which user) — the X-OpenWebUI-User-* headers carry the
    # forwarded user only when the caller is Open WebUI (the internal key). This
    # is used purely to look up the right child profile below.
    if session.user_id == "internal_service":
        user_id, _ = access._get_user_from_headers(request)
    else:
        user_id = session.user_id

    # Safety-pipeline bypass is an AUTHORITY decision, never an identity one: it
    # requires a genuine admin *session*. The internal service key is a relay,
    # not an admin, so a forwarded X-OpenWebUI-User-Role: admin can NOT skip
    # child safety (parity with chat.py). A leaked key therefore can't bypass.
    if is_genuine_admin(session):
        logger.debug("Admin user %s — forwarding /api/chat directly", user_id)
        # Suppress extended-thinking so OWU doesn't render a raw <thinking> block
        body["think"] = False
        body_bytes = _json.dumps(body).encode()
        fwd_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }
        if stream:
            return await transport._stream_chat_from_ollama(body_bytes, fwd_headers)
        try:
            upstream = await transport._forward_request(
                "POST",
                "/api/chat",
                content=body_bytes,
                headers=fwd_headers,
            )
        except httpx.ConnectError:
            return JSONResponse(
                status_code=503,
                content={"detail": "Ollama backend unreachable"},
            )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    # ---- Pin the model (students only; admins returned above) ----
    # A student may only ever run the tutor model. The OWUI dropdown already
    # lists only snflwr.ai (the proxy filters /api/tags), but /api/chat otherwise
    # trusts the request body — a crafted call naming the raw backbone would run
    # it unpinned, losing the tutor Modelfile's system/safety prompt (#190). The
    # safety pipeline still runs regardless, so this is defense-in-depth. Coerce
    # any out-of-set model to the default tutor before any downstream use.
    # ---- Suppress extended thinking on the STUDENT path --------------------
    # gemma4 is a reasoning model. With thinking enabled it spends its budget on
    # `message.thinking` and returns a much shorter `message.content` -- and
    # sometimes NO content at all. The proxy strips `thinking` before serving
    # (it is unvetted chain-of-thought that must never reach a child), so an
    # all-thinking reply reaches the child as an EMPTY BUBBLE.
    #
    # Measured 2026-09-13 against the deployed tutor, 4 ordinary prompts:
    #   "What is 2 plus 2?"                    -> 233 chars content
    #   "3x + 7 = 22, what is x?"              -> 436 chars content
    #   "summary of chapter 1 of Gatsby"       ->   0 chars content, 2247 thinking
    #   "Why is the sky blue?"                 ->   0 chars content, 2293 thinking
    # Half of them answered a child with nothing.
    #
    # The admin bypass above has set `think: False` since it was written, for the
    # same rendering reason. The student path never did -- so the one audience
    # that cannot work around a blank answer was the only one exposed to it, and
    # every tutoring measurement taken with `think: False` described the ADMIN
    # generation shape rather than the child's.
    if body.get("think") is not False:
        body["think"] = False
        body_bytes = _json.dumps(body).encode()

    if model not in access._student_visible_models():
        pinned = system_config.OLLAMA_DEFAULT_MODEL or "snflwr.ai"
        if model:
            logger.info(
                "Pinned student model %r -> %r", sanitize_log_value(model), pinned
            )
        model = pinned
        body["model"] = pinned
        body_bytes = _json.dumps(body).encode()

    # ---- Strip client-supplied system messages (students only) ----
    # Ollama treats a `system` message as a REPLACEMENT for the tutor Modelfile's
    # system/safety prompt, so a student-injected system turn could discard the
    # guardrail. The persona + safety instructions live in the Modelfile and are
    # never set from the request body; drop any system message before forwarding
    # (the native chat route never forwards a student system message either).
    if any(isinstance(m, dict) and m.get("role") == "system" for m in messages):
        messages = [
            m
            for m in messages
            if not (isinstance(m, dict) and m.get("role") == "system")
        ]
        body["messages"] = messages
        body_bytes = _json.dumps(body).encode()

    # ---- Admission control (students only; admins returned above) ----
    _reason = (
        guards.rate_limit_block_reason(user_id)
        or guards.circuit_block_reason(user_id)
        or guards.license_block_reason(user_id)
    )
    if _reason:
        return _gate_block(model, _reason, stream=stream)

    # ---- Quality floor (students only) ----------------------------------
    # Scaling DOWN with the hardware must not mean tutoring worse. Sealed run
    # 2026-09-17: only the 31b backbone met the tutoring bars; e4b and 12b
    # measured 4-13 wrong replies per 121 against a bar of 6 (and the one shape
    # that fixed correctness stonewalled 38 times). A box that cannot run a
    # certified backbone says so instead of serving an uncertified one.
    _plan = serving_plan.get_plan()
    if not _plan.tutoring_enabled:
        logger.warning("Tutoring disabled by serving plan: %s", _plan.reason)
        return _gate_block(model, _UNSUPPORTED_MESSAGE, stream=stream)

    # Student path — run safety pipeline
    profile_id = await profile._get_profile_for_user(user_id)

    no_profile_msg = no_profile_block_reason(profile_id)
    if no_profile_msg:
        logger.warning(
            "Blocked student chat: no learning profile for user %s",
            observability.hash_profile(user_id) if user_id else "unknown",
        )
        return _gate_block(model, no_profile_msg, stream=stream)

    # Resolve age from profile (best-effort; None is acceptable)
    age: Optional[int] = profile._resolve_age(profile_id)

    # ---- Drop history this proxy did not serve recently (students only) ----
    # Open WebUI stores chats in its own DB and resends a reopened conversation's
    # FULL message array, so the turn cap below — which bounds LENGTH — let
    # yesterday's messages back into the prompt. S9051B §1801 makes using
    # personal/wellbeing content "acquired ... more than twelve hours previously
    # or in any previous user session" an unsafe AI companion feature, and the
    # native route already avoids it by scoping history to a session row. The
    # ledger is the proxy's equivalent: it keeps only the trailing run of
    # messages it actually served for this child inside the TTL window, plus the
    # current turn. Fail-closed — any ledger error yields the current turn alone.
    filtered = history_ledger.filter_history(profile_id, messages)
    if len(filtered) != len(messages):
        logger.info(
            "Dropped %d unrecognized history message(s) before forwarding "
            "(cross-session guard)",
            len(messages) - len(filtered),
        )
        messages = filtered
        body["messages"] = messages
        body_bytes = _json.dumps(body).encode()

    # ---- Enforce the per-grade conversation-turn cap (students only) ----
    # FILTER_LEVELS defines max_conversation_turns per grade band but nothing
    # consumed it, so a client could forward unbounded chat history and drive
    # GPU cost / context growth up every turn. Keep only the most recent turns
    # (each turn ≈ a user+assistant pair) so current tutoring context is preserved
    # while old history is dropped before forwarding. The dropped turns also fall
    # out of the safety scan below — safe, since the model never sees them either.
    max_turns = safety_config.max_conversation_turns_for_age(age)
    max_messages = max_turns * 2
    if len(messages) > max_messages:
        dropped = len(messages) - max_messages
        messages = messages[-max_messages:]
        body["messages"] = messages
        body_bytes = _json.dumps(body).encode()
        logger.info(
            "Clipped student conversation to last %d turns (dropped %d older messages)",
            max_turns,
            dropped,
        )

    # Observation-only tracing context. Metadata only — never any chat content.
    # Fail-safe: a tracing error must never change the response.
    _t0 = time.perf_counter()
    _trace: Dict[str, Any] = {
        "model": model,
        "age_band": observability.age_band(age),
        "profile_hash": observability.hash_profile(profile_id),
        "blocked": True,
        "safety": {},
        "latency_ms": {},
        "tokens": None,
    }

    def _emit_trace():
        _trace["latency_ms"]["total"] = round((time.perf_counter() - _t0) * 1000, 2)
        try:
            observability.trace_chat_turn(**_trace)
        except Exception:  # belt-and-suspenders; wrapper is already fail-safe
            pass

    def _usage_from(payload):
        if not isinstance(payload, dict):
            return None
        pin = payload.get("prompt_eval_count")
        out = payload.get("eval_count")
        if pin is None and out is None:
            return None
        return {"input": pin or 0, "output": out or 0}

    # COPPA gate — an under-13 profile may only tutor once per-child parental
    # consent has been verified. Shared with the native chat route via
    # core.coppa_gate so the two model-reaching paths can't drift (finding C1);
    # fail-closed semantics live in the helper.
    coppa_msg = coppa_consent_block_reason(profile_id, fallback_age=age)
    if coppa_msg is not None:
        _trace["safety"] = {"blocked_layer": "coppa"}
        _emit_trace()
        return _gate_block(model, coppa_msg, stream=stream)

    # user_question is the CURRENT (last) student turn — passed as `context` to
    # check_output so the answer inherits the question's educational context (e.g. a
    # biology question about "drugs"). Captured separately because `text` is later
    # shadowed inside the streaming _vet() closure.
    user_question = blocks._extract_last_user_message(messages)
    # Input safety scans ALL student-authored turns, not only the last: a jailbreak
    # placed in an earlier user turn would otherwise slip past check_input.
    text = blocks._all_user_messages_text(messages)

    try:
        from safety.pipeline import safety_pipeline

        result = safety_pipeline.check_input(text=text, age=age, profile_id=profile_id)
    except Exception as exc:
        logger.error("Safety pipeline raised unexpectedly: %s", exc, exc_info=True)
        # Fail closed — block the message
        block_msg = "I'm unable to process that request right now."
        _trace["safety"] = {"blocked_layer": "input"}
        _emit_trace()
        return _gate_block(model, block_msg, stream=stream)

    # ---- Risk DISCLOSURE: escalate without blocking -------------------------
    # `is_safe` was one boolean doing two jobs -- "replace this reply" and "tell
    # an adult". A harmful content REQUEST needs both. A child DISCLOSING risk
    # needs only the second: measured, the tutor already redirects 15 of 17 such
    # turns to a trusted adult, so replacing its reply with canned text would make
    # the child's experience worse. Because the two were coupled, only 1 of 12
    # disclosures ever reached escalation.py, and nothing else shows conversation
    # content to a parent.
    #
    # This records the incident and lets the turn proceed untouched.
    # ⚠️ This runs REGARDLESS of `result.is_safe`, and that is the whole point.
    # It used to sit inside `if result.is_safe`, which made the two nets mutually
    # exclusive: a disclosure the harm classifier happened to block never reached
    # this detector at all, so it was recorded as `exploitation` -- the child
    # REQUESTING harmful content -- instead of the child DISCLOSING it. A parent
    # alert that says the wrong thing about which of those happened is worse than
    # a late one.
    #
    # Measured 2026-09-24 on a held-out sealed set of 22 disclosures: the harm
    # classifier blocks 2 of them. Small, but those 2 were exactly the cases
    # where a parent was told the least accurate story.
    # ⚠️ Built HERE, above the disclosure submit, not at the "forward to
    # Ollama" comment below where it used to live.
    #
    # `_make_disclosure_generate(fwd_headers, ...)` is called inside the submit
    # block, which sits ABOVE that point. So every submit raised
    # UnboundLocalError, the broad `except` logged it "non-fatal", and the
    # SEMANTIC DISCLOSURE PASS NEVER RAN -- 18 turns, every one, from deploy
    # until 2026-09-25. Child disclosures fell back to the regex detector alone
    # (9 of 23 on a cold set) while the classifier behind it was right on 18 of
    # 19. The feature was deployed and absent.
    #
    # HOISTED rather than made lazy on purpose: a lazy closure would still read
    # a variable that is unassigned on some paths and merely move the failure
    # into the worker. This construction depends only on `request.headers`, so
    # moving it up removes the failure CLASS instead of relocating it.
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }

    # The route awaits THIS turn's semantic verdict before serving, so the
    # response router can act on it. Fire-and-forget (verdict=None) remains the
    # behaviour whenever there is no running loop.
    _disclosure_verdict = None
    _disclosure = None
    # Whether the disclosure row below already alerted a parent. Only the MAJOR
    # kinds do, so a bullying or disordered-eating disclosure must NOT suppress
    # the safety alert further down -- that would trade two alerts for none.
    _disclosure_alerted = False
    try:
        from safety.disclosure_detector import detect_disclosure

        _disclosure = detect_disclosure(text)
        if _disclosure is not None:
            _disclosure_alerted = blocks._record_disclosure_incident(
                profile_id,
                _disclosure.kind,
                _disclosure.matched,
                text,
                blocked=not result.is_safe,
                # The block's own severity, so the disclosure row can carry it.
                # A blocked crisis message is CRITICAL on the safety row and
                # only MAJOR on the disclosure row, and major is an ORDINARY
                # parent email while critical is an URGENT one -- so without
                # this the suppression below downgrades the alert.
                # The severity this block will ESCALATE at, not the raw one: a
                # classifier-caught crisis is MAJOR on paper and CRITICAL in
                # fact, so the disclosure row must inherit the real urgency.
                block_severity=(
                    None
                    if result.is_safe
                    else blocks.crisis_escalation_severity(
                        result, text, category_describes_child=True
                    )
                ),
            )
            _trace["disclosure"] = {
                "kind": _disclosure.kind,
                "escalated": True,
                "blocked": not result.is_safe,
            }
    except Exception as exc:  # never let escalation break a child's turn
        logger.warning("disclosure detection failed (continuing): %s", exc)

    # ---- Semantic disclosure pass: enqueue, never await --------------------
    #
    # Submitted here, BEFORE the tutor call, so the CPU classify overlaps GPU
    # generation instead of following it. It is non-blocking either way; this
    # only makes the row land sooner.
    #
    # ⚠️ Submitted even when the turn was BLOCKED -- #325's lesson is that a
    # blocked disclosure still needs typing, and it is the case where the child
    # was both reaching out AND refused.
    #
    # ⚠️ Skipped only when the inline row ALREADY ALERTED (the observed flag,
    # not the kind). An upgrade cannot add anything a parent has already been
    # told, and a second alerting row would be the #330 double-alert.
    if not _disclosure_alerted:
        try:
            from safety.disclosure_queue import _Job as _DisclosureJob

            _disclosure_verdict = _disclosure_queue().new_verdict_future()
            _disclosure_queue().submit(
                _DisclosureJob(
                    profile_id=profile_id,
                    verdict=_disclosure_verdict,
                    # ⚠️ The CHILD's turns only. #331: the crisis and severity
                    # decisions must never read the model's output, and the
                    # worker is handed nothing else.
                    child_text=text,
                    fallback_kind=(_disclosure.kind if _disclosure else None),
                    fallback_alerted=_disclosure_alerted,
                    blocked=not result.is_safe,
                    block_severity=(
                        None
                        if result.is_safe
                        else blocks.crisis_escalation_severity(
                            result, text, category_describes_child=True
                        )
                    ),
                    generate=_make_disclosure_generate(fwd_headers, model),
                )
            )
        except Exception as exc:  # noqa: BLE001 - a child is waiting
            logger.warning("disclosure enqueue failed (non-fatal): %s", exc)

    # ---- Speech-act adjudicator, behind a flag -----------------------------
    #
    # main's DEROGATORY word list is the TRIGGER, not the decision. It flags
    # ~90% of everything containing these words -- 85.4% of insults and 90.2%
    # of benign turns, a 0.95x discrimination ratio, so as a filter it carries
    # almost no information about intent. That indiscriminacy is exactly what
    # makes it a good high-recall PREFILTER.
    #
    # Measured on cold set 6 (269 items, main_flags pinned to e3f457e):
    #   insults blocked      46/48   (false-release 4.2%, bar <=5%)
    #   reports released     44/44   (main: 0/44, V1: 32/44)
    #   denials released     40/42
    #   asking_about         47/47
    #   curriculum           35/35
    #   K-2 insults/reports    8/8   (V1: 1/8 and 6/8)
    #
    # ⚠️ It can only RELEASE, never block something the trigger let through --
    # so it cannot introduce a false positive. It CAN release a real insult the
    # trigger caught, which is a new false negative:
    #     recall = trigger flag rate x (1 - false-release rate)
    # That is the term with the tight bound, and it is why the flag defaults
    # off until the wired latency measurement lands.
    #
    # ⭐ Fail-closed here IS today's behaviour: any error keeps the block, which
    # is what the word list does now. The failure mode is "no improvement",
    # never "new harm" -- the inverse of the reveal confirm, which swallowed
    # errors into a negative verdict and served the leak.
    #
    # ⚠️ INPUT ONLY. The taxonomy asks what the STUDENT is doing with the word;
    # a tutor explaining "pathetic fallacy" is an OUTPUT block and is out of
    # scope for this classifier.
    if (
        not result.is_safe
        and safety_config.SPEECH_ACT_ADJUDICATOR_ENABLED
        and getattr(result.category, "value", None) == "derogatory"
    ):
        try:
            # ⚠️ The TYPES, not attributes of the instance. `safety_pipeline`
            # is a SafetyPipeline INSTANCE with no `SafetyResult` attribute, so
            # `safety_pipeline.SafetyResult(...)` raised AttributeError on
            # EVERY release in production -- swallowed by the except below,
            # logged "unavailable", and the adjudicator never released a single
            # turn. Fourth instance of that shape on this feature.
            from safety.pipeline import Category, Severity, pattern_stage_category
            from safety.pipeline import SafetyResult as _SafetyResult
            from safety.speech_act_adjudicator import (
                ADJUDICATOR_MODEL,
            )
            from safety.speech_act_adjudicator import (
                should_block as _adjudicate,
            )

            _released_by_history = pattern_stage_category(user_question) != "derogatory"
            _adj_gen = _make_adjudicator_generate(fwd_headers, model)
            # ⚠️ NOT wrapped in _stage("adjudicate"): that helper lives on the
            # unmerged stage-timers branch, and referencing it here would raise
            # NameError -> caught by the except below -> fail-closed -> the
            # adjudicator would silently never run. Exactly how the first
            # version of this wiring failed (a missing module did the same).
            # Add the timer once obs/stage-timers merges.
            # ⚠️ `user_question` (the CURRENT turn), NOT `text` (every student
            # turn CONCATENATED, which is what the word-list trigger sees).
            #
            # The blob is what tripped the trigger, but judging it would let a
            # single past insult block EVERY later turn: for a child who once
            # wrote "you are such a loser" and now asks "what are fatty acids",
            # the concatenation is "you are such a loser\nwhat are fatty acids"
            # -- which any honest classifier calls insulting. That earlier turn
            # was already blocked when it was sent; re-blocking the innocent one
            # is punishment for history.
            #
            # It also matches what cold set 6 validated: single messages.
            # Multi-turn input is unmeasured either way, and that is in the PR.
            # ⭐ SHORT-CIRCUIT, which prevents a latency CASCADE.
            #
            # A RELEASED turn is SERVED, so it IS recorded in the history
            # ledger -- unlike a blocked turn, which is not, and which is why
            # today's word list does not keep re-firing after a refusal. So
            # once the adjudicator releases "they call me a freak", that text
            # stays in history, the concatenation keeps tripping the word list,
            # and EVERY later turn of the session would pay another ~6s
            # classifier call to be released again.
            #
            # If the CURRENT turn does not trip the pattern stage by itself,
            # the flag came only from already-served history, so release with
            # NO model call. Deterministic, sub-millisecond, and it caps the
            # cost of a release at one call rather than one per remaining turn.
            # Found by prime-69.
            _keep = (
                False
                if _released_by_history
                else await _adjudicate(user_question, _adj_gen)
            )
            if _released_by_history:
                logger.info(
                    "adjudicator SKIPPED: the flag came from already-served "
                    "history, not the current turn"
                )
                _trace["adjudicator"] = "skipped_history"
                result = _SafetyResult(
                    is_safe=True,
                    severity=Severity.NONE,
                    category=Category.VALID,
                    reason="flagged only by already-served history",
                )
            elif not _keep:
                logger.warning(
                    "adjudicator RELEASED a DEROGATORY-flagged turn (model=%s)",
                    ADJUDICATOR_MODEL,
                )
                _trace["adjudicator"] = "released"
                result = _SafetyResult(
                    is_safe=True,
                    severity=Severity.NONE,
                    category=Category.VALID,
                    reason="released by the speech-act adjudicator",
                )
            else:
                _trace["adjudicator"] = "kept"
        except Exception as exc:  # noqa: BLE001 - fail CLOSED: keep the block
            logger.warning(
                "adjudicator unavailable (%s); KEEPING the block, i.e. today's "
                "behaviour",
                exc,
            )
            _trace["adjudicator"] = "unavailable"

    if not result.is_safe:
        block_message = (
            result.modified_content
            # `text` is passed so a crisis referral does not depend on the
            # classifier having chosen SELF_HARM out of nine categories: an
            # overdose-method question blocked as VIOLENCE used to get the
            # schoolwork redirect and no 988 line.
            or safety_pipeline.get_safe_response(result, text)
            or "I'm not able to help with that right now. Let's try something else!"
        )
        logger.info(
            "Safety blocked message for profile %s (category=%s)",
            profile_id,
            result.category,
        )
        # ⚠️ One alert per turn. A blocked disclosure writes BOTH rows, but only
        # one may reach the parent, and it must be the disclosure row: the safety
        # row's incident_type is the classifier's category, which for a grooming
        # report reads `exploitation` -- the child REQUESTING harmful content,
        # the opposite of what happened. Two emails for one message, one of them
        # framing the child as the offender, would partly undo the fix that
        # wrote the disclosure row in the first place.
        blocks._record_safety_incident(
            profile_id,
            result,
            text,
            send_alert=not _disclosure_alerted,
            # INPUT block: `text` is the child's own turns, so the classifier's
            # category describes the CHILD and a self_harm label is a crisis.
            child_text=text,
            category_describes_child=True,
        )
        _trace["safety"] = {
            "category": str(result.category),
            "severity": str(result.severity),
            "blocked_layer": "input",
        }
        _emit_trace()
        return _gate_block(model, block_message, stream=stream)

    # ---- Structural topic gate (S9051B "unable to respond outside the purpose")
    # DELIBERATELY AFTER check_input, and that order is a child-safety property,
    # not a preference. A crisis message ("I want to hurt myself") is off-topic
    # to any academic classifier — if this gate ran first, a distressed child
    # would get a generic topic refusal and the crisis path (988 response,
    # incident record, parent alert) would never execute, silently. Safety
    # classifies first; only a message the safety layer has already cleared can
    # reach the topic question.
    #
    # OFF by default (TOPIC_GATE_ENABLED). Fails closed when on: the classifier
    # shares Ollama with the tutor, so if it is unreachable the turn was not
    # going to be answered anyway.
    # Prior turns give the classifier the context a follow-up needs. These are
    # the messages that survived the cross-session ledger and the per-grade turn
    # cap, so nothing older than this session reaches the classifier either.
    _topic_history = [
        str(m.get("content", ""))
        for m in messages[:-1]
        if isinstance(m, dict) and m.get("content")
    ]
    topic_msg = await topic_gate.off_topic_block_reason(
        user_question, age=age, history=_topic_history
    )
    if topic_msg is not None:
        logger.info("Topic gate blocked an off-topic turn for profile %s", profile_id)
        _trace["safety"] = {
            "category": "topic_redirect",
            "blocked_layer": "topic_gate",
        }
        _emit_trace()
        return _gate_block(model, topic_msg, stream=stream)

    # Safe — forward to Ollama

    # TODO(pedagogy): force-buffer homework turns when stream=True so the
    # enforcer can run — requires adding the hook to the buffered-stream path too.
    # Deferred: home deployment uses stream=False; only a small fraction of student
    # turns are homework pushes. Implement after the non-streaming path is validated.
    # A homework turn cannot use the progressive path: that path flushes the first
    # sentence to the child before the answer is complete, so the enforcer -- which
    # needs the WHOLE answer and may rewrite it -- has nothing it can still change.
    # Buffer those turns instead and let them fall through to the shared pipeline.
    #
    # Decided with the cheap regex trigger rather than the LLM gate on purpose: the
    # gate would add a model call to EVERY turn before a single token is generated.
    # The asymmetry favours over-buffering -- a wrongly buffered turn costs only
    # progressive rendering, while a wrongly streamed homework turn costs
    # enforcement entirely.
    _buffer_for_enforcer = False
    if stream and system_config.GUIDANCE_ENFORCEMENT_ENABLED:
        try:
            from core.pedagogy.trigger import is_homework_request

            _buffer_for_enforcer = bool(is_homework_request(user_question))
        except Exception as exc:  # never let this decide a turn by raising
            logger.warning("homework pre-check failed (streaming anyway): %s", exc)

    if stream and system_config.CHAT_STREAMING_ENABLED and not _buffer_for_enforcer:
        # Hold-back streaming: flush each part only AFTER check_output has vetted
        # the text-so-far, so the child never receives an un-vetted token. Two
        # checkpoints (first sentence, then the remainder at stream end) keep it
        # to ~+1 output-classifier call vs the buffered path. Off by default
        # (system_config.CHAT_STREAMING_ENABLED) — enabled only where the GPU has
        # headroom; the buffered path below is the single-GPU default.
        from safety.pipeline import safety_pipeline

        async def _vet(text: str):
            # check_output is sync (CPU + a blocking classifier call) — run it off
            # the event loop so it doesn't stall other concurrent requests.
            return await asyncio.to_thread(
                safety_pipeline.check_output,
                text=text,
                age=age,
                profile_id=profile_id,
                context=user_question,
            )

        def _fallback_for(out_result) -> str:
            return (
                out_result.modified_content
                # The blocked text here is the TUTOR's reply, so the child's own
                # question is what the crisis check needs to see.
                or safety_pipeline.get_safe_response(out_result, user_question)
                or "I'm not able to share that. Let's try something else!"
            )

        def _emit_block(out_result, text) -> None:
            # OUTPUT block: `text` here is the TUTOR'S draft. The crisis
            # decision must read the CHILD'S question instead, and the category
            # describes the model's output, so it must not promote on its own.
            blocks._record_safety_incident(
                profile_id,
                out_result,
                text,
                child_text=user_question,
                category_describes_child=False,
            )
            _trace["safety"] = {
                "category": str(out_result.category),
                "severity": str(out_result.severity),
                "blocked_layer": "output",
            }
            _emit_trace()

        async def _holdback_stream():
            _reminder_prefix = ""
            collected: list[bytes] = []
            flushed = 0
            checkpoint_done = False
            try:
                async for chunk in transport._stream_chunks_from_ollama(
                    body_bytes, fwd_headers
                ):
                    collected.append(chunk)
                    if checkpoint_done:
                        continue
                    text = blocks._extract_text_from_ndjson_chunks(collected)
                    if not blocks._first_checkpoint_ready(text):
                        continue
                    res = await _vet(text)
                    if not res.is_safe:
                        # Nothing flushed yet — replace the whole response.
                        _emit_block(res, text)
                        yield blocks._ollama_block_stream_bytes(
                            model, _fallback_for(res)
                        )
                        return
                    # The tag only ever leads the reply, so this first flush is
                    # the one that can carry it. Later flushes need no rewrite.
                    collected = blocks._strip_age_scaffolding_from_ndjson_chunks(
                        collected
                    )
                    # SB 243 §22602(c)(2) break reminder, leading the reply.
                    if break_reminder.due(profile_id):
                        _reminder_prefix = break_reminder.REMINDER_TEXT + "\n\n"
                        yield blocks._ollama_content_chunk_bytes(
                            model, _reminder_prefix
                        )
                        _trace["break_reminder"] = True
                    for c in collected[flushed:]:
                        yield c
                    flushed = len(collected)
                    checkpoint_done = True

                # Stream done — vet the FULL text before flushing the remainder.
                full = blocks._extract_text_from_ndjson_chunks(collected)
                res = await _vet(full)
                if not res.is_safe:
                    # The un-flushed remainder is un-vetted → withhold it and send
                    # a safe fallback for the rest. Already-flushed content passed
                    # the checkpoint check, so no unsafe token ever reached the child.
                    _emit_block(res, full)
                    yield blocks._ollama_block_stream_bytes(model, _fallback_for(res))
                    return
                for c in collected[flushed:]:
                    yield c
                _trace["blocked"] = False
                _trace["safety"] = {"blocked_layer": None}
                _emit_trace()
                # Remember this exchange so the client may replay it next turn.
                # Only on the fully-vetted success path: a blocked or errored
                # stream returns early above and must NOT be recorded, or the
                # withheld text would be replayable as "known" history.
                history_ledger.record_turn(
                    profile_id, messages[-1], _reminder_prefix + full
                )
            except httpx.ConnectError:
                _trace["safety"] = {"blocked_layer": "error"}
                _emit_trace()
                yield blocks._ollama_block_stream_bytes(
                    model, "The tutor is unavailable right now. Please try again."
                )
            except httpx.TimeoutException:
                logger.warning("tutor stream timed out; telling the student so")
                _trace["safety"] = {"blocked_layer": "error"}
                _emit_trace()
                yield blocks._ollama_block_stream_bytes(model, _TIMEOUT_MESSAGE)

        return StreamingResponse(_holdback_stream(), media_type="application/x-ndjson")

    # ---- Buffered streaming: gather the whole answer, then share ONE pipeline ----
    #
    # Open WebUI sends stream=True BY DEFAULT -- its /api/chat payload model
    # declares `stream: bool | None = True` and forwards it. Until this change both
    # streaming branches returned before the pedagogy block, so a streamed turn
    # NEVER REACHED THE ENFORCER and homework protection was inert for the real
    # client. Every reveal figure ever measured came from the non-streaming path.
    #
    # There is no latency cost here: this branch already collected every chunk
    # before returning a byte, so the child was waiting for the whole answer
    # regardless. What changes is that the answer now goes through the same
    # output-safety, enforcer, sycophancy, crisis-suffix and escalation stages a
    # non-streaming turn does. The enforcer may rewrite it, so the original chunk
    # sequence is re-emitted as a single chunk carrying the final text.
    _streamed = bool(stream)
    upstream = None
    # Untyped JSON either way: `.json()` on the non-streaming path returns Any, and
    # the streamed path builds the same shape by hand. Annotated so the streamed
    # literal does not give it a concrete type the isinstance guards below cannot
    # narrow through on re-index.
    upstream_json: Any = None
    if _streamed:
        try:
            collected: list[bytes] = []
            async for chunk in transport._stream_chunks_from_ollama(
                body_bytes, fwd_headers
            ):
                collected.append(chunk)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            # The client asked for a stream, so answer with one: a JSONResponse
            # to a streamed request renders as a BLANK bubble in Open WebUI
            # (that is #188, and this branch still had it).
            timed_out = isinstance(exc, httpx.TimeoutException)
            logger.warning(
                "tutor %s on the streamed path; telling the student so",
                "timed out" if timed_out else "was unreachable",
            )
            _trace["safety"] = {"blocked_layer": "error"}
            _emit_trace()
            return _gate_block(
                model,
                (
                    _TIMEOUT_MESSAGE
                    if timed_out
                    else "The tutor is unavailable right now. Please try again."
                ),
                stream=True,
            )
        # Token counts live on the final chunk; carry them so a streamed turn
        # reports usage the same way a buffered one does.
        _stream_usage = None
        for _line in b"".join(collected).splitlines():
            _line = _line.strip()
            if not _line:
                continue
            try:
                _obj = _json.loads(_line)
            except (ValueError, _json.JSONDecodeError):
                continue
            if isinstance(_obj, dict) and _obj.get("done"):
                _stream_usage = _usage_from(_obj)
        upstream_json = {
            "model": model,
            "done": True,
            "message": {
                "role": "assistant",
                "content": blocks._extract_text_from_ndjson_chunks(collected),
            },
        }
        if _stream_usage:
            upstream_json["prompt_eval_count"] = _stream_usage["input"]
            upstream_json["eval_count"] = _stream_usage["output"]

    else:
        try:
            upstream = await transport._forward_request(
                "POST",
                "/api/chat",
                content=body_bytes,
                headers=fwd_headers,
            )
        except httpx.ConnectError:
            _trace["safety"] = {"blocked_layer": "error"}
            _emit_trace()
            return JSONResponse(
                status_code=503,
                content={"detail": "Ollama backend unreachable"},
            )
        except httpx.TimeoutException:
            logger.warning("tutor timed out on the non-streamed path")
            _trace["safety"] = {"blocked_layer": "error"}
            _emit_trace()
            return _gate_block(model, _TIMEOUT_MESSAGE, stream=False)

        try:
            upstream_json = upstream.json()
        except (ValueError, _json.JSONDecodeError):
            upstream_json = None

    # Bound unconditionally so the response-serialization branch below can read it
    # on every path (incl. non-dict upstream_json). Set True only if the pedagogy
    # enforcer rewrote the content.
    _pedagogy_modified = False
    # Set when the disclosure router replaced the reply, so the block below
    # re-serialises. Separate from `_pedagogy_modified` because conflating them
    # would make the trace attribute a safeguarding override to the pedagogy
    # enforcer.
    _disclosure_overridden = False
    # ⚠️ SAME REASON, and it was missed the first time. `assistant_text` is only
    # assigned inside the `isinstance(upstream_json, dict)` branch below, but the
    # history-ledger block near the end reads it unconditionally -- so when
    # `upstream.json()` raises and the handler above sets `upstream_json = None`
    # ON PURPOSE, the deliberate fallback was followed by an UnboundLocalError
    # and the child got a 500 instead of the graceful message.
    #
    # Reachable whenever upstream returns a non-JSON body: a proxy error page, a
    # truncated response, an OOM message -- all of which this box produces under
    # GPU contention. Found by pyright (`possibly unbound`), invisible to tests,
    # and the second instance of this class on this function after `fwd_headers`.
    assistant_text = ""
    if isinstance(upstream_json, dict):
        msg = upstream_json.get("message")
        assistant_text = msg.get("content", "") if isinstance(msg, dict) else ""
        try:
            from safety.pipeline import safety_pipeline

            out_result = safety_pipeline.check_output(
                text=assistant_text,
                age=age,
                profile_id=profile_id,
                context=user_question,
            )
        except Exception as exc:
            logger.error(
                "check_output raised on non-streaming path: %s", exc, exc_info=True
            )
            block_msg = "I'm unable to process that request right now."
            _trace["safety"] = {"blocked_layer": "output"}
            _emit_trace()
            return _gate_block(model, block_msg, stream=_streamed)

        if not out_result.is_safe:
            block_msg = (
                out_result.modified_content
                # The blocked text here is the TUTOR's reply, so the child's own
                # question is what the crisis check needs to see.
                or safety_pipeline.get_safe_response(out_result, user_question)
                or "I'm not able to share that. Let's try something else!"
            )
            logger.info(
                "Output safety blocked response for profile %s (category=%s)",
                profile_id,
                out_result.category,
            )
            # OUTPUT block: `assistant_text` is the TUTOR'S reply; see above.
            blocks._record_safety_incident(
                profile_id,
                out_result,
                assistant_text,
                child_text=user_question,
                category_describes_child=False,
            )
            _trace["safety"] = {
                "category": str(out_result.category),
                "severity": str(out_result.severity),
                "blocked_layer": "output",
            }
            _emit_trace()
            return _gate_block(model, block_msg, stream=_streamed)

        # ---- Pedagogy post-processor (fail-OPEN, homework-integrity only) --------
        # Runs on EVERY buffered turn -- streamed and non-streamed alike. It used
        # to run only on the non-streaming path, which meant it never ran for the
        # real client at all (Open WebUI defaults stream=True).
        # Never blocks a turn: the outer except logs and serves the original text.
        if system_config.GUIDANCE_ENFORCEMENT_ENABLED:
            try:
                from core.pedagogy import enforce_guidance

                async def _regenerate(nudge: str) -> str:
                    return await _pedagogy_reissue(
                        body_bytes, fwd_headers, assistant_text, nudge, model
                    )

                async def _confirm_generate(prompt: str) -> str:
                    # CONFIRM_MODEL -> GATE_MODEL -> tutor. The tutor is LAST
                    # because running the confirm on it USED TO inherit the
                    # tutor's system prompt, whose brevity rules truncate the
                    # JSON verdict to `{"` and fail open on every reveal
                    # (measured 0/20, and again 0/27 with 27 unparseable
                    # verdicts on 2026-09-20).
                    #
                    # That fallback is reached by DEFAULT: both env vars default
                    # to "" (config.py), and the production compose passes
                    # neither, so a deployment could run with the reveal check
                    # silently dead. It is now SAFE rather than merely last --
                    # when the tutor's weights are used, the persona is replaced
                    # and the same cases score 22/27 instead of 0/27.
                    # The confirm does NOT inherit the GATE's model, and that is
                    # the whole point of this line.
                    #
                    # The gate and the confirm want opposite things. The gate
                    # runs on EVERY turn, so it wants small and cheap (~0.44s on
                    # e4b). The confirm's recall sets the leak FLOOR -- a reveal
                    # it never flags never enters the rewrite ladder -- so it
                    # wants the largest model available, which costs nothing
                    # because the tutor is already resident on the card.
                    #
                    # Until 2026-09-21 this read `CONFIRM_MODEL or GATE_MODEL or
                    # model`, so setting the gate to e4b silently moved the
                    # confirm there too. Caught by the post-deploy smoke printing
                    # `model=gemma4:e4b` minutes after shipping an ensemble
                    # measured at 90.4% recall ON THE 31b; the same prompts score
                    # ~71% on e4b, which is the entire improvement, lost to a
                    # fallback nobody set on purpose.
                    #
                    # Exactly the documented shape: a safety classifier that
                    # follows another component's model choice degrades in
                    # silence, because a weaker checker still returns a
                    # well-formed verdict.
                    confirm_model = (
                        system_config.GUIDANCE_ENFORCER_CONFIRM_MODEL or model
                    )
                    return await _pedagogy_oneshot(
                        prompt,
                        confirm_model,
                        fwd_headers,
                        system=(_CLASSIFIER_SYSTEM if confirm_model == model else None),
                        # Same condition, second consequence: when the confirm
                        # runs on the tutor's own weights it must ALSO drop the
                        # CPU pin, or the check runs a 31b on CPU while its copy
                        # sits on the card. Both hang off "is this the tutor?",
                        # so they are passed together and tested together.
                        tutor_model=model,
                    )

                _gate_generate = None
                if system_config.GUIDANCE_GATE_MODEL:
                    # A SMALL model: this runs on every turn, so its cost lands
                    # on non-homework turns too (~0.44s measured on e4b).
                    async def _gate_generate(prompt: str) -> str:  # noqa: F811
                        return await _pedagogy_oneshot(
                            prompt, system_config.GUIDANCE_GATE_MODEL, fwd_headers
                        )

                new_text, meta = await enforce_guidance(
                    user_question,
                    assistant_text,
                    _regenerate,
                    confirm_generate=_confirm_generate,
                    gate_generate=_gate_generate,
                )
                _revet: Optional[str] = None
                if new_text != assistant_text and isinstance(
                    upstream_json.get("message"), dict
                ):
                    # Re-vet the rewrite before serving: this is model output that
                    # the original check_output never saw. Fail-open: discard the
                    # rewrite on any doubt so the child always gets vetted text.
                    try:
                        rewrite_result = safety_pipeline.check_output(
                            text=new_text,
                            age=age,
                            profile_id=profile_id,
                            context=user_question,
                        )
                        if rewrite_result.is_safe:
                            upstream_json["message"]["content"] = new_text
                            _pedagogy_modified = True
                            _revet = "safe"
                        else:
                            _revet = "discarded"
                    except Exception as recheck_exc:
                        logger.warning(
                            "pedagogy rewrite safety recheck raised (discarding): %s",
                            recheck_exc,
                        )
                        _revet = "discarded"
                pedagogy_trace: Dict[str, Any] = {
                    "action": meta.action,
                    "attempts": meta.attempts,
                    "gate": getattr(meta, "gate", "none"),
                }
                if _revet is not None:
                    pedagogy_trace["revet"] = _revet
                _trace["pedagogy"] = pedagogy_trace
                # Content-free: lets a measurement tell a DRAFT that withheld from
                # a served reply the rewrite ladder produced. Set R could not
                # (2026-09-24) because action/attempts were never recorded.
                logger.info(
                    "pedagogy: action=%s attempts=%d revet=%s gate=%s",
                    meta.action,
                    meta.attempts,
                    _revet,
                    # "llm" | "regex_fallback" | "none". A gate that times out
                    # falls back to the weaker regex SILENTLY and FAST, so latency
                    # cannot show it; this field is the only signal (needed to
                    # watch the disclosure queue's contention with the gate, #326).
                    getattr(meta, "gate", "none"),
                )
            except Exception as exc:  # fail-open: never let pedagogy break a turn
                logger.warning("guidance enforcer errored (fail-open): %s", exc)

        # ---- Runtime S9051B screen on the FINAL delivered text ----
        # Deliberately last: the pedagogy enforcer may have rewritten the answer
        # above, and it is the text actually served that has to be clean. The
        # compliance eval grades the persona at BUILD time; this is what notices
        # if a model swap or Modelfile edit reintroduces flattery in production.
        #
        # Fail-open, the opposite of the topic gate: a missed "great job" is a
        # blemish, a refused answer is a broken tutor. OFF by default.
        try:
            from core.pedagogy import sycophancy_check

            _current = assistant_text
            if isinstance(upstream_json, dict) and isinstance(
                upstream_json.get("message"), dict
            ):
                _current = upstream_json["message"].get("content", assistant_text)

            async def _syco_regen(nudge: str) -> str:
                return await _pedagogy_reissue(
                    body_bytes, fwd_headers, _current, nudge, model
                )

            _cleaned, _syco_meta = await sycophancy_check.check_and_repair(
                _current, regenerate=_syco_regen
            )
            if _syco_meta.action not in ("disabled", "clean", "empty"):
                # Recorded even when repair failed — an unrepaired violation is
                # the drift signal this exists to surface.
                _trace["sycophancy"] = {
                    "action": _syco_meta.action,
                    "hits": _syco_meta.hits,
                    "categories": list(_syco_meta.categories),
                }
            if _cleaned != _current and isinstance(upstream_json, dict):
                upstream_json["message"]["content"] = _cleaned
                _pedagogy_modified = True
        except Exception as exc:  # fail-open
            logger.warning("sycophancy screen errored (fail-open): %s", exc)

    # Append a crisis resource for a disclosure. APPENDED, never substituted: the
    # tutor's own reply already redirects the child to a trusted adult in 15 of 17
    # measured cases, and replacing it would be a downgrade. 0 of those 17 named a
    # resource, and "talk to a counsellor" alone is incomplete for ideation.
    if _disclosure is not None:
        try:
            from safety.disclosure_detector import crisis_suffix

            _suffix = crisis_suffix(_disclosure.kind)
            if _suffix and isinstance(upstream_json.get("message"), dict):
                _body = upstream_json["message"].get("content") or ""
                if _body and _suffix.strip() not in _body:
                    upstream_json["message"]["content"] = _body + _suffix
        except Exception as exc:  # the reply matters more than the suffix
            logger.warning("crisis suffix append failed: %s", exc)

    # ---- Escalate on the tutor's OWN refusal ---------------------------------
    # Input classification of what a child says measured 12.5% on bullying
    # requests and 38.5% on risk disclosures. The MODEL is the effective safety
    # layer -- it refused 8 of 8 bullying requests and redirected 15 of 17
    # disclosures -- and its replies come from a stable system prompt, so that
    # distribution is narrow enough for patterns: 94.1% and 87.5%, with 0 of 61
    # homework refusals and 0 of 37 genuine turns falsely escalated.
    #
    # Never blocks and never alters the reply. The child already has a good
    # answer; this exists so an adult finds out.
    if _disclosure is None:
        try:
            from safety.escalation_signals import escalation_signal

            _served = ""
            if isinstance(upstream_json.get("message"), dict):
                _served = upstream_json["message"].get("content") or ""
            _sig = escalation_signal(
                text, _served, bool(_trace.get("pedagogy", {}).get("action"))
            )
            if _sig:
                blocks._record_disclosure_incident(profile_id, _sig, _sig, text)
                _trace["escalation"] = {"signal": _sig, "blocked": False}
        except Exception as exc:  # a child's answer matters more than the record
            logger.warning("escalation signal failed (continuing): %s", exc)

    # Last edit to the child-facing text: drop a leading age-range tag the model
    # narrated into its own reply. Deliberately after the enforcer, the
    # sycophancy screen and the crisis suffix -- it is the text that actually
    # ships that has to be clean, and a rewrite can reintroduce the tag.
    if isinstance(upstream_json, dict) and isinstance(
        upstream_json.get("message"), dict
    ):
        from core.response_scaffolding import strip_scaffolding

        _before = upstream_json["message"].get("content") or ""
        _after = strip_scaffolding(_before)
        if _after != _before:
            upstream_json["message"]["content"] = _after
            _pedagogy_modified = True

    # SB 243 §22602(c)(2): at least every three hours of continuing chat, tell
    # the child to take a break and that this is an AI, not a human. Placed
    # after every rewrite so it is part of the text that ships (and that the
    # history ledger records), and at the TOP so it is conspicuous.
    if (
        messages
        and isinstance(upstream_json, dict)
        and isinstance(upstream_json.get("message"), dict)
        and break_reminder.due(profile_id)
    ):
        upstream_json["message"]["content"] = break_reminder.with_reminder(
            upstream_json["message"].get("content") or ""
        )
        _pedagogy_modified = True  # forces the re-serialize below
        _trace["break_reminder"] = True

    _trace["blocked"] = False
    _trace["safety"] = {"blocked_layer": None}
    _trace["tokens"] = _usage_from(upstream_json)
    _emit_trace()
    # Strip the model's reasoning field before returning: OWUI >=0.10 mishandles
    # `message.thinking` (blank render) and the raw chain-of-thought is unvetted
    # content that must never reach a child. `content` is untouched. (Streaming
    # paths strip per-line in transport._stream_chunks_from_ollama.)
    # ---- Disclosure response router (BUFFERED paths only) -------------------
    #
    # The semantic verdict is normally already here: the job was submitted
    # BEFORE the tutor call and takes ~6s (e4b, CPU, measured), against a
    # 13-32s tutor turn. So this await usually returns instantly -- the queue
    # was simply throwing away a result it already had in time.
    #
    # ⚠️ Reached by the non-streaming path AND the buffered-stream path (which
    # re-emits the whole reply as one chunk). NOT by the progressive stream,
    # which flushes ~1-3s in, long before a ~6s verdict -- holding that flush
    # would add 3-5s of time-to-first-token to EVERY streamed turn for a rare
    # event, and TTFB is what a child actually perceives. That path needs an
    # APPEND instead, which is not in this commit.
    #
    # ⚠️ So requirement 4 (no schoolwork pivot) is satisfiable only here: an
    # append cannot un-say text already flushed. That is an OWNER decision,
    # stated in the PR rather than settled by me.
    _disclosure_kind = await _await_disclosure_verdict(_disclosure_verdict)
    _disclosure_override = disclosure_response.response_for(_disclosure_kind)
    if (
        _disclosure_override
        and isinstance(upstream_json, dict)
        and isinstance(upstream_json.get("message"), dict)
    ):
        upstream_json["message"]["content"] = _disclosure_override
        _disclosure_overridden = True
        logger.warning(
            "disclosure router replaced the reply for kind=%s", _disclosure_kind
        )

    out_content = upstream.content if upstream is not None else b""
    if isinstance(upstream_json, dict) and isinstance(
        upstream_json.get("message"), dict
    ):
        msg = upstream_json["message"]
        # Re-serialize when the enforcer rewrote the content OR when we need to
        # strip the model's reasoning field (OWUI >=0.10 blank-renders it and
        # chain-of-thought must never reach a child unvetted).
        if (
            "thinking" in msg
            or _pedagogy_modified
            or _streamed
            or _disclosure_overridden
        ):
            msg.pop("thinking", None)
            out_content = _json.dumps(upstream_json).encode()
    # Remember this exchange so the client may replay it next turn. Record the
    # text actually DELIVERED (the pedagogy enforcer may have rewritten it) —
    # that is what Open WebUI stores and will resend.
    # `isinstance(upstream_json, dict)` as well as `messages`: with a non-JSON
    # upstream there IS no exchange to remember, and recording an empty assistant
    # turn would put a blank reply into the history the client replays next turn.
    if messages and isinstance(upstream_json, dict):
        _delivered = assistant_text
        if isinstance(upstream_json, dict) and isinstance(
            upstream_json.get("message"), dict
        ):
            _delivered = upstream_json["message"].get("content", assistant_text)
        history_ledger.record_turn(profile_id, messages[-1], _delivered)

    # A streamed request must get NDJSON back. The enforcer may have rewritten the
    # answer, so the buffered chunk sequence no longer matches what should ship --
    # re-emit the final text as one chunk rather than replaying stale chunks.
    if _streamed:
        _final = ""
        if isinstance(upstream_json, dict) and isinstance(
            upstream_json.get("message"), dict
        ):
            _final = upstream_json["message"].get("content") or ""
        return Response(
            content=blocks._ollama_stream_bytes_for_text(
                model, _final, _usage_from(upstream_json)
            ),
            media_type="application/x-ndjson",
        )
    return Response(
        content=out_content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
