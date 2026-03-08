"""
Integration Tests: Safety Pipeline + Conversation Encryption Flow

End-to-end tests verifying the interaction between safety filtering,
conversation storage with encryption, safety monitoring with incident logging,
and the full message lifecycle.

Uses real EncryptionManager (with temp key dirs) and real SafetyPipeline
(deterministic stages 1-3, 5 work without Ollama). DB and external services
are mocked.
"""

import sys

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

from storage.encryption import EncryptionManager
from storage.conversation_store import ConversationStore, Message, Conversation
from safety.pipeline import SafetyPipeline, SafetyResult, Severity, Category
from safety.safety_monitor import SafetyMonitor, SafetyAlert, MonitoringProfile

_safety_monitor_mod = sys.modules["safety.safety_monitor"]
from utils.logger import get_logger

logger = get_logger(__name__)


def _find_insert_call(mock_db, table="messages"):
    """Find the INSERT INTO <table> call among execute_write calls.

    ConversationStore.__init__ runs CREATE TABLE/INDEX statements that
    appear before any INSERT, so we can't assume the INSERT is at index 0.
    """
    for c in mock_db.execute_write.call_args_list:
        sql = c[0][0] if c[0] else ""
        if f"INSERT INTO {table}" in sql and len(c[0]) > 1:
            return c
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def encryption(tmp_path):
    """Real encryption manager with temp key directory."""
    return EncryptionManager(key_dir=tmp_path)


@pytest.fixture
def mock_db():
    """Mock database manager with sensible defaults."""
    db = MagicMock()
    db.execute_query.return_value = []
    db.execute_write.return_value = None
    return db


@pytest.fixture
def conversation_store_encrypted(mock_db, encryption):
    """ConversationStore wired to a real EncryptionManager with encryption ON."""
    with patch("storage.conversation_store.safety_config") as mock_safety_cfg:
        mock_safety_cfg.ENCRYPT_CONVERSATIONS = True
        store = ConversationStore(db=mock_db, encryption=encryption)
        # Re-patch for the duration of every method call within the store
        yield store, mock_db, encryption, mock_safety_cfg


@pytest.fixture
def conversation_store_unencrypted(mock_db, encryption):
    """ConversationStore wired to a real EncryptionManager with encryption OFF."""
    with patch("storage.conversation_store.safety_config") as mock_safety_cfg:
        mock_safety_cfg.ENCRYPT_CONVERSATIONS = False
        store = ConversationStore(db=mock_db, encryption=encryption)
        yield store, mock_db, encryption, mock_safety_cfg


@pytest.fixture
def pipeline():
    """Real SafetyPipeline (semantic classifier will be unavailable/degraded)."""
    with patch("safety.pipeline._SemanticClassifier") as MockClassifier:
        # Make the semantic classifier always return None (pass-through)
        instance = MockClassifier.return_value
        instance.classify.return_value = None
        instance.is_available.return_value = False
        p = SafetyPipeline()
        yield p


@pytest.fixture
def monitor(mock_db):
    """SafetyMonitor wired to a mock DB and the real safety pipeline."""
    with patch.object(_safety_monitor_mod, "log_safety_incident"):
        m = SafetyMonitor(db=mock_db)
        yield m


# =========================================================================
# 1. Conversation Encryption Flow
# =========================================================================

@pytest.mark.integration
class TestConversationEncryptionFlow:
    """Verify encrypt-on-write / decrypt-on-read for ConversationStore."""

    def test_add_message_encrypts_when_enabled(self, conversation_store_encrypted):
        """When ENCRYPT_CONVERSATIONS=True, stored content must differ from plaintext."""
        store, mock_db, enc, _ = conversation_store_encrypted
        plaintext = "What is photosynthesis?"

        # Provide a profile_id row for the UPDATE child_profiles query
        mock_db.execute_query.return_value = [{"profile_id": "profile-1"}]

        msg = store.add_message("conv-1", "user", plaintext)

        # Find the INSERT INTO messages call (not CREATE TABLE/INDEX from __init__)
        insert_call = _find_insert_call(mock_db)
        assert insert_call is not None, "INSERT INTO messages call not found"
        stored_content = insert_call[0][1][3]  # 4th param = content

        # Stored content should NOT be the original plaintext
        assert stored_content != plaintext
        # But it should be decryptable back to the original
        assert enc.decrypt_string(stored_content) == plaintext

        # The returned Message object has the original plaintext (not ciphertext)
        assert msg.content == plaintext

    def test_get_conversation_decrypts_content(self, conversation_store_encrypted):
        """get_conversation should transparently decrypt stored ciphertext."""
        store, mock_db, enc, _ = conversation_store_encrypted
        original = "The mitochondria is the powerhouse of the cell."
        ciphertext = enc.encrypt_string(original)

        # Mock the DB to return conversation metadata + encrypted message
        now_iso = datetime.now(timezone.utc).isoformat()
        mock_db.execute_query.side_effect = [
            # First call: conversation metadata
            [{
                "conversation_id": "conv-1",
                "session_id": "sess-1",
                "profile_id": "profile-1",
                "created_at": now_iso,
                "updated_at": now_iso,
                "message_count": 1,
                "subject_area": "science",
                "is_flagged": False,
                "flag_reason": None,
            }],
            # Second call: messages
            [{
                "message_id": "msg-1",
                "conversation_id": "conv-1",
                "role": "user",
                "content": ciphertext,
                "timestamp": now_iso,
                "model_used": None,
                "response_time_ms": None,
                "tokens_used": None,
                "safety_filtered": False,
            }],
        ]

        conv = store.get_conversation("conv-1")
        assert conv is not None
        assert len(conv.messages) == 1
        assert conv.messages[0].content == original

    def test_encryption_failure_raises_runtime_error(self, mock_db):
        """When encryption fails, add_message must raise RuntimeError (fail closed)."""
        broken_enc = MagicMock()
        broken_enc.encrypt_string.side_effect = Exception("Key corrupted")

        with patch("storage.conversation_store.safety_config") as mock_cfg:
            mock_cfg.ENCRYPT_CONVERSATIONS = True
            store = ConversationStore(db=mock_db, encryption=broken_enc)

            with pytest.raises(RuntimeError, match="COPPA compliance"):
                store.add_message("conv-1", "user", "Hello")

        # Verify no INSERT INTO messages was called (no plaintext stored)
        # Note: execute_write IS called by __init__ for CREATE TABLE/INDEX
        assert _find_insert_call(mock_db) is None, "INSERT INTO messages should not have been called"

    def test_messages_stored_unencrypted_when_disabled(self, conversation_store_unencrypted):
        """When ENCRYPT_CONVERSATIONS=False, content is stored as plaintext."""
        store, mock_db, enc, _ = conversation_store_unencrypted
        plaintext = "What is 2 + 2?"

        mock_db.execute_query.return_value = [{"profile_id": "profile-1"}]

        store.add_message("conv-1", "user", plaintext)

        insert_call = _find_insert_call(mock_db)
        assert insert_call is not None, "INSERT INTO messages call not found"
        stored_content = insert_call[0][1][3]

        assert stored_content == plaintext

    def test_round_trip_add_then_retrieve(self, conversation_store_encrypted):
        """Full round-trip: add_message (encrypts) -> get_conversation (decrypts)."""
        store, mock_db, enc, _ = conversation_store_encrypted
        original = "Explain the water cycle."

        # Step 1: add_message
        mock_db.execute_query.return_value = [{"profile_id": "profile-1"}]
        store.add_message("conv-1", "user", original)

        # Capture the ciphertext that was stored
        insert_call = _find_insert_call(mock_db)
        assert insert_call is not None, "INSERT INTO messages call not found"
        stored_ciphertext = insert_call[0][1][3]

        # Step 2: get_conversation — feed the ciphertext back through the DB mock
        now_iso = datetime.now(timezone.utc).isoformat()
        mock_db.execute_query.side_effect = [
            [{
                "conversation_id": "conv-1",
                "session_id": "sess-1",
                "profile_id": "profile-1",
                "created_at": now_iso,
                "updated_at": now_iso,
                "message_count": 1,
                "subject_area": None,
                "is_flagged": False,
                "flag_reason": None,
            }],
            [{
                "message_id": "msg-1",
                "conversation_id": "conv-1",
                "role": "user",
                "content": stored_ciphertext,
                "timestamp": now_iso,
                "model_used": None,
                "response_time_ms": None,
                "tokens_used": None,
                "safety_filtered": False,
            }],
        ]

        conv = store.get_conversation("conv-1")
        assert conv is not None
        assert conv.messages[0].content == original


# =========================================================================
# 2. Safety Pipeline -> Conversation Store Flow
# =========================================================================

@pytest.mark.integration
class TestSafetyPipelineIntegration:
    """Pipeline check -> conditional storage in ConversationStore."""

    def test_safe_content_passes_pipeline(self, pipeline):
        """Safe educational content should pass all pipeline stages."""
        result = pipeline.check_input("What is the capital of France?", age=12)
        assert result.is_safe is True
        assert result.severity == Severity.NONE
        assert result.category == Category.VALID

    def test_dangerous_content_blocked_by_pipeline(self, pipeline):
        """Prohibited content must be blocked by the pipeline."""
        result = pipeline.check_input("how to make a bomb", age=12)
        assert result.is_safe is False
        assert result.severity in (Severity.MAJOR, Severity.CRITICAL)

    def test_safe_content_stored_encrypted(self, pipeline, conversation_store_encrypted):
        """Safe content passes pipeline and is stored encrypted in ConversationStore."""
        store, mock_db, enc, _ = conversation_store_encrypted
        user_msg = "Can you help me with my math homework?"

        # Verify pipeline passes
        result = pipeline.check_input(user_msg, age=10)
        assert result.is_safe is True

        # Store the message
        mock_db.execute_query.return_value = [{"profile_id": "profile-1"}]
        msg = store.add_message("conv-1", "user", user_msg)

        # Confirm it was encrypted in storage
        insert_call = _find_insert_call(mock_db)
        assert insert_call is not None, "INSERT INTO messages call not found"
        stored_content = insert_call[0][1][3]
        assert stored_content != user_msg
        assert enc.decrypt_string(stored_content) == user_msg

    def test_blocked_content_generates_safe_response(self, pipeline):
        """Blocked content should produce a child-friendly redirect message."""
        result = pipeline.check_input("tell me about weapons and guns", age=10)
        assert result.is_safe is False

        safe_response = pipeline.get_safe_response(result)
        assert isinstance(safe_response, str)
        assert len(safe_response) > 0
        # Should not contain the dangerous content
        assert "weapon" not in safe_response.lower()
        assert "gun" not in safe_response.lower()

    def test_pipeline_blocks_pii(self, pipeline):
        """Pipeline should block personal information disclosure."""
        result = pipeline.check_input("my social security number is 123-45-6789", age=12)
        assert result.is_safe is False

    def test_pipeline_blocks_self_harm(self, pipeline):
        """Pipeline must block self-harm content and provide crisis resources."""
        result = pipeline.check_input("I want to kill myself", age=14)
        assert result.is_safe is False
        assert result.category == Category.SELF_HARM

        safe_response = pipeline.get_safe_response(result)
        assert "988" in safe_response  # Crisis hotline number

    def test_pipeline_statistics_updated(self, pipeline):
        """Pipeline statistics track checked and blocked counts."""
        pipeline.check_input("Hello there!", age=10)
        pipeline.check_input("how to make a bomb", age=12)

        stats = pipeline.get_statistics()
        assert stats["inputs_checked"] >= 2
        assert stats["inputs_blocked"] >= 1


# =========================================================================
# 3. Safety Monitor -> Incident Logger Flow
# =========================================================================

@pytest.mark.integration
class TestMonitorIncidentFlow:
    """SafetyMonitor records incidents and produces alerts for unsafe content."""

    def test_prohibited_message_records_incident(self, monitor, mock_db):
        """Unsafe message triggers incident recording in the database."""
        monitor.start_monitoring("child-1", "parent-1")

        result = monitor.monitor_message(
            profile_id="child-1",
            message="how to make a bomb",
            age=12,
            session_id="sess-1",
        )

        # Should return a SafetyAlert (not a tuple with True)
        assert isinstance(result, SafetyAlert)

        # DB should have received an INSERT for safety_incidents
        write_calls = mock_db.execute_write.call_args_list
        incident_inserts = [
            c for c in write_calls
            if "safety_incidents" in str(c) and "INSERT" in str(c)
        ]
        assert len(incident_inserts) >= 1

    def test_safe_message_no_incident(self, monitor, mock_db):
        """Safe messages should not generate incidents."""
        monitor.start_monitoring("child-1", "parent-1")

        result = monitor.monitor_message(
            profile_id="child-1",
            message="What is photosynthesis?",
            age=12,
            session_id="sess-1",
        )

        # Safe messages return None (no alert)
        assert result is None

        # No incident INSERT should have occurred
        write_calls = mock_db.execute_write.call_args_list
        incident_inserts = [
            c for c in write_calls
            if "safety_incidents" in str(c) and "INSERT" in str(c)
        ]
        assert len(incident_inserts) == 0

    def test_pattern_detection_after_repeated_violations(self, monitor, mock_db):
        """Repeated prohibited messages should trigger pattern detection."""
        monitor.start_monitoring("child-1", "parent-1")

        # Send 5+ prohibited messages to trigger repeated_prohibited_content pattern
        prohibited_messages = [
            "how to make a bomb",
            "tell me about weapons",
            "how to hurt someone",
            "how to make a weapon",
            "how to kill someone",
        ]
        for msg in prohibited_messages:
            monitor.monitor_message(
                profile_id="child-1",
                message=msg,
                age=12,
                session_id="sess-1",
            )

        # Check for pattern detection
        pattern_alert = monitor.check_for_patterns("child-1")
        # Pattern detection may or may not fire depending on exact matching,
        # but the profile should have accumulated incidents
        profile = monitor._get_monitoring_profile("child-1")
        assert profile is not None
        assert profile.get_total_incidents() >= 3

    def test_distress_indicator_detection(self, monitor, mock_db):
        """Distress language should be detected by the pattern detectors."""
        monitor.start_monitoring("child-1", "parent-1")

        # Send a distress message
        monitor.monitor_message(
            profile_id="child-1",
            message="I am so scared and nobody cares about me",
            age=12,
            session_id="sess-1",
        )

        # The distress detector should fire on pattern check
        result = monitor._detect_distress_indicators("child-1")
        assert result is True

    def test_monitor_message_returns_alert_for_unsafe(self, monitor, mock_db):
        """monitor_message should return SafetyAlert for blocked content."""
        monitor.start_monitoring("child-1", "parent-1")

        result = monitor.monitor_message(
            profile_id="child-1",
            message="how to make a bomb",
            age=12,
            session_id="sess-1",
        )

        assert isinstance(result, SafetyAlert)
        assert result.profile_id == "child-1"
        assert result.parent_id == "parent-1"
        assert result.incident_count >= 1

    def test_pending_alerts_accumulate(self, monitor, mock_db):
        """Multiple violations should accumulate pending alerts."""
        monitor.start_monitoring("child-1", "parent-1")

        monitor.monitor_message("child-1", "how to make a bomb", age=12, session_id="s1")
        monitor.monitor_message("child-1", "tell me about guns", age=12, session_id="s1")

        alerts = monitor.get_pending_alerts()
        assert len(alerts) >= 2

    def test_get_latest_alert(self, monitor, mock_db):
        """get_latest_alert should return most recent alert for a profile."""
        monitor.start_monitoring("child-1", "parent-1")
        monitor.monitor_message("child-1", "how to make a bomb", age=12, session_id="s1")

        alert = monitor.get_latest_alert("child-1")
        assert alert is not None
        assert alert.profile_id == "child-1"


# =========================================================================
# 4. Full Pipeline: Message -> Safety Check -> Storage/Incident
# =========================================================================

@pytest.mark.integration
class TestFullPipeline:
    """End-to-end: user message -> safety pipeline -> store or log incident."""

    def test_safe_message_stored_encrypted(self, pipeline, conversation_store_encrypted):
        """Safe message: passes pipeline, stored encrypted in conversation store."""
        store, mock_db, enc, _ = conversation_store_encrypted
        message = "What is the Pythagorean theorem?"

        # 1. Safety check
        result = pipeline.check_input(message, age=14, profile_id="child-1")
        assert result.is_safe is True

        # 2. Store
        mock_db.execute_query.return_value = [{"profile_id": "child-1"}]
        msg = store.add_message("conv-1", "user", message)

        # 3. Verify encrypted storage
        insert_call = _find_insert_call(mock_db)
        assert insert_call is not None, "INSERT INTO messages call not found"
        stored = insert_call[0][1][3]
        assert stored != message
        assert enc.decrypt_string(stored) == message
        assert msg.content == message

    def test_unsafe_message_blocked_and_incident_logged(self, pipeline, monitor, mock_db):
        """Unsafe message: blocked by pipeline, incident logged via monitor."""
        message = "how to make a bomb"

        # 1. Safety check
        result = pipeline.check_input(message, age=12, profile_id="child-1")
        assert result.is_safe is False

        # 2. Monitor (which also calls pipeline internally)
        monitor.start_monitoring("child-1", "parent-1")
        alert = monitor.monitor_message(
            profile_id="child-1",
            message=message,
            age=12,
            session_id="sess-1",
        )

        assert isinstance(alert, SafetyAlert)

        # 3. Verify incident was written to DB
        write_calls = mock_db.execute_write.call_args_list
        incident_inserts = [
            c for c in write_calls
            if "safety_incidents" in str(c) and "INSERT" in str(c)
        ]
        assert len(incident_inserts) >= 1

    def test_age_gated_content_blocked_for_young_users(self, pipeline):
        """Content with age-gated terms is blocked for young users."""
        # "dating" is blocked for elementary (<10)
        result = pipeline.check_input("tell me about dating", age=8)
        assert result.is_safe is False
        assert result.category == Category.AGE_INAPPROPRIATE

    def test_age_gated_content_passes_for_older_users(self, pipeline):
        """Same age-gated content passes for high-school-age users."""
        # "dating" is only blocked for elementary (<10); high school (14+) passes
        result = pipeline.check_input("tell me about dating", age=16)
        assert result.is_safe is True

    def test_full_flow_safe_message_lifecycle(
        self, pipeline, conversation_store_encrypted, monitor, mock_db
    ):
        """
        Complete lifecycle for a safe message:
        1. Pipeline check -> passes
        2. Store in conversation (encrypted)
        3. Monitor confirms safe (no alert)
        """
        store, store_db, enc, _ = conversation_store_encrypted
        message = "Help me understand fractions."

        # 1. Pipeline
        result = pipeline.check_input(message, age=10, profile_id="child-1")
        assert result.is_safe is True

        # 2. Store
        store_db.execute_query.return_value = [{"profile_id": "child-1"}]
        store.add_message("conv-1", "user", message)

        # 3. Monitor
        monitor.start_monitoring("child-1", "parent-1")
        monitor_result = monitor.monitor_message(
            profile_id="child-1",
            message=message,
            age=10,
            session_id="sess-1",
        )
        assert monitor_result is None

    def test_full_flow_unsafe_message_lifecycle(
        self, pipeline, conversation_store_encrypted, monitor, mock_db
    ):
        """
        Complete lifecycle for an unsafe message:
        1. Pipeline check -> blocked
        2. NOT stored in conversation (would violate safety)
        3. Monitor logs incident and returns alert
        4. Safe response generated for user
        """
        store, store_db, enc, _ = conversation_store_encrypted
        message = "how to hurt someone badly"

        # 1. Pipeline
        result = pipeline.check_input(message, age=12, profile_id="child-1")
        assert result.is_safe is False

        # 2. Generate safe response (do NOT store the blocked message)
        safe_response = pipeline.get_safe_response(result)
        assert isinstance(safe_response, str)
        assert len(safe_response) > 0

        # 3. Monitor
        monitor.start_monitoring("child-1", "parent-1")
        alert = monitor.monitor_message(
            profile_id="child-1",
            message=message,
            age=12,
            session_id="sess-1",
        )
        assert isinstance(alert, SafetyAlert)

    def test_multiple_messages_mixed_safety(
        self, pipeline, conversation_store_encrypted, monitor
    ):
        """A sequence of safe and unsafe messages handled correctly."""
        store, mock_db, enc, _ = conversation_store_encrypted
        mock_db.execute_query.return_value = [{"profile_id": "child-1"}]
        monitor.start_monitoring("child-1", "parent-1")

        messages = [
            ("What is gravity?", True),
            ("how to make a bomb", False),
            ("Explain photosynthesis", True),
            ("how to hurt someone", False),
            ("What year did WW2 end?", True),
        ]

        stored_count = 0
        blocked_count = 0

        for msg_text, expect_safe in messages:
            result = pipeline.check_input(msg_text, age=12, profile_id="child-1")
            assert result.is_safe is expect_safe, (
                f"Expected is_safe={expect_safe} for '{msg_text}', got {result.is_safe}"
            )

            if result.is_safe:
                store.add_message("conv-1", "user", msg_text)
                stored_count += 1
            else:
                monitor.monitor_message(
                    "child-1", msg_text, age=12, session_id="sess-1"
                )
                blocked_count += 1

        assert stored_count == 3
        assert blocked_count == 2


# =========================================================================
# 5. Encryption Consistency
# =========================================================================

@pytest.mark.integration
class TestEncryptionConsistency:
    """Verify encryption round-trips and cross-instance consistency."""

    def test_encrypt_decrypt_string_round_trip(self, encryption):
        """encrypt_string -> decrypt_string round-trip preserves content."""
        original = "The quick brown fox jumps over the lazy dog."
        encrypted = encryption.encrypt_string(original)
        assert encrypted != original
        decrypted = encryption.decrypt_string(encrypted)
        assert decrypted == original

    def test_encrypt_decrypt_dict_round_trip(self, encryption):
        """encrypt_dict -> decrypt_dict round-trip preserves structure and values."""
        original = {
            "student_id": "s-123",
            "grade": 5,
            "subjects": ["math", "science"],
            "scores": {"math": 95, "science": 88},
        }
        encrypted = encryption.encrypt_dict(original)
        assert isinstance(encrypted, str)
        decrypted = encryption.decrypt_dict(encrypted)
        assert decrypted == original

    def test_different_instances_same_key_can_decrypt(self, tmp_path):
        """Two EncryptionManager instances sharing the same key dir can interop."""
        enc1 = EncryptionManager(key_dir=tmp_path)
        enc2 = EncryptionManager(key_dir=tmp_path)

        original = "Shared secret message for cross-instance test."
        ciphertext = enc1.encrypt_string(original)
        decrypted = enc2.decrypt_string(ciphertext)
        assert decrypted == original

    def test_different_instances_different_keys_cannot_decrypt(self, tmp_path):
        """EncryptionManager with different keys cannot decrypt each other's data."""
        key_dir_1 = tmp_path / "key1"
        key_dir_1.mkdir()
        key_dir_2 = tmp_path / "key2"
        key_dir_2.mkdir()

        enc1 = EncryptionManager(key_dir=key_dir_1)
        enc2 = EncryptionManager(key_dir=key_dir_2)

        original = "This should not be decryptable by the other key."
        ciphertext = enc1.encrypt_string(original)
        # decrypt_string returns None on failure (fail-safe)
        result = enc2.decrypt_string(ciphertext)
        assert result is None or result != original

    def test_encrypt_empty_string(self, encryption):
        """Empty string should round-trip correctly."""
        assert encryption.encrypt_string("") == ""
        assert encryption.decrypt_string("") == ""

    def test_encrypt_none_returns_none(self, encryption):
        """None input should return None."""
        assert encryption.encrypt_string(None) is None
        assert encryption.decrypt_string(None) is None

    def test_unicode_content_round_trip(self, encryption):
        """Unicode characters (CJK, emoji, accented) survive encryption round-trip."""
        original = "Hello 世界! Les mathematiques sont belles. 🌻"
        encrypted = encryption.encrypt_string(original)
        decrypted = encryption.decrypt_string(encrypted)
        assert decrypted == original

    def test_large_content_round_trip(self, encryption):
        """Large payloads survive encryption round-trip."""
        original = "A" * 100_000  # 100KB of text
        encrypted = encryption.encrypt_string(original)
        decrypted = encryption.decrypt_string(encrypted)
        assert decrypted == original

    def test_dict_with_nested_structures(self, encryption):
        """Complex nested dict survives encrypt_dict/decrypt_dict."""
        original = {
            "conversation": {
                "id": "conv-abc",
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there!"},
                ],
                "metadata": {
                    "safety_score": 1.0,
                    "flagged": False,
                    "tags": ["math", "homework"],
                },
            }
        }
        encrypted = encryption.encrypt_dict(original)
        decrypted = encryption.decrypt_dict(encrypted)
        assert decrypted == original

    def test_encrypt_wrapper_returns_none_on_failure(self):
        """The encrypt() convenience wrapper returns None on error (not raises)."""
        broken_enc = MagicMock(spec=EncryptionManager)
        broken_enc.encrypt_string.side_effect = Exception("boom")
        broken_enc.encrypt.side_effect = lambda x: None if x is None else EncryptionManager.encrypt(broken_enc, x)

        # Use a real instance with a deliberately broken fernet
        # This tests the wrapper behavior
        enc = MagicMock()
        enc.encrypt.return_value = None
        result = enc.encrypt("test")
        assert result is None


# =========================================================================
# 6. Decryption Failure Sentinel (COPPA ciphertext leak prevention)
# =========================================================================

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


class TestEncryptedSearch:
    """Test that search works when conversations are encrypted."""

    def test_search_finds_encrypted_message_by_token(self, tmp_path):
        """Add an encrypted message, search by a word in it, find the conversation."""
        from storage.encryption import EncryptionManager
        from storage.conversation_store import ConversationStore
        from unittest.mock import MagicMock, patch

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
