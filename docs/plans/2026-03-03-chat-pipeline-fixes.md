# Chat Pipeline Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix four interconnected issues causing truncated responses, missing conversation history, admin usability, and visible thinking tokens in the snflwr-ai chat pipeline.

**Architecture:** Four independent fixes applied in order of impact: (1) Modelfile + timeout fixes unblock everything else, (2) switching generate→chat API adds conversation history, (3) admin profile handling prevents 404s, (4) Modelfile template fix hides thinking tokens.

**Tech Stack:** Python/FastAPI (`api/routes/chat.py`, `utils/ollama_client.py`), Ollama Modelfile (`models/Snflwr_AI_Kids.modelfile`), Open WebUI middleware (`frontend/open-webui/backend/open_webui/middleware/snflwr.py`), `config.py`, `resource_detection.py`

---

## Root Cause Summary

| Issue | Root Cause | Location |
|-------|-----------|----------|
| Response cut off | `num_ctx 2048` in Modelfile caps total tokens (system prompt alone is ~700 tokens) | `models/Snflwr_AI_Kids.modelfile` + `config.py` |
| Response cut off (timeout) | `OLLAMA_TIMEOUT = 30` — thinking models need more time | `config.py:88` + `frontend/.../middleware/snflwr.py:77` |
| No conversation history | `chat.py` calls `ollama_client.generate()` (flat prompt), ignores stored message history | `api/routes/chat.py:269` |
| Admin gets 404 | Admin has no child profile → `no_profile_<id>` → profile lookup fails | `api/routes/chat.py:155` |
| Thinking steps visible | `TEMPLATE {{ .Prompt }}` in Modelfile doesn't use qwen3.5 chat format with `<think>` delimiters | `models/Snflwr_AI_Kids.modelfile` |

---

## Task 1: Fix num_ctx and timeout (root cause of truncation)

**Files:**
- Modify: `models/Snflwr_AI_Kids.modelfile` (bottom parameters section)
- Modify: `config.py:88`
- Modify: `frontend/open-webui/backend/open_webui/middleware/snflwr.py:77`

**Step 1: Update Modelfile parameters**

Replace the bottom parameters block in `models/Snflwr_AI_Kids.modelfile`:

```
PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER top_k 40
PARAMETER num_ctx 8192
PARAMETER num_predict 4096
PARAMETER stop "</s>"
PARAMETER stop "Student:"
PARAMETER stop "Human:"
PARAMETER stop "User:"
```

(`num_ctx 8192` fits the system prompt + history + a full response comfortably. We still set it in the API options too for runtime override.)

**Step 2: Add num_ctx to the options passed in chat.py**

In `api/routes/chat.py`, the generate call (which will become a chat call in Task 2) should also pass `num_ctx` so a runtime value can override the Modelfile without rebuilding:

```python
options={
    'temperature': 0.7,
    'num_predict': _resources.num_predict,
    'num_ctx': _resources.num_ctx,
}
```

Add `num_ctx` to `ResourceProfile` in `resource_detection.py`, with a `recommend_num_ctx` function that mirrors `recommend_num_predict`:

```python
def recommend_num_ctx(memory_gb: float) -> int:
    """Context window size. Must be >= num_predict + system prompt tokens (~700)."""
    if memory_gb >= 32:
        return 32768
    if memory_gb >= 16:
        return 16384
    if memory_gb >= 8:
        return 8192
    if memory_gb >= 4:
        return 4096
    return 2048
```

Add to `ResourceProfile` dataclass:
```python
num_ctx: int = 8192
```

Add to `detect_resources()` profile construction:
```python
num_ctx=recommend_num_ctx(mem_gb),
```

Add to env-var overrides dict:
```python
'OLLAMA_NUM_CTX': 'num_ctx',
```

Add to `summary_lines()`:
```python
f"Ollama num_ctx: {self.num_ctx}",
```

**Step 3: Increase Ollama timeout**

In `config.py:88`, make it env-configurable (thinking models generating long responses need 2-5 minutes):

```python
OLLAMA_TIMEOUT: int = int(os.getenv('OLLAMA_TIMEOUT', '300'))
```

**Step 4: Increase httpx timeout in Open WebUI middleware**

In `frontend/open-webui/backend/open_webui/middleware/snflwr.py:77`:

```python
async with httpx.AsyncClient(timeout=300.0) as client:
```

**Step 5: Rebuild the Ollama model**

```bash
ollama create snflwr.ai -f models/Snflwr_AI_Kids.modelfile
```

Expected output: `success` (takes 10-30 seconds, doesn't re-download weights)

**Step 6: Restart the API server and test**

Send a long-form request (e.g., "Can you make me a lesson plan on regenerative farming for children?") and verify the response is complete.

**Step 7: Commit**

```bash
git add models/Snflwr_AI_Kids.modelfile resource_detection.py config.py \
    frontend/open-webui/backend/open_webui/middleware/snflwr.py
git commit -m "fix: increase num_ctx and timeouts to prevent response truncation"
```

---

## Task 2: Fix Modelfile template (hide thinking tokens)

**Files:**
- Modify: `models/Snflwr_AI_Kids.modelfile` (TEMPLATE section — add before SYSTEM)

**Background:** The model uses qwen3.5 which emits `<think>...</think>` blocks. Open WebUI already extracts these correctly when they're present. The current `TEMPLATE {{ .Prompt }}` doesn't use the qwen3.5 chat format, so thinking bleeds into the response text.

**Step 1: Add proper qwen3.5 chat template to Modelfile**

Add this TEMPLATE block between the FROM line and the SYSTEM block:

```
FROM qwen3.5:9b

TEMPLATE """{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end -}}
{{- range .Messages }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end -}}<|im_start|>assistant
{{- if .Thinking }}
<think>
{{ .Thinking }}</think>
{{ end }}"""
```

**Step 2: Rebuild and verify**

```bash
ollama create snflwr.ai -f models/Snflwr_AI_Kids.modelfile
```

Start a new chat. Open WebUI should show "Thought for X seconds" collapsed, with only the clean response visible. The reasoning steps should no longer appear in the main chat bubble.

**Step 3: Commit**

```bash
git add models/Snflwr_AI_Kids.modelfile
git commit -m "fix: add qwen3.5 chat template to Modelfile to properly delimit thinking tokens"
```

---

## Task 3: Switch from generate API to chat API (conversation history)

**Files:**
- Modify: `api/routes/chat.py:148-280`
- Read first: `storage/conversation_store.py` — `get_conversation_messages()` method at line 778

**Background:** `chat.py` currently calls `ollama_client.generate()` (flat prompt, no history). The `conversation_store` already stores messages after each turn but they're never fed back to Ollama. `ollama_client.chat()` already exists at `utils/ollama_client.py:360` and accepts a `messages` list in OpenAI format.

**Step 1: Load conversation history before the Ollama call**

In `api/routes/chat.py`, after the session is resolved (around line 198) and before the Ollama call (line 269), add history retrieval:

```python
# Load conversation history for this session
history_messages = []
try:
    conv_rows = conversation_store.db.execute_query(
        "SELECT conversation_id FROM conversations WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
        (session.session_id,)
    )
    if conv_rows:
        row = conv_rows[0]
        conv_id = row['conversation_id'] if isinstance(row, dict) else row[0]
        prior_messages = conversation_store.get_conversation_messages(conv_id)
        history_messages = [
            {"role": m.role, "content": m.content}
            for m in prior_messages
            if not m.safety_filtered
        ]
except DB_ERRORS as e:
    logger.warning(f"Could not load conversation history: {e}")
```

**Step 2: Replace the generate() call with chat()**

Replace:
```python
success, response_text, error = ollama_client.generate(
    model=model_name,
    prompt=request.message,
    options={
        'temperature': 0.7,
        'num_predict': _resources.num_predict,
        'num_ctx': _resources.num_ctx,
    }
)
```

With:
```python
messages = history_messages + [{"role": "user", "content": request.message}]
success, response_text, error = ollama_client.chat(
    model=model_name,
    messages=messages,
    options={
        'temperature': 0.7,
        'num_predict': _resources.num_predict,
        'num_ctx': _resources.num_ctx,
    }
)
```

**Step 3: Fix the error variable name**

`ollama_client.chat()` returns `(success, response_text, metadata)` — the third value is metadata, not an error string. Update the error check below:

```python
# Before (generate returned error as string in third slot):
if not success:
    logger.error(f"Ollama generation failed: {error}")
    raise HTTPException(status_code=503, detail=f"AI model unavailable: {error}")
```

Change to:
```python
if not success:
    err_msg = (error or {}).get('error', 'unknown error') if isinstance(error, dict) else str(error or 'unknown')
    logger.error(f"Ollama chat failed: {err_msg}")
    raise HTTPException(status_code=503, detail=f"AI model unavailable: {err_msg}")
```

Note: The variable `error` in the original code shadows the third return value — rename it to `metadata` throughout for clarity, but the minimal change is just fixing the error detail extraction.

**Step 4: Restart API and verify multi-turn conversation works**

Send two messages in the same chat. The second response should reference the first. Check `logs/api.log` to confirm no errors.

**Step 5: Commit**

```bash
git add api/routes/chat.py
git commit -m "feat: switch Ollama generate→chat API to enable conversation history"
```

---

## Task 4: Handle admin without child profile

**Files:**
- Modify: `api/routes/chat.py:151-173` (the profile lookup and authorization block)

**Background:** When admin sends a message, `get_profile_id_from_user` returns `no_profile_<user_id>`. The chat route then calls `profile_manager.get_profile("no_profile_...")` → None → 404. Admins need to be able to test the system without a child profile pre-existing.

**Step 1: Add admin bypass in the profile check**

In `chat.py`, replace the existing profile-not-found error:

```python
profile = profile_manager.get_profile(request.profile_id)

if not profile:
    raise HTTPException(
        status_code=404,
        detail=f"Profile {request.profile_id} not found. Please create a child profile first."
    )
```

With:

```python
profile = profile_manager.get_profile(request.profile_id)

if not profile:
    if auth_session.role == 'admin' and request.profile_id.startswith("no_profile_"):
        # Admin testing without a configured child profile — use a synthetic profile
        from core.profile_manager import ChildProfile
        profile = ChildProfile(
            profile_id=request.profile_id,
            parent_id=auth_session.user_id,
            name="Admin Test",
            age=13,
            is_active=True,
        )
    else:
        raise HTTPException(
            status_code=404,
            detail=f"Profile {request.profile_id} not found. Please create a child profile first."
        )
```

**Step 2: Verify ChildProfile constructor**

Before implementing, check `core/profile_manager.py` to confirm `ChildProfile` fields and constructor signature. The fields used (`profile_id`, `parent_id`, `name`, `age`, `is_active`) should match what's stored in the DB — adjust if needed.

```bash
grep -n "class ChildProfile\|dataclass\|ChildProfile(" /home/prime/Repos/snflwr-ai-main/core/profile_manager.py | head -20
```

**Step 3: Test as admin**

Log in as admin in Open WebUI, send a message, verify you get a response without a 404. Check `logs/api.log` — should see `"Admin testing without a child profile"` or similar.

**Step 4: Commit**

```bash
git add api/routes/chat.py
git commit -m "fix: allow admin to chat without a pre-configured child profile"
```

---

## Testing Checklist

After all tasks:

- [ ] Send a long-form request (lesson plan) — response should be complete, not cut off
- [ ] Send two messages in the same chat — second response should reference the first
- [ ] Open WebUI should show "Thought for X seconds" collapsed, not the reasoning steps inline
- [ ] Admin account can chat without creating a child profile first
- [ ] `logs/api.log` shows no 401, 403, or 504 errors during normal chat

## Execution Order

Tasks must be done in order: **1 → 2 → 3 → 4**. Task 1 is the prerequisite for all others (if `num_ctx` stays at 2048, the chat API in Task 3 will still truncate).
