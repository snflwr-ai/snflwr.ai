"""
Test Suite for Multi-Profile Family Management
Tests family with multiple children, profile switching, isolated conversations,
profile-specific settings, and family-level analytics
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import shutil

from core.authentication import AuthenticationManager
from core.profile_manager import ProfileManager
from core.session_manager import SessionManager
from storage.database import DatabaseManager
from storage.conversation_store import ConversationStore
from safety.incident_logger import IncidentLogger


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


@pytest.fixture
def auth_manager(temp_db):
    """Create authentication manager"""
    usb_path = Path(tempfile.mkdtemp())
    auth = AuthenticationManager(temp_db, usb_path)
    yield auth
    shutil.rmtree(usb_path)


@pytest.fixture
def profile_manager_fixture(temp_db):
    """Create profile manager"""
    return ProfileManager(temp_db)


@pytest.fixture
def session_manager_fixture(temp_db):
    """Create session manager"""
    return SessionManager(temp_db)


@pytest.fixture
def conversation_store_fixture(temp_db):
    """Create conversation store"""
    return ConversationStore(temp_db)


@pytest.fixture
def incident_logger_fixture(temp_db):
    """Create incident logger"""
    return IncidentLogger(temp_db)


@pytest.fixture
def family_with_three_children(auth_manager, profile_manager_fixture):
    """Create family with three children of different ages"""
    # Create parent
    success, parent_id = auth_manager.create_parent_account("parent", "SecurePass123!")
    assert success, f"Failed to create parent: {parent_id}"
    
    # Create three children
    children = []
    
    emma = profile_manager_fixture.create_profile(
        parent_id=parent_id,
        name="Emma",
        age=10,
        grade="5th",
        learning_level="adaptive",
        daily_time_limit_minutes=120
    )
    profile_manager_fixture.add_subject_preference(emma.profile_id, "mathematics")
    profile_manager_fixture.add_subject_preference(emma.profile_id, "science")
    # Refetch to get updated subjects_focus
    emma = profile_manager_fixture.get_profile(emma.profile_id)
    children.append(emma)

    alex = profile_manager_fixture.create_profile(
        parent_id=parent_id,
        name="Alex",
        age=8,
        grade="3rd",
        learning_level="beginner",
        daily_time_limit_minutes=90
    )
    profile_manager_fixture.add_subject_preference(alex.profile_id, "reading")
    # Refetch to get updated subjects_focus
    alex = profile_manager_fixture.get_profile(alex.profile_id)
    children.append(alex)

    sophie = profile_manager_fixture.create_profile(
        parent_id=parent_id,
        name="Sophie",
        age=14,
        grade="9th",
        learning_level="advanced",
        daily_time_limit_minutes=180
    )
    profile_manager_fixture.add_subject_preference(sophie.profile_id, "physics")
    profile_manager_fixture.add_subject_preference(sophie.profile_id, "chemistry")
    # Refetch to get updated subjects_focus
    sophie = profile_manager_fixture.get_profile(sophie.profile_id)
    children.append(sophie)

    return {
        'parent_id': parent_id,
        'children': children,
        'emma': emma,
        'alex': alex,
        'sophie': sophie
    }


class TestFamilyProfileManagement:
    """Test managing multiple child profiles"""
    
    def test_create_multiple_profiles(self, family_with_three_children):
        """Test creating multiple child profiles for one parent"""
        family = family_with_three_children
        
        assert len(family['children']) == 3
        assert family['emma'].name == "Emma"
        assert family['alex'].name == "Alex"
        assert family['sophie'].name == "Sophie"
    
    def test_profiles_have_unique_ids(self, family_with_three_children):
        """Test each profile has unique ID"""
        children = family_with_three_children['children']
        
        profile_ids = [child.profile_id for child in children]
        
        # All IDs should be unique
        assert len(profile_ids) == len(set(profile_ids))
    
    def test_profiles_sorted_by_age(self, profile_manager_fixture, family_with_three_children):
        """Test profiles can be sorted by age"""
        parent_id = family_with_three_children['parent_id']
        
        profiles = profile_manager_fixture.get_profiles_by_parent(parent_id)
        sorted_profiles = sorted(profiles, key=lambda p: p.age)
        
        assert sorted_profiles[0].name == "Alex"  # 8
        assert sorted_profiles[1].name == "Emma"  # 10
        assert sorted_profiles[2].name == "Sophie"  # 14
    
class TestDifferentLearningLevels:
    """Test children with different learning levels"""
    
    def test_different_time_limits(self, family_with_three_children):
        """Test each child has different daily time limit"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        sophie = family_with_three_children['sophie']
        
        assert emma.daily_time_limit_minutes == 120  # 2 hours
        assert alex.daily_time_limit_minutes == 90   # 1.5 hours
        assert sophie.daily_time_limit_minutes == 180  # 3 hours
    
    def test_different_learning_levels(self, family_with_three_children):
        """Test each child has appropriate learning level"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        sophie = family_with_three_children['sophie']
        
        assert alex.learning_level == "beginner"
        assert emma.learning_level == "adaptive"
        assert sophie.learning_level == "advanced"
    
    def test_different_subject_preferences(self, family_with_three_children):
        """Test each child has different subject preferences"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        sophie = family_with_three_children['sophie']
        
        assert "mathematics" in emma.subjects_focus
        assert "reading" in alex.subjects_focus
        assert "physics" in sophie.subjects_focus


class TestConcurrentSessions:
    """Test concurrent sessions for different children"""
    
    def test_all_children_can_have_active_sessions(self, session_manager_fixture, family_with_three_children):
        """Test all children can have simultaneous active sessions"""
        children = family_with_three_children['children']
        
        sessions = []
        for child in children:
            session = session_manager_fixture.create_session(
                profile_id=child.profile_id,
                session_type="student"
            )
            sessions.append(session)
        
        # All sessions should be active
        assert len(sessions) == 3
        for session in sessions:
            assert session.is_active is True
    
    def test_ending_session_allows_sibling_session(self, session_manager_fixture, family_with_three_children):
        """Test ending one child's session doesn't affect siblings"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        
        # Both start sessions
        emma_session = session_manager_fixture.create_session(
            profile_id=emma.profile_id,
            session_type="student"
        )
        
        alex_session = session_manager_fixture.create_session(
            profile_id=alex.profile_id,
            session_type="student"
        )
        
        # End Emma's session
        session_manager_fixture.end_session(emma_session.session_id)
        
        # Alex's session should still be active
        alex_retrieved = session_manager_fixture.get_session(alex_session.session_id)
        assert alex_retrieved.is_active is True


class TestIsolatedConversations:
    """Test conversation isolation between children"""
    
    def test_conversations_isolated_by_profile(self, session_manager_fixture, conversation_store_fixture, family_with_three_children):
        """Test each child's conversations are isolated"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        
        # Emma's session and conversation
        emma_session = session_manager_fixture.create_session(
            profile_id=emma.profile_id,
            session_type="student"
        )
        emma_conv = conversation_store_fixture.create_conversation(
            session_id=emma_session.session_id,
            profile_id=emma.profile_id
        )
        conversation_store_fixture.add_message(
            conversation_id=emma_conv.conversation_id,
            role="user",
            content="Emma's question about math"
        )
        
        # Alex's session and conversation
        alex_session = session_manager_fixture.create_session(
            profile_id=alex.profile_id,
            session_type="student"
        )
        alex_conv = conversation_store_fixture.create_conversation(
            session_id=alex_session.session_id,
            profile_id=alex.profile_id
        )
        conversation_store_fixture.add_message(
            conversation_id=alex_conv.conversation_id,
            role="user",
            content="Alex's question about reading"
        )
        
        # Verify isolation
        emma_convs = conversation_store_fixture.get_profile_conversations(emma.profile_id)
        alex_convs = conversation_store_fixture.get_profile_conversations(alex.profile_id)
        
        assert len(emma_convs) == 1
        assert len(alex_convs) == 1
        assert emma_convs[0].conversation_id != alex_convs[0].conversation_id
    
    def test_sibling_cannot_access_other_conversations(self, conversation_store_fixture, session_manager_fixture, family_with_three_children):
        """Test children cannot see each other's conversations"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        
        # Create Emma's conversation
        emma_session = session_manager_fixture.create_session(
            profile_id=emma.profile_id,
            session_type="student"
        )
        emma_conv = conversation_store_fixture.create_conversation(
            session_id=emma_session.session_id,
            profile_id=emma.profile_id
        )
        
        # Alex should not see Emma's conversations
        alex_convs = conversation_store_fixture.get_profile_conversations(alex.profile_id)
        
        assert len(alex_convs) == 0
        assert emma_conv.conversation_id not in [c.conversation_id for c in alex_convs]


class TestIndependentDailyLimits:
    """Test each child has independent daily limits"""
    
    def test_daily_time_limits_independent(self, session_manager_fixture, family_with_three_children):
        """Test each child's time limit is tracked independently"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        
        # Emma uses 100 minutes
        emma_session = session_manager_fixture.create_session(
            profile_id=emma.profile_id,
            session_type="student"
        )
        session_manager_fixture.end_session(emma_session.session_id)
        session_manager_fixture._set_session_duration(emma_session.session_id, 100)
        
        # Check Emma's remaining time
        emma_can_start, emma_remaining = session_manager_fixture.check_daily_time_limit(emma.profile_id)
        assert emma_remaining == 20  # 120 - 100
        
        # Check Alex's remaining time (should be full)
        alex_can_start, alex_remaining = session_manager_fixture.check_daily_time_limit(alex.profile_id)
        assert alex_remaining == 90  # Full limit
    
    def test_daily_session_limits_independent(self, session_manager_fixture, family_with_three_children):
        """Test daily session count tracked per child"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        
        # Emma has 3 sessions
        for i in range(3):
            session = session_manager_fixture.create_session(
                profile_id=emma.profile_id,
                session_type="student"
            )
            session_manager_fixture.end_session(session.session_id)
        
        # Check session counts
        emma_count = session_manager_fixture.get_sessions_today_count(emma.profile_id)
        alex_count = session_manager_fixture.get_sessions_today_count(alex.profile_id)
        
        assert emma_count == 3
        assert alex_count == 0


class TestFamilyLevelStatistics:
    """Test family-wide statistics and analytics"""
    
    def test_compare_children_activity(self, session_manager_fixture, family_with_three_children):
        """Test comparing activity levels between children"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        sophie = family_with_three_children['sophie']
        
        # Give each child different activity levels
        for child, num_sessions, questions_per_session in [
            (emma, 4, 10),
            (alex, 2, 5),
            (sophie, 6, 15)
        ]:
            for _ in range(num_sessions):
                session = session_manager_fixture.create_session(
                    profile_id=child.profile_id,
                    session_type="student"
                )
                
                for _ in range(questions_per_session):
                    session_manager_fixture.increment_question_count(session.session_id)
                
                session_manager_fixture.end_session(session.session_id)
        
        # Get stats for each child
        emma_stats = session_manager_fixture.get_profile_statistics(emma.profile_id)
        alex_stats = session_manager_fixture.get_profile_statistics(alex.profile_id)
        sophie_stats = session_manager_fixture.get_profile_statistics(sophie.profile_id)
        
        assert emma_stats['total_questions'] == 40  # 4 × 10
        assert alex_stats['total_questions'] == 10  # 2 × 5
        assert sophie_stats['total_questions'] == 90  # 6 × 15


class TestSafetyIncidentsByChild:
    """Test safety incidents tracked per child"""
    
    def test_safety_incidents_isolated_by_child(self, incident_logger_fixture, family_with_three_children):
        """Test each child's safety incidents are tracked separately"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        
        # Log incidents for Emma
        incident_logger_fixture.log_incident(
            profile_id=emma.profile_id,
            incident_type="prohibited_keyword",
            severity="minor",
            content_snippet="Emma's filtered content"
        )
        incident_logger_fixture.log_incident(
            profile_id=emma.profile_id,
            incident_type="pattern_match",
            severity="minor",
            content_snippet="Emma's second incident"
        )
        
        # Log incident for Alex
        incident_logger_fixture.log_incident(
            profile_id=alex.profile_id,
            incident_type="prohibited_keyword",
            severity="minor",
            content_snippet="Alex's filtered content"
        )
        
        # Verify isolation
        emma_incidents = incident_logger_fixture.get_profile_incidents(emma.profile_id)
        alex_incidents = incident_logger_fixture.get_profile_incidents(alex.profile_id)
        
        assert len(emma_incidents) == 2
        assert len(alex_incidents) == 1
    
    def test_identify_child_needing_attention(self, incident_logger_fixture, family_with_three_children):
        """Test identifying which child needs parent attention"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        sophie = family_with_three_children['sophie']
        
        # Emma: Multiple incidents
        for i in range(5):
            incident_logger_fixture.log_incident(
                profile_id=emma.profile_id,
                incident_type="test",
                severity="minor",
                content_snippet=f"Incident {i}"
            )
        
        # Alex: One incident
        incident_logger_fixture.log_incident(
            profile_id=alex.profile_id,
            incident_type="test",
            severity="minor",
            content_snippet="Single incident"
        )
        
        # Sophie: No incidents
        
        # Check unresolved counts
        emma_unresolved = len(incident_logger_fixture.get_unresolved_incidents(emma.profile_id))
        alex_unresolved = len(incident_logger_fixture.get_unresolved_incidents(alex.profile_id))
        sophie_unresolved = len(incident_logger_fixture.get_unresolved_incidents(sophie.profile_id))
        
        assert emma_unresolved == 5
        assert alex_unresolved == 1
        assert sophie_unresolved == 0


class TestProfileSwitching:
    """Test switching between child profiles"""
    
    def test_switch_active_profile(self, session_manager_fixture, conversation_store_fixture, family_with_three_children):
        """Test switching from one child to another"""
        emma = family_with_three_children['emma']
        alex = family_with_three_children['alex']
        
        # Emma's session
        emma_session = session_manager_fixture.create_session(
            profile_id=emma.profile_id,
            session_type="student"
        )
        emma_conv = conversation_store_fixture.create_conversation(
            session_id=emma_session.session_id,
            profile_id=emma.profile_id
        )
        
        # End Emma's session
        session_manager_fixture.end_session(emma_session.session_id)
        
        # Switch to Alex
        alex_session = session_manager_fixture.create_session(
            profile_id=alex.profile_id,
            session_type="student"
        )
        alex_conv = conversation_store_fixture.create_conversation(
            session_id=alex_session.session_id,
            profile_id=alex.profile_id
        )
        
        # Verify both have separate contexts
        assert emma_conv.conversation_id != alex_conv.conversation_id
        assert emma_session.session_id != alex_session.session_id
    
    def test_resume_previous_child_session(self, session_manager_fixture, conversation_store_fixture, family_with_three_children):
        """Test resuming previous child's conversation context"""
        emma = family_with_three_children['emma']
        
        # Emma's first session
        session1 = session_manager_fixture.create_session(
            profile_id=emma.profile_id,
            session_type="student"
        )
        conv1 = conversation_store_fixture.create_conversation(
            session_id=session1.session_id,
            profile_id=emma.profile_id
        )
        conversation_store_fixture.add_message(
            conversation_id=conv1.conversation_id,
            role="user",
            content="Question from first session"
        )
        session_manager_fixture.end_session(session1.session_id)
        
        # Emma's second session (new day)
        session2 = session_manager_fixture.create_session(
            profile_id=emma.profile_id,
            session_type="student"
        )
        
        # Can retrieve previous conversations
        emma_conversations = conversation_store_fixture.get_profile_conversations(emma.profile_id)
        
        assert len(emma_conversations) >= 1
        assert any("Question from first session" in msg.content 
                  for conv in emma_conversations 
                  for msg in conversation_store_fixture.get_conversation_messages(conv.conversation_id))


class TestAgeProgressionHandling:
    """Test handling children aging up"""
    
    def test_update_child_age(self, profile_manager_fixture, family_with_three_children):
        """Test updating child's age (birthday)"""
        alex = family_with_three_children['alex']
        
        # Alex has birthday
        success = profile_manager_fixture.update_profile(
            alex.profile_id,
            age=9,
            grade="4th"
        )
        
        assert success is True
        
        updated = profile_manager_fixture.get_profile(alex.profile_id)
        assert updated.age == 9
        assert updated.grade == "4th"
    
    def test_adjust_settings_for_age(self, profile_manager_fixture, family_with_three_children):
        """Test adjusting settings when child gets older"""
        alex = family_with_three_children['alex']
        
        # Update age and settings
        profile_manager_fixture.update_profile(
            alex.profile_id,
            age=9,
            learning_level="adaptive",  # Upgrade from beginner
            daily_time_limit_minutes=120  # Increase from 90
        )
        
        updated = profile_manager_fixture.get_profile(alex.profile_id)
        assert updated.learning_level == "adaptive"
        assert updated.daily_time_limit_minutes == 120


class TestFamilyScenarios:
    """Test real-world family scenarios"""
    
    def test_homeschool_family_scenario(self, session_manager_fixture, conversation_store_fixture, family_with_three_children):
        """Test typical homeschool family daily usage"""
        children = family_with_three_children['children']
        
        # Morning: All children start lessons
        morning_sessions = []
        for child in children:
            session = session_manager_fixture.create_session(
                profile_id=child.profile_id,
                session_type="student"
            )
            morning_sessions.append(session)
        
        # Each child asks questions
        for session, child in zip(morning_sessions, children):
            conv = conversation_store_fixture.create_conversation(
                session_id=session.session_id,
                profile_id=child.profile_id
            )
            
            for i in range(8):  # 8 questions each
                conversation_store_fixture.add_message(
                    conversation_id=conv.conversation_id,
                    role="user",
                    content=f"{child.name}'s question {i+1}"
                )
                conversation_store_fixture.add_message(
                    conversation_id=conv.conversation_id,
                    role="assistant",
                    content=f"Answer for {child.name}"
                )
                session_manager_fixture.increment_question_count(session.session_id)
        
        # End morning sessions
        for session in morning_sessions:
            session_manager_fixture.end_session(session.session_id)
            session_manager_fixture._set_session_duration(session.session_id, 90)
        
        # Afternoon: Only Emma and Sophie continue
        for child in [children[0], children[2]]:  # Emma and Sophie
            session = session_manager_fixture.create_session(
                profile_id=child.profile_id,
                session_type="student"
            )
            session_manager_fixture.end_session(session.session_id)
            session_manager_fixture._set_session_duration(session.session_id, 45)
        
        # Verify all activity tracked
        for child in children:
            stats = session_manager_fixture.get_profile_statistics(child.profile_id)
            assert stats['total_sessions'] >= 1
    
    def test_siblings_with_different_schedules(self, session_manager_fixture, family_with_three_children):
        """Test siblings using system at different times"""
        emma = family_with_three_children['emma']
        sophie = family_with_three_children['sophie']
        
        # Emma: Morning user (8am-10am)
        emma_sessions = []
        for _ in range(3):
            session = session_manager_fixture.create_session(
                profile_id=emma.profile_id,
                session_type="student"
            )
            session_manager_fixture.end_session(session.session_id)
            session_manager_fixture._set_session_duration(session.session_id, 30)
            emma_sessions.append(session)
        
        # Sophie: Evening user (7pm-9pm)
        sophie_sessions = []
        for _ in range(2):
            session = session_manager_fixture.create_session(
                profile_id=sophie.profile_id,
                session_type="student"
            )
            session_manager_fixture.end_session(session.session_id)
            session_manager_fixture._set_session_duration(session.session_id, 60)
            sophie_sessions.append(session)
        
        # Verify independent tracking
        emma_stats = session_manager_fixture.get_profile_statistics(emma.profile_id)
        sophie_stats = session_manager_fixture.get_profile_statistics(sophie.profile_id)
        
        assert emma_stats['total_minutes'] == 90  # 3 × 30
        assert sophie_stats['total_minutes'] == 120  # 2 × 60


# Run tests with: pytest tests/test_multi_profile_family.py -v
