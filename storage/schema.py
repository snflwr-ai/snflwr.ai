"""Database schema DDL for snflwr.ai.

Single source of truth for table-creation SQL, extracted verbatim from
storage/database.py (behavior-preserving refactor). The two dialect blocks stay
separate because SQLite and PostgreSQL differ in type/constraint syntax; the
shared idempotent migration column lists below are deduplicated across dialects.
"""

# Idempotent ADD COLUMN migrations for existing databases. Identical column set
# for both dialects (the per-dialect ALTER wrapping stays in database.py).
ACCOUNT_MIGRATION_COLUMNS = [
    "role TEXT DEFAULT 'parent'",
    "name TEXT",
    "email_hash TEXT",
    "encrypted_email TEXT",
    "is_active BOOLEAN DEFAULT TRUE",
    "email_notifications_enabled BOOLEAN DEFAULT TRUE",
    "email_verified BOOLEAN DEFAULT FALSE",
    "deletion_requested_at TEXT",
    "owui_token TEXT",
]

PROFILE_MIGRATION_COLUMNS = [
    "owui_user_id TEXT",
    "grade_level TEXT",
    "tier TEXT DEFAULT 'standard'",
    "model_role TEXT DEFAULT 'student'",
]


def create_sqlite_tables(cursor):
    """Create all SQLite tables (idempotent; uses IF NOT EXISTS)."""
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS accounts (
                    parent_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    email TEXT,
                    device_id TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login TEXT,
                    failed_login_attempts INTEGER DEFAULT 0,
                    account_locked_until TEXT,
                    role TEXT DEFAULT 'parent',
                    name TEXT,
                    email_hash TEXT,
                    encrypted_email TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    email_notifications_enabled BOOLEAN DEFAULT TRUE,
                    email_verified BOOLEAN DEFAULT FALSE,
                    deletion_requested_at TEXT,
                    owui_token TEXT
                )
            """
    )

    # Child profiles table
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS child_profiles (
                    profile_id TEXT PRIMARY KEY,
                    parent_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    avatar TEXT DEFAULT 'default',
                    created_at TEXT NOT NULL,
                    learning_level TEXT DEFAULT 'adaptive',
                    daily_time_limit_minutes INTEGER DEFAULT 120,
                    total_sessions INTEGER DEFAULT 0,
                    total_questions INTEGER DEFAULT 0,
                    weekly_conversation_count INTEGER DEFAULT 0,
                    weekly_reset_date TEXT,
                    last_active TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    birthdate TEXT,
                    parental_consent_given BOOLEAN DEFAULT FALSE,
                    parental_consent_date TEXT,
                    parental_consent_method TEXT,
                    coppa_verified BOOLEAN DEFAULT FALSE,
                    age_verified_at TEXT,
                    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
                    CONSTRAINT valid_age CHECK (age BETWEEN 5 AND 18),
                    CONSTRAINT valid_learning_level CHECK (learning_level IN ('beginner', 'adaptive', 'advanced'))
                )
            """
    )

    # Profile subject preferences
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS profile_subjects (
                    id INTEGER PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                    UNIQUE(profile_id, subject)
                )
            """
    )

    # Sessions table
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    profile_id TEXT,
                    parent_id TEXT,
                    session_type TEXT DEFAULT 'student',
                    started_at TEXT NOT NULL,
                    last_activity TEXT,
                    ended_at TEXT,
                    duration_minutes INTEGER,
                    questions_asked INTEGER DEFAULT 0,
                    platform TEXT,
                    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE SET NULL,
                    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE SET NULL,
                    CONSTRAINT valid_session_type CHECK (session_type IN ('student', 'parent', 'educator'))
                )
            """
    )

    # Conversations table
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    subject_area TEXT,
                    is_flagged BOOLEAN DEFAULT FALSE,
                    flag_reason TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
                    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
                )
            """
    )

    # Messages table
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    model_used TEXT,
                    response_time_ms INTEGER,
                    tokens_used INTEGER,
                    safety_filtered BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    CONSTRAINT valid_role CHECK (role IN ('user', 'assistant', 'system'))
                )
            """
    )

    # Safety incidents table
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS safety_incidents (
                    incident_id INTEGER PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    session_id TEXT,
                    incident_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    content_snippet TEXT,
                    timestamp TEXT NOT NULL,
                    parent_notified BOOLEAN DEFAULT FALSE,
                    parent_notified_at TEXT,
                    resolved BOOLEAN DEFAULT FALSE,
                    resolved_at TEXT,
                    resolution_notes TEXT,
                    metadata TEXT,
                    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL,
                    CONSTRAINT valid_severity CHECK (severity IN ('minor', 'major', 'critical'))
                )
            """
    )

    # False positive reports table
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS safety_false_positives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    block_reason TEXT NOT NULL,
                    triggered_keywords TEXT NOT NULL,
                    educator_note TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    reviewed_by TEXT,
                    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
                )
            """
    )

    cursor.execute(
        """
                CREATE INDEX IF NOT EXISTS idx_false_positives_profile
                ON safety_false_positives(profile_id)
            """
    )

    cursor.execute(
        """
                CREATE INDEX IF NOT EXISTS idx_false_positives_unreviewed
                ON safety_false_positives(reviewed_at) WHERE reviewed_at IS NULL
            """
    )

    # Parent alerts table
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS parent_alerts (
                    alert_id INTEGER PRIMARY KEY,
                    parent_id TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    related_incident_id INTEGER,
                    timestamp TEXT NOT NULL,
                    acknowledged BOOLEAN DEFAULT FALSE,
                    acknowledged_at TEXT,
                    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
                    FOREIGN KEY (related_incident_id) REFERENCES safety_incidents(incident_id) ON DELETE SET NULL,
                    CONSTRAINT valid_alert_severity CHECK (severity IN ('minor', 'major', 'critical'))
                )
            """
    )

    # Parent authentication tokens (sessions, email verification, password reset)
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    parent_id TEXT,
                    token_type TEXT DEFAULT 'session',
                    token_hash TEXT,
                    session_token TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_used TEXT,
                    used_at TEXT,
                    is_valid BOOLEAN DEFAULT TRUE,
                    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
                    CONSTRAINT valid_token_type CHECK (token_type IN ('session', 'email_verification', 'password_reset'))
                )
            """
    )

    # Create index for token lookups
    cursor.execute(
        """
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash
                ON auth_tokens(token_hash)
            """
    )

    cursor.execute(
        """
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_session
                ON auth_tokens(session_token)
            """
    )

    # System audit log
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS audit_log (
                    log_id INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    user_id TEXT,
                    user_type TEXT,
                    action TEXT NOT NULL,
                    details TEXT,
                    ip_address TEXT,
                    success BOOLEAN DEFAULT TRUE
                )
            """
    )

    # Learning analytics
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS learning_analytics (
                    analytics_id INTEGER PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    subject_area TEXT,
                    questions_asked INTEGER DEFAULT 0,
                    session_duration_minutes INTEGER DEFAULT 0,
                    topics_covered TEXT,
                    difficulty_level TEXT,
                    engagement_score REAL,
                    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                    UNIQUE(profile_id, date, subject_area)
                )
            """
    )

    # Parental consent log (COPPA audit trail)
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS parental_consent_log (
                    consent_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    consent_type TEXT NOT NULL,
                    consent_method TEXT NOT NULL,
                    consent_date TEXT NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    electronic_signature TEXT,
                    verification_token TEXT,
                    verified_at TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    notes TEXT,
                    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE
                )
            """
    )

    # System configuration (for admin settings — used by database/init_db.py)
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS system_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    setting_type TEXT NOT NULL CHECK (setting_type IN ('string', 'integer', 'boolean', 'json')),
                    description TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                )
            """
    )

    # Error tracking (production monitoring — used by utils/error_tracking.py)
    cursor.execute(
        """
                CREATE TABLE IF NOT EXISTS error_tracking (
                    error_id INTEGER PRIMARY KEY,
                    error_hash TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    module TEXT NOT NULL,
                    function TEXT NOT NULL,
                    line_number INTEGER NOT NULL,
                    stack_trace TEXT,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    occurrence_count INTEGER DEFAULT 1,
                    severity TEXT NOT NULL CHECK (severity IN ('critical', 'error', 'warning')),
                    resolved INTEGER DEFAULT 0,
                    resolved_at TEXT,
                    resolution_notes TEXT,
                    user_id TEXT,
                    session_id TEXT,
                    context TEXT
                )
            """
    )


def create_postgres_tables(cursor):
    """Create all PostgreSQL tables (idempotent; uses IF NOT EXISTS)."""
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS accounts (
                        parent_id TEXT PRIMARY KEY,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        email TEXT,
                        device_id TEXT UNIQUE NOT NULL,
                        created_at TEXT NOT NULL,
                        last_login TEXT,
                        failed_login_attempts INTEGER DEFAULT 0,
                        account_locked_until TEXT,
                        role TEXT DEFAULT 'parent',
                        name TEXT,
                        email_hash TEXT,
                        encrypted_email TEXT,
                        is_active BOOLEAN DEFAULT TRUE,
                        email_notifications_enabled BOOLEAN DEFAULT TRUE,
                        email_verified BOOLEAN DEFAULT FALSE,
                        deletion_requested_at TEXT,
                        owui_token TEXT
                    )
                """
    )

    # Child profiles table
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS child_profiles (
                        profile_id TEXT PRIMARY KEY,
                        parent_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        age INTEGER NOT NULL,
                        grade TEXT NOT NULL,
                        avatar TEXT DEFAULT 'default',
                        created_at TEXT NOT NULL,
                        learning_level TEXT DEFAULT 'adaptive',
                        daily_time_limit_minutes INTEGER DEFAULT 120,
                        total_sessions INTEGER DEFAULT 0,
                        total_questions INTEGER DEFAULT 0,
                        weekly_conversation_count INTEGER DEFAULT 0,
                        weekly_reset_date TEXT,
                        last_active TEXT,
                        is_active BOOLEAN DEFAULT TRUE,
                        birthdate TEXT,
                        parental_consent_given BOOLEAN DEFAULT FALSE,
                        parental_consent_date TEXT,
                        parental_consent_method TEXT,
                        coppa_verified BOOLEAN DEFAULT FALSE,
                        age_verified_at TEXT,
                        FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
                        CONSTRAINT valid_age CHECK (age BETWEEN 5 AND 18),
                        CONSTRAINT valid_learning_level CHECK (learning_level IN ('beginner', 'adaptive', 'advanced'))
                    )
                """
    )

    # Profile subject preferences
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS profile_subjects (
                        id SERIAL PRIMARY KEY,
                        profile_id TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        added_at TEXT NOT NULL,
                        FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                        UNIQUE(profile_id, subject)
                    )
                """
    )

    # Sessions table
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        profile_id TEXT,
                        parent_id TEXT,
                        session_type TEXT DEFAULT 'student',
                        started_at TEXT NOT NULL,
                        last_activity TEXT,
                        ended_at TEXT,
                        duration_minutes INTEGER,
                        questions_asked INTEGER DEFAULT 0,
                        platform TEXT,
                        FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE SET NULL,
                        FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE SET NULL,
                        CONSTRAINT valid_session_type CHECK (session_type IN ('student', 'parent', 'educator'))
                    )
                """
    )

    # Conversations table
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS conversations (
                        conversation_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        profile_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        message_count INTEGER DEFAULT 0,
                        subject_area TEXT,
                        is_flagged BOOLEAN DEFAULT FALSE,
                        flag_reason TEXT,
                        FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
                        FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
                    )
                """
    )

    # Messages table
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS messages (
                        message_id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        model_used TEXT,
                        response_time_ms INTEGER,
                        tokens_used INTEGER,
                        safety_filtered BOOLEAN DEFAULT FALSE,
                        FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                        CONSTRAINT valid_role CHECK (role IN ('user', 'assistant', 'system'))
                    )
                """
    )

    # Safety incidents table
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS safety_incidents (
                        incident_id SERIAL PRIMARY KEY,
                        profile_id TEXT NOT NULL,
                        session_id TEXT,
                        incident_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        content_snippet TEXT,
                        timestamp TEXT NOT NULL,
                        parent_notified BOOLEAN DEFAULT FALSE,
                        parent_notified_at TEXT,
                        resolved BOOLEAN DEFAULT FALSE,
                        resolved_at TEXT,
                        resolution_notes TEXT,
                        metadata TEXT,
                        FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                        FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL,
                        CONSTRAINT valid_severity CHECK (severity IN ('minor', 'major', 'critical'))
                    )
                """
    )

    # Parent alerts table
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS parent_alerts (
                        alert_id SERIAL PRIMARY KEY,
                        parent_id TEXT NOT NULL,
                        alert_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        message TEXT NOT NULL,
                        related_incident_id INTEGER,
                        timestamp TEXT NOT NULL,
                        acknowledged BOOLEAN DEFAULT FALSE,
                        acknowledged_at TEXT,
                        FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
                        FOREIGN KEY (related_incident_id) REFERENCES safety_incidents(incident_id) ON DELETE SET NULL,
                        CONSTRAINT valid_alert_severity CHECK (severity IN ('minor', 'major', 'critical'))
                    )
                """
    )

    # Parent authentication tokens (sessions, email verification, password reset)
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS auth_tokens (
                        token_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        parent_id TEXT,
                        token_type TEXT DEFAULT 'session',
                        token_hash TEXT,
                        session_token TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        last_used TEXT,
                        used_at TEXT,
                        is_valid BOOLEAN DEFAULT TRUE,
                        FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
                        CONSTRAINT valid_token_type CHECK (token_type IN ('session', 'email_verification', 'password_reset'))
                    )
                """
    )

    # Create index for token lookups
    cursor.execute(
        """
                    CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash
                    ON auth_tokens(token_hash)
                """
    )

    cursor.execute(
        """
                    CREATE INDEX IF NOT EXISTS idx_auth_tokens_session
                    ON auth_tokens(session_token)
                """
    )

    # System audit log
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS audit_log (
                        log_id SERIAL PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        user_id TEXT,
                        user_type TEXT,
                        action TEXT NOT NULL,
                        details TEXT,
                        ip_address TEXT,
                        success BOOLEAN DEFAULT TRUE
                    )
                """
    )

    # Learning analytics
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS learning_analytics (
                        analytics_id SERIAL PRIMARY KEY,
                        profile_id TEXT NOT NULL,
                        date TEXT NOT NULL,
                        subject_area TEXT,
                        questions_asked INTEGER DEFAULT 0,
                        session_duration_minutes INTEGER DEFAULT 0,
                        topics_covered TEXT,
                        difficulty_level TEXT,
                        engagement_score REAL,
                        FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                        UNIQUE(profile_id, date, subject_area)
                    )
                """
    )

    # Parental consent log (COPPA audit trail)
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS parental_consent_log (
                        consent_id TEXT PRIMARY KEY,
                        profile_id TEXT NOT NULL,
                        parent_id TEXT NOT NULL,
                        consent_type TEXT NOT NULL,
                        consent_method TEXT NOT NULL,
                        consent_date TEXT NOT NULL,
                        ip_address TEXT,
                        user_agent TEXT,
                        electronic_signature TEXT,
                        verification_token TEXT,
                        verified_at TEXT,
                        is_active BOOLEAN DEFAULT TRUE,
                        notes TEXT,
                        FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
                        FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE
                    )
                """
    )

    # System configuration (for admin settings — used by database/init_db.py)
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS system_settings (
                        setting_key TEXT PRIMARY KEY,
                        setting_value TEXT NOT NULL,
                        setting_type TEXT NOT NULL CHECK (setting_type IN ('string', 'integer', 'boolean', 'json')),
                        description TEXT,
                        updated_at TEXT NOT NULL,
                        updated_by TEXT
                    )
                """
    )

    # Error tracking (production monitoring — used by utils/error_tracking.py)
    cursor.execute(
        """
                    CREATE TABLE IF NOT EXISTS error_tracking (
                        error_id SERIAL PRIMARY KEY,
                        error_hash TEXT NOT NULL,
                        error_type TEXT NOT NULL,
                        error_message TEXT NOT NULL,
                        module TEXT NOT NULL,
                        function TEXT NOT NULL,
                        line_number INTEGER NOT NULL,
                        stack_trace TEXT,
                        first_seen TEXT NOT NULL,
                        last_seen TEXT NOT NULL,
                        occurrence_count INTEGER DEFAULT 1,
                        severity TEXT NOT NULL CHECK (severity IN ('critical', 'error', 'warning')),
                        resolved INTEGER DEFAULT 0,
                        resolved_at TEXT,
                        resolution_notes TEXT,
                        user_id TEXT,
                        session_id TEXT,
                        context TEXT
                    )
                """
    )
