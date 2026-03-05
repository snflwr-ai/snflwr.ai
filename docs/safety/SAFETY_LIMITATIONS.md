# Known Safety Limitations

This document records known gaps and intentional tradeoffs in the snflwr.ai safety pipeline.
It exists so that deployers, contributors, and auditors have a clear picture of what the system
does and does not protect against. These are not bugs — they are scoped decisions with documented
rationale and planned follow-up work.

---

## 1. ~~AI Output Has No Semantic Classifier~~ — RESOLVED

Stage 4 (semantic classifier) was added to `check_output()` in March 2026.
AI responses now go through Llama Guard screening in addition to pattern matching.
If Ollama is unavailable, the classifier degrades gracefully (skipped, not fail-closed)
to avoid blocking all responses during an outage.

---

## 2. Age Is Self-Reported — No Parental Verification Enforcement

**Affected component:** `core/profile_manager.py`, `api/routes/profiles.py`

**Description:**
The age gate applies restrictions based on the age stored in a child's profile. That age
is entered by the parent during profile creation. There is no independent verification
mechanism (e.g., parental consent flow, document check, or cross-reference) to confirm
the age is accurate. A parent could create a profile with an incorrect age, intentionally
or by mistake, bypassing age-appropriate restrictions.

**Risk level:** Medium — the primary trust boundary is the parent account, not the child.
Parents are authenticated and take responsibility for profile data accuracy.

**Planned fix:** Add a parental consent flow during profile creation that confirms the
child's age and grade level, with periodic re-verification prompts. This requires a
separate UX design pass.

---

## 3. ~~Educational Exemption Abusable with Generic Words~~ — RESOLVED

The exemption now requires subject-specific indicators (`"biology"`, `"chemistry"`, `"physics"`,
`"history"`, etc.). Generic words like `"class"` or `"homework"` alone no longer bypass keyword
blocks on contextual keywords (`bomb`, `sex`, `cocaine`, `kill`, etc.).

Messages blocked where a weak indicator suggests possible legitimate academic intent are flagged
with `possible_false_positive=True` in the API response. Educators and parents can report these
via `POST /api/safety/false-positive` (requires parent/admin auth). Reports are stored in the
`safety_false_positives` table and reviewed by developers via `GET /api/admin/false-positives`
and marked resolved via `PATCH /api/admin/false-positives/{id}`.

Bare `"class"` or `"homework"` alone is NOT sufficient to bypass the block — subject-specific
indicators are required (see `_STRONG_EDUCATIONAL_INDICATORS` in `safety/pipeline.py`).

---

## 4. ~~Politics/Religion Redirects Cause False Positives~~ — RESOLVED

A `_CIVICS_INDICATORS` exemption was added to `_stage_age_gate()` in March 2026.
Messages containing a politics or religion keyword now pass through the redirect
if the text also contains a specific civics/social-studies indicator (e.g., `"civics"`,
`"social studies"`, `"world history"`, `"religious studies"`, `"electoral college"`).

Bare words like `"class"` or `"school"` alone are **not** sufficient to bypass —
a multi-word or subject-specific indicator is required to prevent gaming.

---

## 5. Admin Chat Bypasses All Safety Filtering

**Affected component:** `api/routes/chat.py` → `skip_safety` path

**Description:**
When a user with the `admin` role sends a chat message, the entire safety pipeline
(input filter, output filter, safety monitoring) is skipped. This is by design — admins
need to test the system without triggering safety blocks — but it means an admin account
can request any content without restriction.

Admin chat usage is now audit-logged (`chat_admin` event type), providing an accountability
trail. However, the content of admin messages is not logged.

**Risk level:** Low — admin accounts require explicit role assignment via Open WebUI and
are not accessible to students or parents.

**Mitigation in place:** `audit_log('chat_admin', ...)` records who used the admin chat
endpoint and when.

**Potential improvement:** Add an optional admin-mode safety report (log what *would* have
been blocked) without actually blocking, for audit purposes.

---

## Summary Table

| Limitation | Risk | Status |
|---|---|---|
| No semantic classifier on AI output | Medium | Planned — needs perf design |
| Self-reported age, no verification | Medium | Planned — needs UX design |
| Educational exemption abusable | Low–Medium | ✅ Resolved — subject-specific indicators required; false positive reporting added |
| Civics/politics false positives | Low | Planned improvement |
| Admin bypasses all safety | Low | Mitigated by audit logging |
