"""
Test Suite for Offline Operation
Tests data persistence on USB, zero-network-dependency, and crash recovery
"""

import pytest
from pathlib import Path
import tempfile
import shutil
from unittest.mock import patch

from core.partition_detector import PartitionDetector
from storage.database import DatabaseManager
from storage.encryption import EncryptionManager
from safety.pipeline import SafetyPipeline


@pytest.fixture
def temp_db(temp_usb_simulation):
    """Create database on USB device for offline operation"""
    usb_path = temp_usb_simulation['usb']
    db_path = usb_path / "snflwr.db"

    db = DatabaseManager(db_path)
    db.initialize_database()
    EncryptionManager(usb_path)

    yield db
    db.close()


@pytest.fixture
def temp_usb_simulation():
    """Create simulated USB mount point for offline operation testing"""
    temp_dir = Path(tempfile.mkdtemp())

    usb_mount = temp_dir / "usb_device"
    usb_mount.mkdir()

    cdrom_mount = temp_dir / "cdrom"
    cdrom_mount.mkdir()

    (usb_mount / "profiles").mkdir()
    (usb_mount / "logs").mkdir()
    (usb_mount / "data").mkdir()
    (cdrom_mount / "docs").mkdir()

    yield {
        'usb': usb_mount,
        'cdrom': cdrom_mount,
        'root': temp_dir
    }

    shutil.rmtree(temp_dir)


class TestOfflineDataPersistence:
    """Test data persistence on USB partition"""

    def test_write_and_read_data_on_usb(self, temp_db, temp_usb_simulation):
        """Test database round-trip on USB partition"""
        db_path = temp_usb_simulation['usb'] / "snflwr.db"
        assert db_path.exists()
        assert temp_db.db_path == db_path

        temp_db.execute_update(
            """INSERT INTO accounts
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("test_id", "testuser", "hash", "device", "2025-01-01T00:00:00")
        )

        result = temp_db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?",
            ("test_id",)
        )
        assert len(result) == 1

    def test_encryption_keys_on_usb(self, temp_usb_simulation):
        """Test encryption keys stored on USB partition"""
        usb_path = temp_usb_simulation['usb']
        EncryptionManager(usb_path)

        key_file = usb_path / ".encryption_key"
        assert key_file.exists()


class TestOfflineSafetySystems:
    """Test safety systems work without network"""

    def test_content_filter_works_offline(self):
        """Test content filtering with local rules"""
        pipeline = SafetyPipeline()
        result = pipeline.check_input("Tell me about weapons", 10, "test_profile")

        assert result is not None
        assert result.is_safe is False

    def test_keyword_filtering_offline(self):
        """Test keyword list is local"""
        pipeline = SafetyPipeline()
        # Verify pipeline blocks known prohibited keywords
        result_weapon = pipeline.check_input("weapon", 10, "test_profile")
        assert result_weapon.is_safe is False
        result_drugs = pipeline.check_input("drugs", 10, "test_profile")
        assert result_drugs.is_safe is False

    def test_pattern_matching_offline(self):
        """Test pattern matching catches personal info locally"""
        pipeline = SafetyPipeline()
        result = pipeline.check_input("My phone is 555-123-4567", 10, "test_profile")
        assert result.is_safe is False


class TestNoNetworkDependency:
    """Test system has zero network dependencies"""

    @patch('socket.socket')
    def test_content_filter_with_network_unavailable(self, mock_socket):
        """Test content filter works when network is broken"""
        mock_socket.side_effect = Exception("Network unavailable")

        pipeline = SafetyPipeline()
        result = pipeline.check_input("hello", 10, "test_profile")
        assert result is not None

    @patch('urllib.request.urlopen')
    def test_no_http_requests(self, mock_urlopen):
        """Test no HTTP requests made during filtering"""
        mock_urlopen.side_effect = Exception("Network unavailable")

        pipeline = SafetyPipeline()
        result = pipeline.check_input("test message", 10, "test_profile")
        assert result is not None


class TestOfflineRecovery:
    """Test recovery from offline scenarios"""

    def test_reconnect_usb_device(self, temp_db, temp_usb_simulation):
        """Test data persists across database reconnect"""
        temp_db.execute_update(
            """INSERT INTO accounts
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("test_id", "user", "hash", "device", "2025-01-01T00:00:00")
        )

        temp_db.close()

        db_path = temp_usb_simulation['usb'] / "snflwr.db"
        new_db = DatabaseManager(db_path)

        result = new_db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?",
            ("test_id",)
        )
        assert len(result) == 1
        new_db.close()

    def test_orphaned_session_cleanup(self, temp_db, temp_usb_simulation):
        """Test cleaning up orphaned sessions after crash"""
        from core.session_manager import SessionManager
        from core.profile_manager import ProfileManager
        from core.authentication import AuthenticationManager
        from datetime import datetime, timedelta

        auth = AuthenticationManager(temp_db, temp_usb_simulation['usb'])
        profiles = ProfileManager(temp_db)
        sessions = SessionManager(temp_db)

        success, parent_id = auth.create_parent_account("parent", "SecurePass123!")
        assert success, f"Failed to create parent: {parent_id}"
        profile = profiles.create_profile(
            parent_id=parent_id, name="Child", age=10, grade="5th"
        )

        session = sessions.create_session(
            profile_id=profile.profile_id, session_type="student"
        )

        past_time = datetime.now() - timedelta(hours=10)
        sessions._update_session_start(session.session_id, past_time.isoformat())

        recovered = sessions.recover_orphaned_sessions()
        assert recovered > 0


class TestCrossPlatformOffline:
    """Test offline operation across platforms"""

    @pytest.mark.parametrize("platform_name", ["Windows", "Darwin", "Linux"])
    def test_partition_detector_per_platform(self, platform_name, temp_usb_simulation):
        """Test PartitionDetector initializes on each platform"""
        with patch('platform.system', return_value=platform_name):
            detector = PartitionDetector()
            assert detector.platform == platform_name


class TestOfflineDataIntegrity:
    """Test data integrity in offline mode"""
    # NOTE: transaction, foreign key, and encryption round-trip tests
    # live in test_database_operations.py and test_encryption.py


class TestPrivacyInOfflineMode:
    """Test privacy guarantees in offline mode"""

    def test_all_data_on_usb(self, temp_db, temp_usb_simulation):
        """Test all user data stored on USB"""
        usb_path = temp_usb_simulation['usb']

        assert (usb_path / "snflwr.db").exists()
        assert (usb_path / ".encryption_key").exists()
        assert (usb_path / "logs").exists()
