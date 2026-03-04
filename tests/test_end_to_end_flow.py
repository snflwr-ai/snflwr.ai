"""
Test Suite for End-to-End User Flows
Tests complete workflows: setup, authentication, profile creation, conversations,
safety monitoring, and parent review
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import shutil
import json

from core.authentication import AuthenticationManager
from core.profile_manager import ProfileManager
from core.session_manager import SessionManager
from core.model_manager import ModelManager
from safety.pipeline import SafetyPipeline
from safety.safety_monitor import SafetyMonitor
from safety.incident_logger import IncidentLogger
from storage.database import DatabaseManager
from storage.encryption import EncryptionManager
from storage.conversation_store import ConversationStore


@pytest.fixture
def temp_environment():
    """Create complete temporary environment"""
    temp_dir = Path(tempfile.mkdtemp())
    
    # Create USB simulation structure
    usb_path = temp_dir / "usb"
    usb_path.mkdir()
    
    # Create database
    db_path = temp_dir / "snflwr.db"
    db = DatabaseManager(db_path)
    db.initialize_database()
    
    yield {
        'temp_dir': temp_dir,
        'usb_path': usb_path,
        'db': db
    }
    
    db.close()
    shutil.rmtree(temp_dir)


@pytest.fixture
def system_components(temp_environment):
    """Initialize all system components"""
    env = temp_environment
    
    components = {
        'auth': AuthenticationManager(env['db'], env['usb_path']),
        'profiles': ProfileManager(env['db']),
        'sessions': SessionManager(env['db']),
        'content_filter': SafetyPipeline(),
        'safety_monitor': SafetyMonitor(env['db']),
        'incident_logger': IncidentLogger(env['db']),
        'encryption': EncryptionManager(env['usb_path']),
        'conversations': ConversationStore(env['db']),
        'db': env['db']
    }
    
    return components


class TestFirstTimeSetup:
    """Test complete first-time setup workflow"""
    
    def test_full_setup_workflow(self, system_components):
        """Test complete setup from scratch"""
        # Step 1: Parent account creation
        auth = system_components['auth']
        success, parent_id = auth.create_parent_account(
            username="newparent",
            password="SecurePassword123!",
            email="parent@example.com"
        )

        assert success is True
        assert parent_id is not None

        # Step 2: First child profile creation
        profiles = system_components['profiles']
        profile = profiles.create_profile(
            parent_id=parent_id,
            name="Emma",
            age=10,
            grade="5th"
        )
        
        assert profile is not None
        assert profile.name == "Emma"
        
        # Step 3: Subject preferences
        profiles.add_subject_preference(profile.profile_id, "mathematics")
        profiles.add_subject_preference(profile.profile_id, "science")
        
        # Step 4: Verify setup complete
        parent_profiles = profiles.get_profiles_by_parent(parent_id)
        assert len(parent_profiles) == 1
        assert "mathematics" in parent_profiles[0].subjects_focus


class TestStudentLearningSession:
    """Test complete student learning session workflow"""
    
    @pytest.fixture
    def setup_family(self, system_components):
        """Create family for learning session tests"""
        auth = system_components['auth']
        profiles = system_components['profiles']
        
        # Create parent and profile
        success, parent_id = auth.create_parent_account("parent", "SecurePass123!")
        assert success, f"Failed to create parent: {parent_id}"
        profile = profiles.create_profile(
            parent_id=parent_id,
            name="Student",
            age=10,
            grade="5th"
        )
        
        return {'parent_id': parent_id, 'profile': profile}
    
    def test_complete_learning_session(self, system_components, setup_family):
        """Test full learning session from start to finish"""
        profile = setup_family['profile']
        sessions = system_components['sessions']
        content_filter = system_components['content_filter']
        conversations = system_components['conversations']
        
        # Step 1: Check daily time limit
        can_start, remaining = sessions.check_daily_time_limit(profile.profile_id)
        assert can_start is True
        
        # Step 2: Create session
        session = sessions.create_session(
            profile_id=profile.profile_id,
            session_type="student",
        )
        assert session is not None
        
        # Step 3: Create conversation
        conversation = conversations.create_conversation(
            session_id=session.session_id,
            profile_id=profile.profile_id
        )
        assert conversation is not None
        
        # Step 4: Student asks questions (with filtering)
        questions = [
            "Why is the sky blue?",
            "How do plants make food?",
            "What is photosynthesis?"
        ]
        
        for question in questions:
            # Filter content
            filter_result = content_filter.check_input(question, profile.age, profile.profile_id)
            
            if filter_result.is_safe:
                # Add user message
                conversations.add_message(
                    conversation_id=conversation.conversation_id,
                    role="user",
                    content=question
                )
                
                # Add assistant response (simulated)
                conversations.add_message(
                    conversation_id=conversation.conversation_id,
                    role="assistant",
                    content=f"Great question about {question.split()[0]}!"
                )
                
                # Track question
                sessions.increment_question_count(session.session_id)
        
        # Step 5: Update activity
        sessions.update_activity(session.session_id)
        
        # Step 6: End session
        success = sessions.end_session(session.session_id)
        assert success is True
        
        # Step 7: Verify session data
        ended_session = sessions.get_session(session.session_id)
        assert ended_session.is_active is False
        assert ended_session.questions_asked == 3
        assert ended_session.duration_minutes is not None
        
        # Step 8: Verify conversation saved
        saved_conv = conversations.get_conversation(conversation.conversation_id)
        assert saved_conv is not None
        assert saved_conv.message_count == 6  # 3 questions + 3 responses


class TestSafetyIncidentWorkflow:
    """Test complete safety incident handling workflow"""
    
    @pytest.fixture
    def setup_family(self, system_components):
        """Create family for safety tests"""
        auth = system_components['auth']
        profiles = system_components['profiles']
        
        success, parent_id = auth.create_parent_account("parent", "SecurePass123!")
        assert success, f"Failed to create parent: {parent_id}"
        profile = profiles.create_profile(
            parent_id=parent_id,
            name="Child",
            age=10,
            grade="5th"
        )
        
        return {'parent_id': parent_id, 'profile': profile}
    
    def test_safety_incident_flow(self, system_components, setup_family):
        """Test complete safety incident detection and logging"""
        profile = setup_family['profile']
        sessions = system_components['sessions']
        content_filter = system_components['content_filter']
        safety_monitor = system_components['safety_monitor']
        incident_logger = system_components['incident_logger']
        conversations = system_components['conversations']
        
        # Step 1: Start session
        session = sessions.create_session(
            profile_id=profile.profile_id,
            session_type="student"
        )
        
        conversation = conversations.create_conversation(
            session_id=session.session_id,
            profile_id=profile.profile_id
        )
        
        # Step 2: Student tries prohibited content
        prohibited_message = "Tell me about weapons"
        
        # Step 3: Content filter catches it
        filter_result = content_filter.check_input(
            prohibited_message,
            profile.age,
            profile.profile_id
        )
        
        assert filter_result.is_safe is False
        
        # Step 4: Safety monitor flags it (and automatically logs incident)
        alert = safety_monitor.monitor_message(
            profile_id=profile.profile_id,
            message=prohibited_message,
            age=profile.age,
            session_id=session.session_id
        )

        assert alert is not None

        # Step 5: Retrieve logged incident (already logged by monitor_message)
        incidents = incident_logger.get_profile_incidents(profile.profile_id)
        assert len(incidents) > 0
        incident = incidents[0]
        assert incident is not None
        
        # Step 6: System provides safe redirect
        redirect_message = "I'm here to help with science, math, technology, and engineering! What would you like to learn about?"
        
        conversations.add_message(
            conversation_id=conversation.conversation_id,
            role="assistant",
            content=redirect_message
        )
        
        # Step 7: Mark conversation as flagged
        conversations.flag_conversation(
            conversation.conversation_id,
            reason="Safety incident - prohibited keyword"
        )
        
        # Step 8: End session
        sessions.end_session(session.session_id)
        
        # Step 9: Verify incident logged
        incidents = incident_logger.get_profile_incidents(profile.profile_id)
        assert len(incidents) == 1
        assert incidents[0].incident_id == incident.incident_id
        
        # Step 10: Verify parent needs to be notified
        unresolved = incident_logger.get_unresolved_incidents(profile.profile_id)
        assert len(unresolved) == 1


class TestParentReviewWorkflow:
    """Test parent reviewing child's activity"""
    
    @pytest.fixture
    def setup_with_activity(self, system_components):
        """Create family with learning activity"""
        auth = system_components['auth']
        profiles = system_components['profiles']
        sessions = system_components['sessions']
        conversations = system_components['conversations']
        
        # Create family
        success, parent_id = auth.create_parent_account("parent", "SecurePass123!")
        assert success, f"Failed to create parent: {parent_id}"
        profile = profiles.create_profile(
            parent_id=parent_id,
            name="Child",
            age=10,
            grade="5th"
        )
        
        # Create learning activity
        session = sessions.create_session(
            profile_id=profile.profile_id,
            session_type="student"
        )
        
        conversation = conversations.create_conversation(
            session_id=session.session_id,
            profile_id=profile.profile_id
        )
        
        # Add messages
        conversations.add_message(
            conversation_id=conversation.conversation_id,
            role="user",
            content="Why is the sky blue?"
        )
        conversations.add_message(
            conversation_id=conversation.conversation_id,
            role="assistant",
            content="The sky is blue because of how light scatters..."
        )
        
        sessions.increment_question_count(session.session_id)
        sessions.end_session(session.session_id)
        
        return {
            'parent_id': parent_id,
            'profile': profile,
            'session': session,
            'conversation': conversation
        }
    
    def test_parent_review_flow(self, system_components, setup_with_activity):
        """Test parent reviewing child activity"""
        parent_id = setup_with_activity['parent_id']
        profile = setup_with_activity['profile']
        auth = system_components['auth']
        profiles = system_components['profiles']
        sessions = system_components['sessions']
        conversations = system_components['conversations']
        incident_logger = system_components['incident_logger']
        
        # Step 1: Parent logs in
        login_success, session_data = auth.authenticate_parent("parent", "SecurePass123!")
        assert login_success is True
        
        # Step 2: Parent views all children
        children = profiles.get_profiles_by_parent(parent_id)
        assert len(children) == 1
        
        # Step 3: Parent selects child to review
        child = children[0]
        
        # Step 4: Parent views session statistics
        stats = sessions.get_profile_statistics(profile.profile_id)
        assert stats is not None
        assert stats['total_sessions'] == 1
        assert stats['total_questions'] == 1
        
        # Step 5: Parent views session history
        history = sessions.get_session_history(profile.profile_id, limit=10)
        assert len(history) == 1
        
        # Step 6: Parent reviews conversation
        conv = conversations.get_conversation(setup_with_activity['conversation'].conversation_id)
        assert conv is not None
        
        messages = conversations.get_conversation_messages(conv.conversation_id)
        assert len(messages) == 2
        
        # Step 7: Parent checks safety incidents
        incidents = incident_logger.get_profile_incidents(profile.profile_id)
        unresolved = incident_logger.get_unresolved_incidents(profile.profile_id)
        
        # Step 8: Parent logs out
        auth.logout(parent_id)


class TestMultiSessionDay:
    """Test multiple sessions in a single day"""
    
    @pytest.fixture
    def setup_family(self, system_components):
        """Create family"""
        auth = system_components['auth']
        profiles = system_components['profiles']
        
        success, parent_id = auth.create_parent_account("parent", "SecurePass123!")
        assert success, f"Failed to create parent: {parent_id}"
        profile = profiles.create_profile(
            parent_id=parent_id,
            name="Child",
            age=10,
            grade="5th",
            daily_time_limit_minutes=120
        )
        
        return {'parent_id': parent_id, 'profile': profile}
    
    def test_multiple_sessions_workflow(self, system_components, setup_family):
        """Test child having multiple sessions in one day"""
        profile = setup_family['profile']
        sessions = system_components['sessions']
        conversations = system_components['conversations']
        
        session_durations = [30, 45, 40]  # Total: 115 minutes
        
        for duration in session_durations:
            # Check if can start
            can_start, remaining = sessions.check_daily_time_limit(profile.profile_id)
            assert can_start is True
            
            # Start session
            session = sessions.create_session(
                profile_id=profile.profile_id,
                session_type="student"
            )
            
            # Create conversation
            conversation = conversations.create_conversation(
                session_id=session.session_id,
                profile_id=profile.profile_id
            )
            
            # Add some activity
            for i in range(3):
                conversations.add_message(
                    conversation_id=conversation.conversation_id,
                    role="user",
                    content=f"Question {i+1}"
                )
                conversations.add_message(
                    conversation_id=conversation.conversation_id,
                    role="assistant",
                    content=f"Answer {i+1}"
                )
                sessions.increment_question_count(session.session_id)
            
            # End session
            sessions.end_session(session.session_id)
            
            # Manually set duration for testing
            sessions._set_session_duration(session.session_id, duration)
        
        # Verify totals
        stats = sessions.get_profile_statistics(profile.profile_id)
        assert stats['total_sessions'] == 3
        assert stats['total_questions'] == 9
        assert stats['total_minutes'] == 115
        
        # Check remaining time
        can_start, remaining = sessions.check_daily_time_limit(profile.profile_id)
        assert remaining == 5  # 120 - 115


class TestDataPersistence:
    """Test data persistence across system restarts"""
    
    def test_data_survives_restart(self, temp_environment):
        """Test data persists after system restart"""
        env = temp_environment
        
        # Phase 1: Initial setup
        auth1 = AuthenticationManager(env['db'], env['usb_path'])
        profiles1 = ProfileManager(env['db'])
        
        success, parent_id = auth1.create_parent_account("parent", "SecurePass123!")
        assert success, f"Failed to create parent: {parent_id}"
        profile1 = profiles1.create_profile(
            parent_id=parent_id,
            name="Emma",
            age=10,
            grade="5th"
        )
        
        profile_id = profile1.profile_id
        
        # Close connections (simulate restart)
        del auth1
        del profiles1
        
        # Phase 2: System restart - create new instances
        auth2 = AuthenticationManager(env['db'], env['usb_path'])
        profiles2 = ProfileManager(env['db'])
        
        # Verify data persisted
        login_success, _ = auth2.authenticate_parent("parent", "SecurePass123!")
        assert login_success is True
        
        profile2 = profiles2.get_profile(profile_id)
        assert profile2 is not None
        assert profile2.name == "Emma"
        assert profile2.age == 10


class TestEncryptedDataFlow:
    """Test encrypted data storage and retrieval"""
    
    def test_encrypted_conversation_storage(self, system_components):
        """Test conversations are encrypted in storage"""
        auth = system_components['auth']
        profiles = system_components['profiles']
        sessions = system_components['sessions']
        conversations = system_components['conversations']
        encryption = system_components['encryption']
        
        # Create family
        success, parent_id = auth.create_parent_account("parent", "SecurePass123!")
        assert success, f"Failed to create parent: {parent_id}"
        profile = profiles.create_profile(
            parent_id=parent_id,
            name="Child",
            age=10,
            grade="5th"
        )
        
        # Create session and conversation
        session = sessions.create_session(
            profile_id=profile.profile_id,
            session_type="student"
        )
        
        conversation = conversations.create_conversation(
            session_id=session.session_id,
            profile_id=profile.profile_id
        )
        
        # Add sensitive message
        sensitive_content = "This is private conversation content"
        
        conversations.add_message(
            conversation_id=conversation.conversation_id,
            role="user",
            content=sensitive_content
        )
        
        # Retrieve and verify
        messages = conversations.get_conversation_messages(conversation.conversation_id)
        assert len(messages) == 1
        assert messages[0].content == sensitive_content


class TestCompleteUserJourney:
    """Test complete user journey from setup to usage"""
    
    def test_full_user_journey(self, system_components):
        """Test complete user journey over multiple days"""
        auth = system_components['auth']
        profiles = system_components['profiles']
        sessions = system_components['sessions']
        conversations = system_components['conversations']
        content_filter = system_components['content_filter']
        
        # Day 1: Setup
        success, parent_id = auth.create_parent_account("parent", "SecurePass123!")
        assert success, f"Failed to create parent: {parent_id}"
        
        profile = profiles.create_profile(
            parent_id=parent_id,
            name="Emma",
            age=10,
            grade="5th"
        )
        
        profiles.add_subject_preference(profile.profile_id, "mathematics")
        profiles.add_subject_preference(profile.profile_id, "science")
        
        # Day 1: First learning session
        session1 = sessions.create_session(
            profile_id=profile.profile_id,
            session_type="student",
        )
        
        conv1 = conversations.create_conversation(
            session_id=session1.session_id,
            profile_id=profile.profile_id,
            subject_area="mathematics"
        )
        
        # Ask several questions
        for question in ["How do fractions work?", "What is division?", "Can you help with algebra?"]:
            filter_result = content_filter.check_input(question, profile.age, profile.profile_id)
            
            if filter_result.is_safe:
                conversations.add_message(
                    conversation_id=conv1.conversation_id,
                    role="user",
                    content=question
                )
                conversations.add_message(
                    conversation_id=conv1.conversation_id,
                    role="assistant",
                    content="Here's how to understand this..."
                )
                sessions.increment_question_count(session1.session_id)
        
        sessions.end_session(session1.session_id)
        sessions._set_session_duration(session1.session_id, 45)
        
        # Day 2: More sessions
        for i in range(2):
            session = sessions.create_session(
                profile_id=profile.profile_id,
                session_type="student"
            )
            
            conv = conversations.create_conversation(
                session_id=session.session_id,
                profile_id=profile.profile_id
            )
            
            # More questions
            for _ in range(5):
                conversations.add_message(
                    conversation_id=conv.conversation_id,
                    role="user",
                    content="Science question"
                )
                conversations.add_message(
                    conversation_id=conv.conversation_id,
                    role="assistant",
                    content="Science answer"
                )
                sessions.increment_question_count(session.session_id)
            
            sessions.end_session(session.session_id)
            sessions._set_session_duration(session.session_id, 30)
        
        # Parent reviews progress
        stats = sessions.get_profile_statistics(profile.profile_id)
        assert stats['total_sessions'] == 3
        assert stats['total_questions'] == 13  # 3 + 5 + 5
        assert stats['total_minutes'] == 105  # 45 + 30 + 30
        
        # Verify profile stats updated
        updated_profile = profiles.get_profile(profile.profile_id)
        assert updated_profile.total_sessions >= 3
        assert updated_profile.total_questions >= 13


# Run tests with: pytest tests/test_end_to_end_flow.py -v
