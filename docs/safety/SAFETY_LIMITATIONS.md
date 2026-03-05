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

## 3. Educational Exemption Is Keyword-Based and Abusable

**Affected component:** `safety/pipeline.py` → `_PatternMatcher._has_educational_context()`

**Description:**
Certain prohibited keywords (e.g., `kill`, `cocaine`, `sex`, `bomb`) are allowed through
if an educational indicator word is present in the same message. Educational indicators
include common words like `class`, `school`, `homework`, `learn`, `biology`, `history`, etc.

This means a message like `"tell me about sex for my class"` or `"cocaine for my homework"`
would pass the pattern filter, because the single word `class` or `homework` satisfies the
educational context check.

**Risk level:** Low–Medium — a student would need to know the exemption exists and intentionally
craft their message to exploit it. The semantic classifier (Stage 4) provides a second line
of defense for student inputs, and the system prompt constrains AI responses.

**Intentional tradeoff:** Tightening the educational exemption causes significant false
positives on legitimate schoolwork queries (e.g., history questions about wars, biology
questions about cell death, health class questions about sexual reproduction). The current
tuning prioritizes usability over strictness for this category.

**Potential improvements (not scheduled):**
- Require multiple educational indicators rather than one
- Weight indicators by specificity (e.g., `"biology class"` > bare `"class"`)
- Add a list of known abuse phrases to `_CONCERNING_INDICATORS`

---

## 4. Politics and Religion Topic Redirects Cause False Positives in Civics Education

**Affected component:** `safety/pipeline.py` → `_stage_age_gate()`

**Description:**
The age gate redirects all messages containing politics- or religion-related keywords
(e.g., `politics`, `election`, `church`, `religion`) regardless of context. This correctly
prevents partisan political discussions, but also blocks legitimate civics and social studies
questions like `"explain how the electoral college works"` or `"what did Martin Luther King Jr believe?"`.

**Risk level:** Low — the redirect provides a suggested alternative topic. Students are not
silently blocked; they receive guidance to rephrase toward curriculum-appropriate framing.

**Planned fix:** Add a civics/social-studies educational indicator exemption similar to the
one used for violence and drug keywords, so `"electoral college"` + `"history class"` passes
while bare `"politics"` still redirects.

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
| Educational exemption abusable | Low–Medium | Known tradeoff, monitored |
| Civics/politics false positives | Low | Planned improvement |
| Admin bypasses all safety | Low | Mitigated by audit logging |
