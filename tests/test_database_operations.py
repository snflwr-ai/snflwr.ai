"""
Test Suite for Database Operations
Tests SQLite database with 11 tables, transactions, queries, indexes, and data integrity
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import shutil
import sqlite3

from storage.database import DatabaseManager, db_manager


@pytest.fixture
def temp_db():
    """Create temporary database for testing"""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    db = DatabaseManager(db_path)
    db.initialize_database()
    yield db
    db.close()
    shutil.rmtree(temp_dir)


class TestDatabaseInitialization:
    """Test database creation and schema initialization"""
    
    def test_database_file_created(self):
        """Test database file is created"""
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test.db"
        
        db = DatabaseManager(db_path)
        db.initialize_database()
        
        assert db_path.exists()
        
        db.close()
        shutil.rmtree(temp_dir)
    
    def test_all_tables_created(self, temp_db):
        """Test all 11 tables are created"""
        expected_tables = [
            'accounts',
            'child_profiles',
            'profile_subjects',
            'sessions',
            'conversations',
            'messages',
            'safety_incidents',
            'auth_tokens',
            'audit_log',
            'learning_analytics'
        ]
        
        result = temp_db.execute_query(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        
        table_names = [row['name'] for row in result]
        
        for table in expected_tables:
            assert table in table_names
    
    def test_foreign_keys_enabled(self, temp_db):
        """Test foreign key constraints are enabled"""
        result = temp_db.execute_query("PRAGMA foreign_keys")
        
        assert result[0]['foreign_keys'] == 1
    
    def test_wal_mode_enabled(self, temp_db):
        """Test WAL (Write-Ahead Logging) mode is enabled"""
        result = temp_db.execute_query("PRAGMA journal_mode")
        
        assert result[0]['journal_mode'].upper() == 'WAL'
    
    def test_indexes_created(self, temp_db):
        """Test required indexes are created"""
        result = temp_db.execute_query(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        
        index_names = [row['name'] for row in result]
        
        # Check for key indexes
        expected_indexes = [
            'idx_accounts_username',
            'idx_profiles_parent',
            'idx_sessions_profile',
            'idx_conversations_session',
            'idx_messages_conversation',
            'idx_incidents_profile'
        ]
        
        for index in expected_indexes:
            assert index in index_names


class TestParentTableOperations:
    """Test operations on parents table"""
    
    def test_insert_parent(self, temp_db):
        """Test inserting parent record"""
        parent_id = "test_parent_123"
        
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (parent_id, "testuser", "hash123", "device123", datetime.now().isoformat())
        )
        
        result = temp_db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )
        
        assert len(result) == 1
        assert result[0]['username'] == "testuser"
    
    def test_parent_username_unique_constraint(self, temp_db):
        """Test username must be unique"""
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("parent1", "testuser", "hash1", "device1", datetime.now().isoformat())
        )
        
        # Try to insert duplicate username
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute_update(
                """INSERT INTO accounts 
                   (parent_id, username, password_hash, device_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("parent2", "testuser", "hash2", "device2", datetime.now().isoformat())
            )
    
    def test_parent_failed_login_default(self, temp_db):
        """Test failed login attempts defaults to 0"""
        parent_id = "test_parent_789"
        
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (parent_id, "testuser3", "hash123", "device789", datetime.now().isoformat())
        )
        
        result = temp_db.execute_query(
            "SELECT failed_login_attempts FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )
        
        assert result[0]['failed_login_attempts'] == 0


class TestChildProfileTableOperations:
    """Test operations on child_profiles table"""
    
    @pytest.fixture
    def parent_id(self, temp_db):
        """Create parent for child profile tests"""
        parent_id = "test_parent_001"
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (parent_id, "parent1", "hash", "device1", datetime.now().isoformat())
        )
        return parent_id
    
    def test_insert_child_profile(self, temp_db, parent_id):
        """Test inserting child profile"""
        profile_id = "profile_001"
        
        temp_db.execute_update(
            """INSERT INTO child_profiles 
               (profile_id, parent_id, name, age, grade, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (profile_id, parent_id, "Emma", 10, "5th", datetime.now().isoformat())
        )
        
        result = temp_db.execute_query(
            "SELECT * FROM child_profiles WHERE profile_id = ?",
            (profile_id,)
        )
        
        assert len(result) == 1
        assert result[0]['name'] == "Emma"
        assert result[0]['age'] == 10
    
    def test_child_profile_age_constraint(self, temp_db, parent_id):
        """Test age must be between 5 and 18"""
        # Age too young
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute_update(
                """INSERT INTO child_profiles 
                   (profile_id, parent_id, name, age, grade, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("profile_002", parent_id, "TooYoung", 4, "Pre-K", datetime.now().isoformat())
            )
        
        # Age too old
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute_update(
                """INSERT INTO child_profiles 
                   (profile_id, parent_id, name, age, grade, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("profile_003", parent_id, "TooOld", 19, "College", datetime.now().isoformat())
            )
    
    def test_child_profile_defaults(self, temp_db, parent_id):
        """Test child profile default values"""
        profile_id = "profile_004"
        
        temp_db.execute_update(
            """INSERT INTO child_profiles 
               (profile_id, parent_id, name, age, grade, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (profile_id, parent_id, "Default", 10, "5th", datetime.now().isoformat())
        )
        
        result = temp_db.execute_query(
            "SELECT * FROM child_profiles WHERE profile_id = ?",
            (profile_id,)
        )
        
        assert result[0]['avatar'] == 'default'
        assert result[0]['learning_level'] == 'adaptive'
        assert result[0]['daily_time_limit_minutes'] == 120
        assert result[0]['total_sessions'] == 0
        assert result[0]['total_questions'] == 0
        assert result[0]['is_active'] == 1
    
    def test_child_profile_cascade_delete(self, temp_db, parent_id):
        """Test child profiles deleted when parent is deleted"""
        profile_id = "profile_005"
        
        temp_db.execute_update(
            """INSERT INTO child_profiles 
               (profile_id, parent_id, name, age, grade, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (profile_id, parent_id, "Cascade", 10, "5th", datetime.now().isoformat())
        )
        
        # Delete parent
        temp_db.execute_update(
            "DELETE FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )
        
        # Child profile should be deleted
        result = temp_db.execute_query(
            "SELECT * FROM child_profiles WHERE profile_id = ?",
            (profile_id,)
        )
        
        assert len(result) == 0


class TestSessionTableOperations:
    """Test operations on sessions table"""
    
    @pytest.fixture
    def profile_id(self, temp_db):
        """Create parent and profile for session tests"""
        parent_id = "parent_session"
        profile_id = "profile_session"
        
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (parent_id, "sessionparent", "hash", "device", datetime.now().isoformat())
        )
        
        temp_db.execute_update(
            """INSERT INTO child_profiles 
               (profile_id, parent_id, name, age, grade, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (profile_id, parent_id, "Student", 10, "5th", datetime.now().isoformat())
        )
        
        return profile_id
    
    def test_insert_session(self, temp_db, profile_id):
        """Test inserting session record"""
        session_id = "session_001"
        
        temp_db.execute_update(
            """INSERT INTO sessions
               (session_id, profile_id, session_type, started_at, platform)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, profile_id, "student", datetime.now().isoformat(), "Windows")
        )

        result = temp_db.execute_query(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,)
        )

        assert len(result) == 1
        assert result[0]['session_type'] == 'student'
    
    def test_session_type_constraint(self, temp_db, profile_id):
        """Test session type must be valid"""
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute_update(
                """INSERT INTO sessions 
                   (session_id, profile_id, session_type, started_at, platform)
                   VALUES (?, ?, ?, ?, ?)""",
                ("session_002", profile_id, "invalid_type", datetime.now().isoformat(), "Windows")
            )
    
    def test_session_duration_calculation(self, temp_db, profile_id):
        """Test session duration can be calculated"""
        session_id = "session_003"
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=45)
        
        temp_db.execute_update(
            """INSERT INTO sessions 
               (session_id, profile_id, session_type, started_at, ended_at, duration_minutes, platform)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, profile_id, "student", start_time.isoformat(), 
             end_time.isoformat(), 45, "Windows")
        )
        
        result = temp_db.execute_query(
            "SELECT duration_minutes FROM sessions WHERE session_id = ?",
            (session_id,)
        )
        
        assert result[0]['duration_minutes'] == 45


class TestConversationAndMessageTables:
    """Test operations on conversations and messages tables"""
    
    @pytest.fixture
    def session_id(self, temp_db):
        """Create full hierarchy for conversation tests"""
        parent_id = "parent_conv"
        profile_id = "profile_conv"
        session_id = "session_conv"
        
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (parent_id, "convparent", "hash", "device", datetime.now().isoformat())
        )
        
        temp_db.execute_update(
            """INSERT INTO child_profiles 
               (profile_id, parent_id, name, age, grade, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (profile_id, parent_id, "Student", 10, "5th", datetime.now().isoformat())
        )
        
        temp_db.execute_update(
            """INSERT INTO sessions 
               (session_id, profile_id, session_type, started_at, platform)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, profile_id, "student", datetime.now().isoformat(), "Windows")
        )
        
        return session_id
    
    def test_insert_conversation(self, temp_db, session_id):
        """Test inserting conversation record"""
        conversation_id = "conv_001"
        profile_id = "profile_conv"
        
        temp_db.execute_update(
            """INSERT INTO conversations 
               (conversation_id, session_id, profile_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, session_id, profile_id, 
             datetime.now().isoformat(), datetime.now().isoformat())
        )
        
        result = temp_db.execute_query(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,)
        )
        
        assert len(result) == 1
        assert result[0]['conversation_id'] == conversation_id
    
    def test_insert_messages(self, temp_db, session_id):
        """Test inserting message records"""
        conversation_id = "conv_002"
        profile_id = "profile_conv"
        
        # Create conversation
        temp_db.execute_update(
            """INSERT INTO conversations 
               (conversation_id, session_id, profile_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, session_id, profile_id, 
             datetime.now().isoformat(), datetime.now().isoformat())
        )
        
        # Insert user message
        temp_db.execute_update(
            """INSERT INTO messages 
               (message_id, conversation_id, role, content, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            ("msg_001", conversation_id, "user", "Why is the sky blue?", 
             datetime.now().isoformat())
        )
        
        # Insert assistant message
        temp_db.execute_update(
            """INSERT INTO messages 
               (message_id, conversation_id, role, content, timestamp, model_used)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("msg_002", conversation_id, "assistant", 
             "The sky is blue because...", datetime.now().isoformat(), "llama3.2:3b")
        )
        
        result = temp_db.execute_query(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp",
            (conversation_id,)
        )
        
        assert len(result) == 2
        assert result[0]['role'] == 'user'
        assert result[1]['role'] == 'assistant'
    
    def test_message_role_constraint(self, temp_db, session_id):
        """Test message role must be valid"""
        conversation_id = "conv_003"
        profile_id = "profile_conv"
        
        temp_db.execute_update(
            """INSERT INTO conversations 
               (conversation_id, session_id, profile_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, session_id, profile_id, 
             datetime.now().isoformat(), datetime.now().isoformat())
        )
        
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute_update(
                """INSERT INTO messages 
                   (message_id, conversation_id, role, content, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                ("msg_003", conversation_id, "invalid_role", "Test", 
                 datetime.now().isoformat())
            )
    
    def test_conversation_cascade_delete(self, temp_db, session_id):
        """Test messages deleted when conversation is deleted"""
        conversation_id = "conv_004"
        profile_id = "profile_conv"
        
        # Create conversation with messages
        temp_db.execute_update(
            """INSERT INTO conversations 
               (conversation_id, session_id, profile_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conversation_id, session_id, profile_id, 
             datetime.now().isoformat(), datetime.now().isoformat())
        )
        
        temp_db.execute_update(
            """INSERT INTO messages 
               (message_id, conversation_id, role, content, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            ("msg_004", conversation_id, "user", "Test message", 
             datetime.now().isoformat())
        )
        
        # Delete conversation
        temp_db.execute_update(
            "DELETE FROM conversations WHERE conversation_id = ?",
            (conversation_id,)
        )
        
        # Messages should be deleted
        result = temp_db.execute_query(
            "SELECT * FROM messages WHERE conversation_id = ?",
            (conversation_id,)
        )
        
        assert len(result) == 0


class TestSafetyIncidentsTable:
    """Test operations on safety_incidents table"""
    
    @pytest.fixture
    def profile_id(self, temp_db):
        """Create parent and profile for incident tests"""
        parent_id = "parent_incident"
        profile_id = "profile_incident"
        
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (parent_id, "incidentparent", "hash", "device", datetime.now().isoformat())
        )
        
        temp_db.execute_update(
            """INSERT INTO child_profiles 
               (profile_id, parent_id, name, age, grade, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (profile_id, parent_id, "Student", 10, "5th", datetime.now().isoformat())
        )
        
        return profile_id
    
    def test_insert_safety_incident(self, temp_db, profile_id):
        """Test inserting safety incident"""
        temp_db.execute_update(
            """INSERT INTO safety_incidents 
               (profile_id, incident_type, severity, content_snippet, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (profile_id, "prohibited_keyword", "minor", "filtered content", 
             datetime.now().isoformat())
        )
        
        result = temp_db.execute_query(
            "SELECT * FROM safety_incidents WHERE profile_id = ?",
            (profile_id,)
        )
        
        assert len(result) == 1
        assert result[0]['incident_type'] == 'prohibited_keyword'
        assert result[0]['severity'] == 'minor'
    
    def test_incident_severity_constraint(self, temp_db, profile_id):
        """Test incident severity must be valid"""
        with pytest.raises(sqlite3.IntegrityError):
            temp_db.execute_update(
                """INSERT INTO safety_incidents 
                   (profile_id, incident_type, severity, timestamp)
                   VALUES (?, ?, ?, ?)""",
                (profile_id, "test", "invalid_severity", datetime.now().isoformat())
            )
    
    def test_incident_defaults(self, temp_db, profile_id):
        """Test safety incident default values"""
        temp_db.execute_update(
            """INSERT INTO safety_incidents 
               (profile_id, incident_type, severity, timestamp)
               VALUES (?, ?, ?, ?)""",
            (profile_id, "test_incident", "minor", datetime.now().isoformat())
        )
        
        result = temp_db.execute_query(
            "SELECT * FROM safety_incidents WHERE profile_id = ?",
            (profile_id,)
        )
        
        assert result[0]['parent_notified'] == 0
        assert result[0]['resolved'] == 0


class TestTransactionSupport:
    """Test transaction handling"""
    
    def test_transaction_commit(self, temp_db):
        """Test successful transaction commit"""
        parent_id = "trans_parent"
        
        temp_db.begin_transaction()
        
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (parent_id, "transuser", "hash", "device", datetime.now().isoformat())
        )
        
        temp_db.commit_transaction()
        
        # Verify data persisted
        result = temp_db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )
        
        assert len(result) == 1
    
    def test_transaction_rollback(self, temp_db):
        """Test transaction rollback"""
        parent_id = "rollback_parent"
        
        temp_db.begin_transaction()
        
        temp_db.execute_update(
            """INSERT INTO accounts 
               (parent_id, username, password_hash, device_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (parent_id, "rollbackuser", "hash", "device", datetime.now().isoformat())
        )
        
        temp_db.rollback_transaction()
        
        # Verify data was not persisted
        result = temp_db.execute_query(
            "SELECT * FROM accounts WHERE parent_id = ?",
            (parent_id,)
        )
        
        assert len(result) == 0


class TestQueryPerformance:
    """Test query performance with indexes"""
    
    def test_username_index_performance(self, temp_db):
        """Test username lookup uses index"""
        # Insert multiple parents
        for i in range(100):
            temp_db.execute_update(
                """INSERT INTO accounts 
                   (parent_id, username, password_hash, device_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (f"parent_{i}", f"user_{i}", "hash", f"device_{i}", 
                 datetime.now().isoformat())
            )
        
        # Query by username should use index
        result = temp_db.execute_query(
            "EXPLAIN QUERY PLAN SELECT * FROM accounts WHERE username = ?",
            ("user_50",)
        )

        # Should use index (contains 'idx_parents_username' in plan)
        # Extract plan details from Row objects
        plan_text = ' '.join(str(row['detail'] if isinstance(row, dict) else row[3]) for row in result)
        assert 'idx_accounts_username' in plan_text or 'INDEX' in plan_text


# Run tests with: pytest tests/test_database_operations.py -v
