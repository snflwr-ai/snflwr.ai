-- snflwr.ai Database Schema
-- PostgreSQL and SQLite compatible

-- Accounts table (renamed from parents — holds parents, admins, educators)
-- NOTE: Emails are encrypted at rest for COPPA/privacy compliance
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
    is_active INTEGER DEFAULT 1,
    email_notifications_enabled INTEGER DEFAULT 1,
    email_verified INTEGER DEFAULT 0,
    deletion_requested_at TEXT,
    owui_token TEXT
);

CREATE INDEX IF NOT EXISTS idx_accounts_username ON accounts(username);
CREATE INDEX IF NOT EXISTS idx_accounts_device ON accounts(device_id);
CREATE INDEX IF NOT EXISTS idx_accounts_email_hash ON accounts(email_hash);
CREATE INDEX IF NOT EXISTS idx_accounts_role ON accounts(role);

-- Authentication tokens (sessions, email verification, password reset)
-- NOTE: session_token stores SHA-256 hashed values for session types.
-- token_hash stores SHA-256 hashes for email_verification/password_reset types.
-- Raw tokens are never persisted to the database.
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
    is_valid INTEGER DEFAULT 1,
    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
    CONSTRAINT valid_token_type CHECK (token_type IN ('session', 'email_verification', 'password_reset'))
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_session ON auth_tokens(session_token);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_expires ON auth_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_tokens_valid ON auth_tokens(is_valid) WHERE is_valid = 1;

-- Child profiles
CREATE TABLE IF NOT EXISTS child_profiles (
    profile_id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age >= 0 AND age <= 18),
    grade TEXT,                                  -- Used by ProfileManager (alias for grade_level)
    grade_level TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'standard',
    model_role TEXT NOT NULL CHECK (model_role IN ('student', 'educator')),
    created_at TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    avatar TEXT DEFAULT 'default',               -- Used by ProfileManager (alias for avatar_url)
    avatar_url TEXT,
    preferences TEXT,
    learning_level TEXT DEFAULT 'adaptive',
    daily_time_limit_minutes INTEGER DEFAULT 120,
    total_sessions INTEGER DEFAULT 0,
    total_questions INTEGER DEFAULT 0,
    last_active TEXT,
    -- COPPA compliance fields
    birthdate TEXT,                              -- ISO 8601 date (YYYY-MM-DD)
    parental_consent_given INTEGER DEFAULT 0,
    parental_consent_date TEXT,
    parental_consent_method TEXT,                -- 'email_verification', 'electronic_signature', etc.
    coppa_verified INTEGER DEFAULT 0,            -- 1 if age < 13 and consent obtained
    age_verified_at TEXT,                        -- When age was last verified
    owui_user_id TEXT,                           -- Open WebUI user ID (for direct student login)
    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_profiles_owui_user ON child_profiles(owui_user_id);

CREATE INDEX IF NOT EXISTS idx_profiles_parent ON child_profiles(parent_id);
CREATE INDEX IF NOT EXISTS idx_profiles_active ON child_profiles(is_active);
CREATE INDEX IF NOT EXISTS idx_profiles_underage ON child_profiles(age) WHERE age < 13;
CREATE INDEX IF NOT EXISTS idx_profiles_consent ON child_profiles(parental_consent_given, coppa_verified);

-- Profile subject preferences (used by ProfileManager)
CREATE TABLE IF NOT EXISTS profile_subjects (
    id INTEGER PRIMARY KEY,
    profile_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    added_at TEXT,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_profile_subjects_profile ON profile_subjects(profile_id);

-- Parental consent audit log (COPPA compliance)
CREATE TABLE IF NOT EXISTS parental_consent_log (
    consent_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    consent_type TEXT NOT NULL CHECK (consent_type IN ('initial', 'renewed', 'revoked')),
    consent_method TEXT NOT NULL,
    consent_date TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    electronic_signature TEXT,
    verification_token TEXT,
    verified_at TEXT,
    is_active INTEGER DEFAULT 1,
    notes TEXT,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_consent_profile ON parental_consent_log(profile_id);
CREATE INDEX IF NOT EXISTS idx_consent_parent ON parental_consent_log(parent_id);

-- Sessions table (runtime session management)
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
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
    CONSTRAINT valid_session_type CHECK (session_type IN ('student', 'parent', 'educator'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions(profile_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);

-- Conversations (used by conversation_store for chat history)
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    session_id TEXT,
    profile_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    subject_area TEXT,
    is_flagged INTEGER DEFAULT 0,
    flag_reason TEXT,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_profile ON conversations(profile_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    session_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    tokens INTEGER DEFAULT 0,
    tokens_used INTEGER,
    model_used TEXT,
    response_time_ms INTEGER,
    filtered INTEGER DEFAULT 0,
    safety_filtered INTEGER DEFAULT 0,
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_filtered ON messages(filtered);

-- Search index for encrypted conversations (HMAC token hashes)
CREATE TABLE IF NOT EXISTS message_search_index (
    id INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_search_token ON message_search_index(token_hash);
CREATE INDEX IF NOT EXISTS idx_search_conversation ON message_search_index(conversation_id);
CREATE INDEX IF NOT EXISTS idx_search_message ON message_search_index(message_id);

-- Safety incidents
CREATE TABLE IF NOT EXISTS safety_incidents (
    incident_id INTEGER PRIMARY KEY,
    profile_id TEXT NOT NULL,
    session_id TEXT,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('minor', 'major', 'critical')),
    content_snippet TEXT,
    triggered_keywords TEXT,
    timestamp TEXT NOT NULL,
    parent_notified INTEGER DEFAULT 0,
    parent_notified_at TEXT,
    resolved INTEGER DEFAULT 0,
    resolved_at TEXT,
    resolution_notes TEXT,
    metadata TEXT,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_profile ON safety_incidents(profile_id);
CREATE INDEX IF NOT EXISTS idx_incidents_session ON safety_incidents(session_id);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON safety_incidents(severity);
CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON safety_incidents(timestamp);
CREATE INDEX IF NOT EXISTS idx_incidents_notified ON safety_incidents(parent_notified);

-- Parent alerts (generated from safety incidents)
CREATE TABLE IF NOT EXISTS parent_alerts (
    alert_id INTEGER PRIMARY KEY,
    parent_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('minor', 'major', 'critical')),
    message TEXT NOT NULL,
    related_incident_id INTEGER,
    timestamp TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0,
    acknowledged_at TEXT,
    FOREIGN KEY (parent_id) REFERENCES accounts(parent_id) ON DELETE CASCADE,
    FOREIGN KEY (related_incident_id) REFERENCES safety_incidents(incident_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_parent ON parent_alerts(parent_id);
CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON parent_alerts(acknowledged);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON parent_alerts(timestamp);

-- Usage limits and quotas
CREATE TABLE IF NOT EXISTS usage_quotas (
    quota_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    quota_type TEXT NOT NULL CHECK (quota_type IN ('daily_messages', 'daily_tokens', 'session_duration')),
    limit_value INTEGER NOT NULL,
    current_value INTEGER DEFAULT 0,
    reset_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_quotas_profile ON usage_quotas(profile_id);
CREATE INDEX IF NOT EXISTS idx_quotas_reset ON usage_quotas(reset_at);

-- Parental controls
CREATE TABLE IF NOT EXISTS parental_controls (
    control_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL UNIQUE,
    allowed_models TEXT,
    blocked_topics TEXT,
    time_restrictions TEXT,
    daily_message_limit INTEGER DEFAULT -1,
    require_approval INTEGER DEFAULT 0,
    enable_web_search INTEGER DEFAULT 1,
    enable_file_upload INTEGER DEFAULT 0,
    enable_code_execution INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_controls_profile ON parental_controls(profile_id);

-- Activity log (for parent dashboard)
CREATE TABLE IF NOT EXISTS activity_log (
    log_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    session_id TEXT,
    activity_type TEXT NOT NULL,
    description TEXT NOT NULL,
    metadata TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_profile ON activity_log(profile_id);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_activity_type ON activity_log(activity_type);

-- Safety filter cache (for performance)
CREATE TABLE IF NOT EXISTS safety_filter_cache (
    cache_id TEXT PRIMARY KEY,
    content_hash TEXT UNIQUE NOT NULL,
    is_safe INTEGER NOT NULL,
    severity TEXT,
    reason TEXT,
    triggered_keywords TEXT,
    cached_at TEXT NOT NULL,
    hit_count INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_cache_hash ON safety_filter_cache(content_hash);
CREATE INDEX IF NOT EXISTS idx_cache_time ON safety_filter_cache(cached_at);

-- Model usage tracking
CREATE TABLE IF NOT EXISTS model_usage (
    usage_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    request_count INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    total_duration_seconds INTEGER DEFAULT 0,
    last_used TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_usage_profile ON model_usage(profile_id);
CREATE INDEX IF NOT EXISTS idx_usage_model ON model_usage(model_name);

-- System configuration (for admin settings)
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    setting_type TEXT NOT NULL CHECK (setting_type IN ('string', 'integer', 'boolean', 'json')),
    description TEXT,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_settings_key ON system_settings(setting_key);

-- Error tracking (production monitoring)
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
);

CREATE INDEX IF NOT EXISTS idx_errors_hash ON error_tracking(error_hash);
CREATE INDEX IF NOT EXISTS idx_errors_severity ON error_tracking(severity);
CREATE INDEX IF NOT EXISTS idx_errors_first_seen ON error_tracking(first_seen);
CREATE INDEX IF NOT EXISTS idx_errors_unresolved ON error_tracking(resolved) WHERE resolved = 0;

-- Audit log (security monitoring)
CREATE TABLE IF NOT EXISTS audit_log (
    log_id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    user_id TEXT,
    user_type TEXT,
    action TEXT NOT NULL,
    details TEXT,
    ip_address TEXT,
    success INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_success ON audit_log(success);

-- Learning analytics
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
);

CREATE INDEX IF NOT EXISTS idx_analytics_profile_date ON learning_analytics(profile_id, date);
CREATE INDEX IF NOT EXISTS idx_analytics_date ON learning_analytics(date);
