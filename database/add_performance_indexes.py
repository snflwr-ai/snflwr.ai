#!/usr/bin/env python3
"""
Add Performance Indexes to snflwr.ai Database
Adds additional indexes to improve query performance for production workloads
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.database import db_manager
from utils.logger import get_logger

logger = get_logger(__name__)


def add_performance_indexes():
    """Add performance indexes to existing database"""

    logger.info("=" * 60)
    logger.info("Adding Performance Indexes to Database")
    logger.info("=" * 60)

    # Additional composite indexes for common query patterns
    indexes = [
        # Users table - composite indexes for common queries
        (
            "idx_users_active_role",
            "CREATE INDEX IF NOT EXISTS idx_users_active_role ON users(is_active, role)"
        ),
        (
            "idx_users_last_login",
            "CREATE INDEX IF NOT EXISTS idx_users_last_login ON users(last_login)"
        ),

        # Auth sessions - improve session cleanup queries
        (
            "idx_sessions_expires",
            "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON auth_sessions(expires_at)"
        ),
        (
            "idx_sessions_user_active",
            "CREATE INDEX IF NOT EXISTS idx_sessions_user_active ON auth_sessions(user_id, is_active)"
        ),

        # Auth tokens - improve token lookup performance
        (
            "idx_tokens_user",
            "CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id)"
        ),
        (
            "idx_tokens_hash",
            "CREATE INDEX IF NOT EXISTS idx_tokens_hash ON auth_tokens(token_hash)"
        ),
        (
            "idx_tokens_type",
            "CREATE INDEX IF NOT EXISTS idx_tokens_type ON auth_tokens(token_type)"
        ),
        (
            "idx_tokens_expires",
            "CREATE INDEX IF NOT EXISTS idx_tokens_expires ON auth_tokens(expires_at)"
        ),
        (
            "idx_tokens_valid",
            "CREATE INDEX IF NOT EXISTS idx_tokens_valid ON auth_tokens(is_valid)"
        ),
        (
            "idx_tokens_user_type",
            "CREATE INDEX IF NOT EXISTS idx_tokens_user_type ON auth_tokens(user_id, token_type, is_valid)"
        ),

        # Child profiles - improve parent dashboard queries
        (
            "idx_profiles_parent_active",
            "CREATE INDEX IF NOT EXISTS idx_profiles_parent_active ON child_profiles(parent_id, is_active)"
        ),
        (
            "idx_profiles_tier",
            "CREATE INDEX IF NOT EXISTS idx_profiles_tier ON child_profiles(tier)"
        ),
        (
            "idx_profiles_created",
            "CREATE INDEX IF NOT EXISTS idx_profiles_created ON child_profiles(created_at)"
        ),

        # Conversation sessions - improve session queries
        (
            "idx_sessions_profile_active",
            "CREATE INDEX IF NOT EXISTS idx_sessions_profile_active ON conversation_sessions(profile_id, is_active)"
        ),
        (
            "idx_sessions_ended",
            "CREATE INDEX IF NOT EXISTS idx_sessions_ended ON conversation_sessions(ended_at)"
        ),
        (
            "idx_sessions_profile_started",
            "CREATE INDEX IF NOT EXISTS idx_sessions_profile_started ON conversation_sessions(profile_id, started_at DESC)"
        ),

        # Messages - improve message retrieval
        (
            "idx_messages_session_timestamp",
            "CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp ON messages(session_id, timestamp)"
        ),
        (
            "idx_messages_role",
            "CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role)"
        ),

        # Safety incidents - improve incident queries and analytics
        (
            "idx_incidents_profile_timestamp",
            "CREATE INDEX IF NOT EXISTS idx_incidents_profile_timestamp ON safety_incidents(profile_id, timestamp DESC)"
        ),
        (
            "idx_incidents_severity_timestamp",
            "CREATE INDEX IF NOT EXISTS idx_incidents_severity_timestamp ON safety_incidents(severity, timestamp DESC)"
        ),
        (
            "idx_incidents_acknowledged",
            "CREATE INDEX IF NOT EXISTS idx_incidents_acknowledged ON safety_incidents(acknowledged)"
        ),
        (
            "idx_incidents_profile_severity",
            "CREATE INDEX IF NOT EXISTS idx_incidents_profile_severity ON safety_incidents(profile_id, severity)"
        ),

        # Parent alerts - improve alert queries
        (
            "idx_alerts_parent_acknowledged",
            "CREATE INDEX IF NOT EXISTS idx_alerts_parent_acknowledged ON parent_alerts(parent_id, acknowledged)"
        ),
        (
            "idx_alerts_severity",
            "CREATE INDEX IF NOT EXISTS idx_alerts_severity ON parent_alerts(severity)"
        ),
        (
            "idx_alerts_parent_created",
            "CREATE INDEX IF NOT EXISTS idx_alerts_parent_created ON parent_alerts(parent_id, created_at DESC)"
        ),

        # Usage quotas - improve quota checks
        (
            "idx_quotas_profile_type",
            "CREATE INDEX IF NOT EXISTS idx_quotas_profile_type ON usage_quotas(profile_id, quota_type)"
        ),
        (
            "idx_quotas_type",
            "CREATE INDEX IF NOT EXISTS idx_quotas_type ON usage_quotas(quota_type)"
        ),

        # Audit log - improve audit queries
        (
            "idx_audit_timestamp",
            "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC)"
        ),
        (
            "idx_audit_user",
            "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)"
        ),
        (
            "idx_audit_event_type",
            "CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type)"
        ),
        (
            "idx_audit_user_timestamp",
            "CREATE INDEX IF NOT EXISTS idx_audit_user_timestamp ON audit_log(user_id, timestamp DESC)"
        ),
    ]

    success_count = 0
    fail_count = 0

    for index_name, index_sql in indexes:
        try:
            logger.info(f"Creating index: {index_name}")
            db_manager.execute_write(index_sql)
            success_count += 1
            logger.info(f"[OK] Created: {index_name}")
        except Exception as e:
            fail_count += 1
            logger.error(f"[FAIL] Failed to create {index_name}: {e}")

    logger.info("=" * 60)
    logger.info(f"Index Creation Summary:")
    logger.info(f"  Total indexes: {len(indexes)}")
    logger.info(f"  Successfully created: {success_count}")
    logger.info(f"  Failed: {fail_count}")
    logger.info("=" * 60)

    if fail_count > 0:
        logger.warning(f"{fail_count} indexes failed to create")
        return False

    logger.info("[OK] All performance indexes created successfully!")
    return True


def analyze_database():
    """Run database analysis to update query planner statistics"""
    logger.info("Running database analysis...")

    try:
        # SQLite: ANALYZE command updates index statistics
        db_manager.execute_write("ANALYZE")
        logger.info("[OK] Database analysis completed")
    except Exception as e:
        logger.error(f"[FAIL] Database analysis failed: {e}")


def main():
    """Main execution"""
    logger.info("Starting performance index migration")

    try:
        # Add indexes
        if not add_performance_indexes():
            logger.error("Some indexes failed to create")
            sys.exit(1)

        # Analyze database to update statistics
        analyze_database()

        logger.info("[OK] Performance optimization completed successfully!")

    except Exception as e:
        logger.exception(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
