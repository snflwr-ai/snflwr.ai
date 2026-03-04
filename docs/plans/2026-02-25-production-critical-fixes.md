# Production Critical Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix three critical COPPA/FERPA compliance bugs: broken encrypted search, ciphertext leaking to UI, and rate limiting failing open without Redis.

**Architecture:** Each fix is independent and separately committable. Fix 2 is a one-line logic change. Fix 1 adds an HMAC search index alongside the existing encryption. Fix 3 adds an in-memory fallback rate limiter. All fixes include TDD — write failing test first, then implement.

**Tech Stack:** Python 3.11, FastAPI, pytest, SQLite/PostgreSQL, HMAC-SHA256, threading

---

## Task 1: Fix `_maybe_decrypt` ciphertext leak

The simplest fix. `_maybe_decrypt()` currently returns raw ciphertext on decryption failure. Change it to return a sentinel message when encryption is enabled.

**Files:**
- Modify: `storage/conversation_store.py:99-109`
- Test: `tests/test_safety_integration.py` (add test to existing file)

**Step 1: Write the failing test**

Add to the bottom of `tests/test_safety_integration.py`, inside or after `TestConversationEncryptionFlow`:

```python
class TestDecryptionFailureSentinel:
    """Test that decryption failure returns sentinel, not ciphertext."""

    def test_maybe_decrypt_returns_sentinel_when_encryption_enabled(self, tmp_path):
        """When ENCRYPT_CONVERSATIONS=True and decryption fails, return sentinel."""
        from storage.encryption import EncryptionManager
        from storage.conversation_store import ConversationStore
        from unittest.mock import MagicMock, patch

        enc = EncryptionManager(key_dir=tmp_path)
        db = MagicMock()
        store = ConversationStore(db=db, encryption=enc)

        # Simulate garbled ciphertext that can't be decrypted
        garbled = "dGhpcyBpcyBub3QgcmVhbCBjaXBoZXJ0ZXh0"  # base64 but not valid Fernet

        with patch("storage.conversation_store.safety_config") as mock_cfg:
            mock_cfg.ENCRYPT_CONVERSATIONS = True
            result = store._maybe_decrypt(garbled)

        assert result == "[Message could not be decrypted]"
        assert garbled not in result  # No ciphertext leak

    def test_maybe_decrypt_returns_plaintext_when_encryption_disabled(self, tmp_path):
        """When ENCRYPT_CONVERSATIONS=False, assume legacy plaintext and return as-is."""
        from storage.encryption import EncryptionManager
        from storage.conversation_store import ConversationStore
        from unittest.mock import MagicMock, patch

        enc = EncryptionManager(key_dir=tmp_path)
        db = MagicMock()
        store = ConversationStore(db=db, encryption=enc)

        plaintext = "Hello, this is a normal message"

        with patch("storage.conversation_store.safety_config") as mock_cfg:
            mock_cfg.ENCRYPT_CONVERSATIONS = False
            result = store._maybe_decrypt(plaintext)

        assert result == plaintext

    def test_maybe_decrypt_round_trip_succeeds(self, tmp_path):
        """Valid encrypted content decrypts correctly (no sentinel)."""
        from storage.encryption import EncryptionManager
        from storage.conversation_store import ConversationStore
        from unittest.mock import MagicMock, patch

        enc = EncryptionManager(key_dir=tmp_path)
        db = MagicMock()
        store = ConversationStore(db=db, encryption=enc)

        original = "This is a secret message"
        encrypted = enc.encrypt_string(original)

        with patch("storage.conversation_store.safety_config") as mock_cfg:
            mock_cfg.ENCRYPT_CONVERSATIONS = True
            result = store._maybe_decrypt(encrypted)

        assert result == original
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_safety_integration.py::TestDecryptionFailureSentinel -v --no-header --tb=short 2>&1 | tail -15`

Expected: FAIL — `_maybe_decrypt` returns the garbled ciphertext instead of sentinel.

**Step 3: Implement the fix**

In `storage/conversation_store.py`, replace lines 99-109:

```python
    def _maybe_decrypt(self, stored: str) -> str:
        """Decrypt message content, falling back to plaintext for unencrypted messages."""
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

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_safety_integration.py::TestDecryptionFailureSentinel -v --no-header --tb=short 2>&1 | tail -15`

Expected: 3 PASSED

**Step 5: Run full test suite for regressions**

Run: `python3 -m pytest tests/ -m "not integration" --no-header -q 2>&1 | tail -5`

Expected: All pass (825+)

**Step 6: Commit**

```bash
git add storage/conversation_store.py tests/test_safety_integration.py
git commit -m "fix: return sentinel on decryption failure instead of leaking ciphertext (COPPA)"
```

---

## Task 2: Add `hmac_token()` to EncryptionManager

Add the HMAC method that the search index will use. This is a pure function with no side effects — easy to test in isolation.

**Files:**
- Modify: `storage/encryption.py:469-481` (after `verify_password`, before `generate_secure_token`)
- Test: `tests/test_encryption.py` (add test to existing file)

**Step 1: Write the failing test**

Add to `tests/test_encryption.py`:

```python
class TestHmacToken:
    """Test HMAC token hashing for encrypted search index."""

    def test_hmac_produces_hex_string(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc = EncryptionManager(key_dir=tmp_path)
        result = enc.hmac_token("hello")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest

    def test_hmac_deterministic(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc = EncryptionManager(key_dir=tmp_path)
        assert enc.hmac_token("hello") == enc.hmac_token("hello")

    def test_hmac_different_tokens_differ(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc = EncryptionManager(key_dir=tmp_path)
        assert enc.hmac_token("hello") != enc.hmac_token("world")

    def test_hmac_same_key_same_result(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc1 = EncryptionManager(key_dir=tmp_path)
        enc2 = EncryptionManager(key_dir=tmp_path)
        assert enc1.hmac_token("test") == enc2.hmac_token("test")

    def test_hmac_different_key_different_result(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc1 = EncryptionManager(key_dir=tmp_path / "k1")
        enc2 = EncryptionManager(key_dir=tmp_path / "k2")
        assert enc1.hmac_token("test") != enc2.hmac_token("test")

    def test_hmac_empty_string(self, tmp_path):
        from storage.encryption import EncryptionManager
        enc = EncryptionManager(key_dir=tmp_path)
        result = enc.hmac_token("")
        assert isinstance(result, str)
        assert len(result) == 64
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_encryption.py::TestHmacToken -v --no-header --tb=short 2>&1 | tail -15`

Expected: FAIL — `hmac_token` not defined.

**Step 3: Implement `hmac_token`**

In `storage/encryption.py`, add after the `verify_password` method (after line 469), before `generate_secure_token`:

```python
    def hmac_token(self, token: str) -> str:
        """
        HMAC-SHA256 a search token using the master key.

        Used by the encrypted search index to create deterministic,
        non-reversible hashes of content tokens for searchability
        without exposing plaintext.

        Args:
            token: The plaintext token to hash

        Returns:
            Hex-encoded HMAC-SHA256 digest (64 chars)
        """
        import hmac as _hmac
        key = self._master_key if self._master_key else b''
        return _hmac.new(key, token.encode('utf-8'), hashlib.sha256).hexdigest()
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_encryption.py::TestHmacToken -v --no-header --tb=short 2>&1 | tail -15`

Expected: 6 PASSED

**Step 5: Commit**

```bash
git add storage/encryption.py tests/test_encryption.py
git commit -m "feat: add hmac_token() to EncryptionManager for encrypted search index"
```

---

## Task 3: Add `message_search_index` schema

Add the table to both SQLite and PostgreSQL schemas.

**Files:**
- Modify: `database/schema.sql` (append after messages table indexes, around line 189)
- Modify: `database/schema_postgresql.sql` (append after messages table)

**Step 1: Add to SQLite schema**

Append after line 189 in `database/schema.sql` (after `CREATE INDEX ... idx_messages_filtered`):

```sql
-- Search index for encrypted conversations (HMAC token hashes)
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
CREATE INDEX IF NOT EXISTS idx_search_message ON message_search_index(message_id);
```

**Step 2: Add to PostgreSQL schema**

Find the messages table in `database/schema_postgresql.sql` and add after its indexes:

```sql
-- Search index for encrypted conversations (HMAC token hashes)
CREATE TABLE IF NOT EXISTS message_search_index (
    id SERIAL PRIMARY KEY,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_search_token ON message_search_index(token_hash);
CREATE INDEX IF NOT EXISTS idx_search_conversation ON message_search_index(conversation_id);
CREATE INDEX IF NOT EXISTS idx_search_message ON message_search_index(message_id);
```

**Step 3: Add runtime table creation**

In `storage/conversation_store.py`, add a method to `ConversationStore.__init__` that ensures the table exists (SQLite only — PostgreSQL uses migration scripts):

Add after line 97 (`logger.info("Conversation store initialized")`):

```python
        self._ensure_search_index_table()

    def _ensure_search_index_table(self):
        """Create search index table if it doesn't exist (SQLite auto-migration)."""
        try:
            self.db.execute_write("""
                CREATE TABLE IF NOT EXISTS message_search_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
                )
            """)
            self.db.execute_write(
                "CREATE INDEX IF NOT EXISTS idx_search_token ON message_search_index(token_hash)"
            )
            self.db.execute_write(
                "CREATE INDEX IF NOT EXISTS idx_search_conversation ON message_search_index(conversation_id)"
            )
        except Exception as e:
            logger.debug(f"Search index table setup: {e}")
```

**Step 4: Compile check**

Run: `python3 -m py_compile storage/conversation_store.py && echo OK`

Expected: OK

**Step 5: Commit**

```bash
git add database/schema.sql database/schema_postgresql.sql storage/conversation_store.py
git commit -m "feat: add message_search_index table for encrypted conversation search"
```

---

## Task 4: Wire HMAC search index into `add_message` and `search_conversations`

The core search fix. Tokenize plaintext before encryption, store HMAC hashes, query by hash on search.

**Files:**
- Modify: `storage/conversation_store.py:186-220` (`add_message`), `storage/conversation_store.py:406-489` (`search_conversations`)
- Test: `tests/test_safety_integration.py` (add search test)

**Step 1: Write the failing test**

Add to `tests/test_safety_integration.py`:

```python
class TestEncryptedSearch:
    """Test that search works when conversations are encrypted."""

    def test_search_finds_encrypted_message_by_token(self, tmp_path):
        """Add an encrypted message, search by a word in it, find the conversation."""
        from storage.encryption import EncryptionManager
        from storage.conversation_store import ConversationStore
        from unittest.mock import MagicMock, patch, call
        from datetime import datetime, timezone

        enc = EncryptionManager(key_dir=tmp_path)
        db = MagicMock()
        db.execute_write.return_value = None
        db.execute_query.return_value = []
        store = ConversationStore(db=db, encryption=enc)

        # Capture what gets written to search index
        write_calls = []
        original_write = db.execute_write
        def capture_write(sql, params=None):
            write_calls.append((sql, params))
            return original_write.return_value
        db.execute_write.side_effect = capture_write

        with patch("storage.conversation_store.safety_config") as mock_cfg:
            mock_cfg.ENCRYPT_CONVERSATIONS = True
            store.add_message(
                conversation_id="conv123",
                role="user",
                content="What is photosynthesis in biology"
            )

        # Verify search index inserts happened
        index_inserts = [
            c for c in write_calls
            if c[0] and "message_search_index" in str(c[0]) and "INSERT" in str(c[0])
        ]
        assert len(index_inserts) > 0, "Should have inserted token hashes into search index"

        # Verify the tokens include "photosynthesis" and "biology" (3+ char tokens)
        # Each insert has (message_id, conversation_id, token_hash) params
        inserted_hashes = set()
        for sql, params in index_inserts:
            if params and len(params) >= 3:
                inserted_hashes.add(params[2])  # token_hash

        # Compute expected hash for "photosynthesis"
        expected_hash = enc.hmac_token("photosynthesis")
        assert expected_hash in inserted_hashes, "Token hash for 'photosynthesis' should be in index"

    def test_search_conversations_uses_index_when_encrypted(self, tmp_path):
        """search_conversations should query the index table, not LIKE on ciphertext."""
        from storage.encryption import EncryptionManager
        from storage.conversation_store import ConversationStore
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timezone

        enc = EncryptionManager(key_dir=tmp_path)
        db = MagicMock()
        db.execute_query.return_value = []
        db.execute_write.return_value = None
        store = ConversationStore(db=db, encryption=enc)

        with patch("storage.conversation_store.safety_config") as mock_cfg:
            mock_cfg.ENCRYPT_CONVERSATIONS = True
            store.search_conversations(profile_id="prof123", search_text="photosynthesis")

        # Check that the query used message_search_index, not LIKE on content
        query_calls = db.execute_query.call_args_list
        assert len(query_calls) > 0
        sql = str(query_calls[-1])
        assert "message_search_index" in sql or "token_hash" in sql, \
            f"Should query search index, not LIKE on ciphertext. Got: {sql}"

    def test_search_without_text_still_works(self, tmp_path):
        """search_conversations without search_text should work as before."""
        from storage.encryption import EncryptionManager
        from storage.conversation_store import ConversationStore
        from unittest.mock import MagicMock, patch

        enc = EncryptionManager(key_dir=tmp_path)
        db = MagicMock()
        db.execute_query.return_value = []
        db.execute_write.return_value = None
        store = ConversationStore(db=db, encryption=enc)

        with patch("storage.conversation_store.safety_config") as mock_cfg:
            mock_cfg.ENCRYPT_CONVERSATIONS = True
            result = store.search_conversations(profile_id="prof123")

        assert result == []  # No results, but no crash
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_safety_integration.py::TestEncryptedSearch -v --no-header --tb=short 2>&1 | tail -15`

Expected: FAIL — no search index inserts, search still uses LIKE.

**Step 3: Add tokenization and indexing to `add_message`**

In `storage/conversation_store.py`, add a tokenization helper method to `ConversationStore`:

```python
    @staticmethod
    def _tokenize(text: str) -> set:
        """Tokenize text for search indexing. Returns deduplicated lowercase tokens >= 3 chars."""
        import re
        tokens = re.split(r'[\s\W]+', text.lower())
        return {t for t in tokens if len(t) >= 3}
```

Then in `add_message`, after the `execute_write` that inserts the message (after line 220 in current code), add:

```python
        # Index tokens for encrypted search (before encryption, content is still plaintext)
        if safety_config.ENCRYPT_CONVERSATIONS and self.encryption:
            try:
                tokens = self._tokenize(content)
                for token in tokens:
                    token_hash = self.encryption.hmac_token(token)
                    self.db.execute_write(
                        """
                        INSERT INTO message_search_index (message_id, conversation_id, token_hash)
                        VALUES (?, ?, ?)
                        """,
                        (message_id, conversation_id, token_hash)
                    )
            except Exception as e:
                # Non-fatal: search index failure should not block message storage
                logger.warning(f"Failed to index message tokens for search: {e}")
```

**Step 4: Rewrite `search_conversations` to use the index**

Replace the `search_text` branch in `search_conversations` (lines 427-440) with:

```python
            if search_text:
                if safety_config.ENCRYPT_CONVERSATIONS and self.encryption:
                    # Encrypted mode: search via HMAC token index
                    search_tokens = self._tokenize(search_text)
                    if not search_tokens:
                        return []
                    token_hashes = [self.encryption.hmac_token(t) for t in search_tokens]
                    placeholders = ','.join('?' * len(token_hashes))
                    query = f"""
                        SELECT DISTINCT c.conversation_id, c.session_id, c.profile_id,
                               c.created_at, c.updated_at, c.message_count,
                               c.subject_area, c.is_flagged, c.flag_reason
                        FROM conversations c
                        JOIN message_search_index msi ON c.conversation_id = msi.conversation_id
                        WHERE c.profile_id = ?
                        AND msi.token_hash IN ({placeholders})
                    """
                    params = [profile_id] + token_hashes
                else:
                    # Unencrypted mode: use SQL LIKE as before
                    query = """
                        SELECT DISTINCT c.conversation_id, c.session_id, c.profile_id,
                               c.created_at, c.updated_at, c.message_count,
                               c.subject_area, c.is_flagged, c.flag_reason
                        FROM conversations c
                        JOIN messages m ON c.conversation_id = m.conversation_id
                        WHERE c.profile_id = ?
                        AND m.content LIKE ?
                    """
                    params = [profile_id, f"%{search_text}%"]
```

**Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_safety_integration.py::TestEncryptedSearch -v --no-header --tb=short 2>&1 | tail -15`

Expected: 3 PASSED

**Step 6: Run full test suite**

Run: `python3 -m pytest tests/ -m "not integration" --no-header -q 2>&1 | tail -5`

Expected: All pass

**Step 7: Commit**

```bash
git add storage/conversation_store.py tests/test_safety_integration.py
git commit -m "feat: HMAC search index for encrypted conversations (COPPA parental oversight)"
```

---

## Task 5: Add in-memory rate limit fallback

Replace the "allow all" fallback with an in-memory sliding window rate limiter.

**Files:**
- Modify: `utils/rate_limiter.py`
- Create: `tests/test_rate_limiter.py`

**Step 1: Write the failing tests**

Create `tests/test_rate_limiter.py`:

```python
"""
Tests for rate limiter with in-memory fallback.
"""

import time
import pytest
from unittest.mock import MagicMock, patch


class TestLocalRateLimiter:
    """Test the in-memory fallback rate limiter."""

    def test_allows_within_limit(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        allowed, info = limiter.check_rate_limit("user1", 5, 60, "api")
        assert allowed is True
        assert info['remaining'] == 4

    def test_blocks_when_limit_exceeded(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user1", 5, 60, "api")
        allowed, info = limiter.check_rate_limit("user1", 5, 60, "api")
        assert allowed is False
        assert info['remaining'] == 0

    def test_different_identifiers_independent(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user1", 5, 60, "api")
        allowed, _ = limiter.check_rate_limit("user2", 5, 60, "api")
        assert allowed is True

    def test_different_limit_types_independent(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user1", 5, 60, "api")
        allowed, _ = limiter.check_rate_limit("user1", 5, 60, "auth")
        assert allowed is True

    def test_window_expires(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        # Use 1-second window
        for _ in range(3):
            limiter.check_rate_limit("user1", 3, 1, "api")
        allowed, _ = limiter.check_rate_limit("user1", 3, 1, "api")
        assert allowed is False
        time.sleep(1.1)
        allowed, _ = limiter.check_rate_limit("user1", 3, 1, "api")
        assert allowed is True

    def test_returns_retry_after(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        for _ in range(5):
            limiter.check_rate_limit("user1", 5, 60, "api")
        _, info = limiter.check_rate_limit("user1", 5, 60, "api")
        assert info['retry_after'] > 0

    def test_info_dict_keys(self):
        from utils.rate_limiter import LocalRateLimiter
        limiter = LocalRateLimiter()
        _, info = limiter.check_rate_limit("user1", 5, 60, "api")
        assert 'remaining' in info
        assert 'retry_after' in info
        assert 'reset_time' in info


class TestRateLimiterFallback:
    """Test that RateLimiter falls back to LocalRateLimiter when Redis is down."""

    def test_production_non_critical_uses_local_fallback(self):
        from utils.rate_limiter import RateLimiter
        mock_cache = MagicMock()
        mock_cache.enabled = False
        limiter = RateLimiter(redis_cache=mock_cache)

        with patch("utils.rate_limiter._IS_PRODUCTION", True):
            allowed, info = limiter.check_rate_limit(
                "user1", 100, 60, limit_type="chat"
            )

        # Should be allowed (first request) but via local limiter, not bypass
        assert allowed is True
        assert 'remaining' in info
        # Should NOT have the old bypass warning
        assert info.get('warning') != 'Rate limiting disabled - Redis not available'

    def test_production_critical_still_fails_closed(self):
        from utils.rate_limiter import RateLimiter
        mock_cache = MagicMock()
        mock_cache.enabled = False
        limiter = RateLimiter(redis_cache=mock_cache)

        with patch("utils.rate_limiter._IS_PRODUCTION", True):
            allowed, info = limiter.check_rate_limit(
                "user1", 10, 60, limit_type="auth"
            )

        # Auth should still fail closed
        assert allowed is False

    def test_development_uses_local_fallback(self):
        from utils.rate_limiter import RateLimiter
        mock_cache = MagicMock()
        mock_cache.enabled = False
        limiter = RateLimiter(redis_cache=mock_cache)

        with patch("utils.rate_limiter._IS_PRODUCTION", False):
            allowed, info = limiter.check_rate_limit(
                "user1", 100, 60, limit_type="api"
            )

        assert allowed is True
        assert 'remaining' in info
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rate_limiter.py -v --no-header --tb=short 2>&1 | tail -20`

Expected: FAIL — `LocalRateLimiter` not found.

**Step 3: Implement `LocalRateLimiter`**

In `utils/rate_limiter.py`, add after the imports (line 24) and before the `RateLimiter` class:

```python
class LocalRateLimiter:
    """
    In-memory sliding window rate limiter.

    Fallback for when Redis is unavailable. NOT distributed — each process
    instance has its own counters. Provides degraded-mode protection rather
    than no protection at all.
    """

    def __init__(self):
        self._windows: dict = {}  # {window_key: [timestamp, ...]}
        self._lock = __import__('threading').Lock()
        self._warned: set = set()  # Track which keys we've warned about

    def check_rate_limit(
        self,
        identifier: str,
        max_requests: int,
        window_seconds: int,
        limit_type: str = "api",
    ) -> Tuple[bool, dict]:
        current_time = time.time()
        window_key = f"{limit_type}:{identifier}"

        with self._lock:
            # Lazy cleanup: remove expired entries
            if window_key in self._windows:
                cutoff = current_time - window_seconds
                self._windows[window_key] = [
                    t for t in self._windows[window_key] if t > cutoff
                ]
            else:
                self._windows[window_key] = []

            request_count = len(self._windows[window_key])
            allowed = request_count < max_requests

            if allowed:
                self._windows[window_key].append(current_time)

            remaining = max(0, max_requests - request_count - (1 if allowed else 0))

            # Calculate retry_after
            if not allowed and self._windows[window_key]:
                oldest = min(self._windows[window_key])
                retry_after = max(0, int((oldest + window_seconds) - current_time))
                reset_time = oldest + window_seconds
            else:
                retry_after = 0
                reset_time = current_time + window_seconds

            # Periodic cleanup of stale keys (every 100 checks)
            if len(self._windows) > 1000:
                self._cleanup_stale(current_time, window_seconds)

        # Warn once per limit_type
        if limit_type not in self._warned:
            self._warned.add(limit_type)
            logger.warning(
                f"Using in-memory rate limiting for '{limit_type}' (Redis unavailable)"
            )

        return allowed, {
            'remaining': remaining,
            'reset_time': datetime.fromtimestamp(reset_time).isoformat(),
            'retry_after': retry_after,
            'limit': max_requests,
            'window': window_seconds,
            'backend': 'local',
        }

    def _cleanup_stale(self, current_time: float, default_window: int = 120):
        """Remove window keys with no recent entries."""
        cutoff = current_time - (default_window * 2)
        stale = [k for k, v in self._windows.items() if not v or max(v) < cutoff]
        for k in stale:
            del self._windows[k]


# Module-level local fallback instance
_local_limiter = LocalRateLimiter()
```

**Step 4: Wire the fallback into `RateLimiter.check_rate_limit`**

Replace lines 100-117 (the production non-critical and development branches) with:

```python
            if _IS_PRODUCTION and is_critical:
                # CRITICAL: In production, rate limiting on auth endpoints is mandatory
                logger.critical(
                    f"SECURITY ALERT: Rate limiting unavailable for critical endpoint '{limit_type}'. "
                    f"Redis is REQUIRED in production for rate limiting. "
                    f"Denying request to prevent potential brute force attacks."
                )
                return False, {
                    'remaining': 0,
                    'reset_time': None,
                    'retry_after': 60,
                    'error': 'Rate limiting service unavailable - request denied for security'
                }

            # Fall back to in-memory rate limiting (non-distributed but still protective)
            return _local_limiter.check_rate_limit(identifier, max_requests, window_seconds, limit_type)
```

Also update `TokenBucketRateLimiter.check_rate_limit` lines 301-302 (the `if not self.cache.enabled:` branch) to use the local fallback for the sliding window equivalent:

```python
        if not self.cache.enabled:
            # Fall back to local sliding window (not token bucket, but still protective)
            return _local_limiter.check_rate_limit(identifier, capacity, 60, "token_bucket")
```

**Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rate_limiter.py -v --no-header --tb=short 2>&1 | tail -20`

Expected: 10 PASSED

**Step 6: Run full test suite**

Run: `python3 -m pytest tests/ -m "not integration" --no-header -q 2>&1 | tail -5`

Expected: All pass

**Step 7: Commit**

```bash
git add utils/rate_limiter.py tests/test_rate_limiter.py
git commit -m "fix: add in-memory rate limit fallback when Redis unavailable (COPPA security)"
```

---

## Task 6: Final validation

Run the complete test suite and verify coverage.

**Step 1: Full test suite**

Run: `python3 -m pytest tests/ -m "not integration" -v --no-header 2>&1 | tail -20`

Expected: All tests pass, coverage >= 29%.

**Step 2: Compile check all modified files**

Run: `python3 -m py_compile storage/conversation_store.py && python3 -m py_compile storage/encryption.py && python3 -m py_compile utils/rate_limiter.py && echo "All OK"`

Expected: All OK
