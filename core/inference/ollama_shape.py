"""Translation between the Ollama wire shape and the OpenAI wire shape.

The proxy speaks Ollama's shape to Open WebUI and reads it back in
``blocks.py``, ``history_ledger.py`` and the guidance enforcer. vLLM speaks the
OpenAI shape. Rather than rewrite those consumers — and re-validate the sealed
tutoring result that was measured through them — the conversion happens here, in
pure functions with no I/O, so both directions are directly testable.

Two rules are load-bearing:

1. **The persona is ours, never the client's.** Ollama applies the Modelfile
   SYSTEM block; vLLM applies nothing. The driver therefore prepends the persona
   and DROPS any system message that arrived in the request body, matching the
   proxy's own student-path behaviour.
2. **Thinking stays off.** gemma4 with thinking enabled can return an empty
   ``content`` and spend its budget on ``thinking``, which the proxy strips —
   the child would see an empty bubble (measured 2026-09-13, 2 of 4 prompts).
"""

from __future__ import annotations

import json
from typing import Optional

from core.inference.base import ChatChunk, ChatRequest, ChatResult, usage_from_openai

# Options that mean something to Ollama but are not OpenAI request fields.
_NON_SAMPLING_OPTIONS = frozenset({"num_ctx", "num_predict", "num_gpu", "think"})

_OPTION_TO_OPENAI = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "repeat_penalty": "repetition_penalty",
    "stop": "stop",
    "seed": "seed",
    "presence_penalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
}


def request_from_ollama(body: dict) -> ChatRequest:
    """Parse an Ollama ``/api/chat`` body into the engine-neutral request."""
    options = body.get("options")
    return ChatRequest(
        model=str(body.get("model") or ""),
        messages=[m for m in (body.get("messages") or []) if isinstance(m, dict)],
        stream=bool(body.get("stream", False)),
        options=dict(options) if isinstance(options, dict) else {},
        think=body.get("think", False),
    )


def request_to_openai(req: ChatRequest, *, persona: str, sampling: dict) -> dict:
    """Build an OpenAI ``/v1/chat/completions`` payload.

    ``sampling`` carries the Modelfile's PARAMETER block (temperature, top_p,
    top_k, repeat_penalty, stop). Ollama reads those from the model itself; vLLM
    only ever sees what we put in the request, so leaving them out would serve a
    different tutor than the one that was measured.
    """
    messages = [m for m in req.messages if m.get("role") != "system"]
    payload: dict = {
        "model": req.model,
        "messages": [{"role": "system", "content": persona}] + messages,
        "stream": bool(req.stream),
        # gemma4 is a reasoning model; the proxy never shows chain-of-thought.
        "chat_template_kwargs": {"thinking": bool(req.think)},
    }

    for key, value in (sampling or {}).items():
        payload[_OPTION_TO_OPENAI.get(key, key)] = value

    for key, value in (req.options or {}).items():
        if key in _NON_SAMPLING_OPTIONS:
            continue
        payload[_OPTION_TO_OPENAI.get(key, key)] = value

    num_predict = (req.options or {}).get("num_predict")
    if isinstance(num_predict, int) and num_predict > 0:
        payload["max_tokens"] = num_predict

    if req.stream:
        # Without this vLLM omits usage on streamed calls and traces lose tokens.
        payload["stream_options"] = {"include_usage": True}

    return payload


def result_from_openai(payload: dict) -> ChatResult:
    """Parse a non-streamed OpenAI response."""
    text = ""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            text = message.get("content") or ""
    return ChatResult(
        text=text,
        usage=usage_from_openai(payload),
        raw=payload if isinstance(payload, dict) else {},
    )


def result_to_ollama(result: ChatResult, *, model: str) -> dict:
    """Render a complete answer in the shape the proxy already consumes."""
    body: dict = {
        "model": model,
        "message": {"role": "assistant", "content": result.text},
        "done": True,
        "done_reason": "stop",
    }
    if result.usage:
        body["prompt_eval_count"] = result.usage.get("input", 0)
        body["eval_count"] = result.usage.get("output", 0)
    return body


def chunk_from_sse_line(line: str) -> Optional[ChatChunk]:
    """Parse one SSE line. Returns None for keep-alives and blanks."""
    if not line:
        return None
    text = line.strip()
    if not text or text.startswith(":"):
        return None
    if text.startswith("data:"):
        text = text[len("data:") :].strip()
    if not text:
        return None
    if text == "[DONE]":
        return ChatChunk(text="", done=True)
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    delta_text = ""
    done = False
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta")
        if isinstance(delta, dict):
            delta_text = delta.get("content") or ""
        if first.get("finish_reason"):
            done = True
    usage = usage_from_openai(payload)
    if usage is not None and not delta_text:
        # vLLM sends a final usage-only frame when stream_options asks for it.
        done = True
    return ChatChunk(text=delta_text, done=done, usage=usage)


def chunk_to_ollama_ndjson(chunk: ChatChunk, *, model: str) -> bytes:
    """Render one chunk as an Ollama NDJSON line (including the trailing \\n)."""
    payload: dict = {
        "model": model,
        "message": {"role": "assistant", "content": chunk.text},
        "done": bool(chunk.done),
    }
    if chunk.done:
        payload["done_reason"] = "stop"
    if chunk.usage:
        payload["prompt_eval_count"] = chunk.usage.get("input", 0)
        payload["eval_count"] = chunk.usage.get("output", 0)
    return (json.dumps(payload) + "\n").encode()
