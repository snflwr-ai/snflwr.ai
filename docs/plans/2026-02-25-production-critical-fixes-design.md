# Production Critical Fixes Design

**Goal:** Fix three critical production bugs that break COPPA/FERPA compliance when conversation encryption is enabled (the default).

**Scope:** Tier 1 only — the three issues that are broken in production right now.

---

## Fix 1: HMAC Search Index for Encrypted Conversations

### Problem

`search_conversations()` in `storage/conversation_store.py:406-439` runs `SQL LIKE` on ciphertext. When `ENCRYPT_CONVERSATIONS=true` (default for COPPA), search returns zero results. Parents cannot search their child's conversations, violating COPPA parental oversight requirements.

### Design

Add a `message_search_index` table storing HMAC-SHA256 hashes of content tokens. On `add_message`, tokenize plaintext before encryption, HMAC each token, store in the index. On `search_conversations`, hash the query tokens the same way and match against the index.

**Data flow — write path:**
```
add_message(content="Hello world")
  → tokenize("hello world") → ["hello", "world"]
  → HMAC-SHA256("hello", master_key), HMAC-SHA256("world", master_key)
  → INSERT INTO message_search_index (message_id, conversation_id, token_hash)
  → encrypt(content) → INSERT INTO messages
```

**Data flow — search path:**
```
search_conversations(search_text="hello")
  → tokenize("hello") → ["hello"]
  → HMAC-SHA256("hello", master_key)
  → SELECT DISTINCT conversation_id FROM message_search_index WHERE token_hash = ?
  → return matching conversations
```

**Schema addition (both SQLite and PostgreSQL):**
```sql
CREATE TABLE IF NOT EXISTS message_search_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_search_token ON message_search_index(token_hash);
CREATE INDEX IF NOT EXISTS idx_search_conversation ON message_search_index(conversation_id);
```

**Tokenization rules:**
- Lowercase
- Split on whitespace and punctuation
- Strip tokens < 3 chars (noise: "a", "is", "to")
- Deduplicate per message
- HMAC key = encryption master key (already managed by EncryptionManager)

**New method on EncryptionManager:**
```python
def hmac_token(self, token: str) -> str:
    """HMAC-SHA256 a search token using the master key."""
```

**Files changed:**
- `storage/encryption.py` — add `hmac_token()` method
- `storage/conversation_store.py` — add `_index_message_tokens()`, modify `add_message()` and `search_conversations()`
- `database/schema.sql` — add `message_search_index` table
- `database/schema_postgresql.sql` — add same table (use SERIAL instead of AUTOINCREMENT)

**Limitations:**
- Exact token match only (no substring/partial search)
- Existing encrypted messages won't be searchable until re-indexed
- Stop-word filtering is minimal (length < 3 only)

---

## Fix 2: Sentinel Message for Decryption Failures

### Problem

`_maybe_decrypt()` in `storage/conversation_store.py:99-109` returns raw ciphertext to the UI when decryption fails. This leaks encrypted data and shows garbled text to parents/students.

### Design

When decryption fails and encryption is currently enabled, return a fixed sentinel string instead of ciphertext. Log the failure for ops.

**New behavior:**
```python
def _maybe_decrypt(self, stored: str) -> str:
    if not stored or not self.encryption:
        return stored
    try:
        result = self.encryption.decrypt_string(stored)
        if result is not None:
            return result
    except Exception:
        pass
    # Decryption failed or returned None.
    # If encryption is enabled, this is a real failure — don't leak ciphertext.
    if safety_config.ENCRYPT_CONVERSATIONS:
        logger.warning(f"Decryption failed for stored message (len={len(stored)})")
        return "[Message could not be decrypted]"
    # Encryption disabled — assume legacy plaintext
    return stored
```

**Files changed:**
- `storage/conversation_store.py` — modify `_maybe_decrypt()`

---

## Fix 3: In-Memory Rate Limit Fallback

### Problem

`utils/rate_limiter.py:100-117` — when Redis is unavailable, non-critical endpoints bypass rate limiting entirely (return `True` with all limits). This means in production, a Redis outage removes all rate limiting from chat, API, and email verification endpoints.

### Design

Add a `LocalRateLimiter` class that provides in-memory sliding window rate limiting. When Redis is unavailable, `RateLimiter.check_rate_limit()` falls back to this local implementation instead of returning `True`.

**LocalRateLimiter behavior:**
- Dict of `{window_key: [(timestamp, ...), ...]}` with `threading.Lock`
- Same sliding window algorithm as Redis version: remove expired entries, count remaining
- Lazy cleanup on each check (remove windows older than 2x window_seconds)
- Logs once per key when falling back: "Using in-memory rate limiting for '{limit_type}'"
- NOT distributed — each instance has its own limits. This is acceptable as degraded-mode protection.

**Integration point in RateLimiter.check_rate_limit():**

Replace lines 100-117 (the "Redis not available" branch for production non-critical) with:
```python
if _IS_PRODUCTION:
    logger.warning(
        f"Redis unavailable for '{limit_type}', falling back to in-memory rate limiting."
    )
    return _local_limiter.check_rate_limit(identifier, max_requests, window_seconds, limit_type)
```

Also update `TokenBucketRateLimiter` line 301-302 with same pattern.

**Files changed:**
- `utils/rate_limiter.py` — add `LocalRateLimiter` class, modify fallback paths in both `RateLimiter` and `TokenBucketRateLimiter`

---

## Testing

Each fix gets unit tests added to the existing test files or new test files:
- Fix 1: Tests in `tests/test_safety_integration.py` — round-trip: add encrypted message → search by token → find it
- Fix 2: Tests for `_maybe_decrypt` sentinel behavior
- Fix 3: Tests for `LocalRateLimiter` in isolation and for the fallback path

---

## Out of Scope

- Tier 2 (health probes, Sentry, env validation) — deferred
- Tier 3 (Docker, CI, PII logging) — deferred
- Re-indexing existing encrypted messages — migration script deferred (new messages will be indexed immediately)
