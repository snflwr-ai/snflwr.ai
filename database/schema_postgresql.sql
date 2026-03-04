-- snflwr.ai Database Schema - PostgreSQL
-- Optimized for production PostgreSQL deployments

-- Users table (parents and admins)
-- NOTE: Emails are encrypted at rest for COPPA/privacy compliance
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email_hash TEXT UNIQUE NOT NULL,  -- SHA256 hash for fast lookup
    encrypted_email TEXT NOT NULL,     -- Fernet encrypted actual email (PII)
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'parent', 'user')),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_login TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    email_verified BOOLEAN DEFAULT FALSE,
    name TEXT DEFAULT 'User',
    email_notifications_enabled BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_users_email_hash ON users(email_hash);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_created ON users(created_at);

-- Authentication sessions
-- NOTE: session_id stores SHA-256 hashed tokens, not plaintext session tokens.
CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON auth_sessions(is_active, expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON auth_sessions(expires_at);

-- Child profiles
CREATE TABLE IF NOT EXISTS child_profiles (
    profile_id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age >= 0 AND age <= 18),
    grade_level TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('budget', 'standard', 'premium')),
    model_role TEXT NOT NULL CHECK (model_role IN ('student', 'educator')),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    avatar_url TEXT,
    preferences JSONB,  -- PostgreSQL native JSON type
    -- COPPA compliance fields
    birthdate TEXT,
    parental_consent_given BOOLEAN DEFAULT FALSE,
    parental_consent_date TEXT,
    parental_consent_method TEXT,
    coppa_verified BOOLEAN DEFAULT FALSE,
    age_verified_at TEXT,
    grade TEXT,
    daily_time_limit_minutes INTEGER DEFAULT 120,
    total_sessions INTEGER DEFAULT 0,
    total_questions INTEGER DEFAULT 0,
    last_active TEXT,
    owui_user_id TEXT,
    FOREIGN KEY (parent_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_profiles_parent ON child_profiles(parent_id);
CREATE INDEX IF NOT EXISTS idx_profiles_active ON child_profiles(is_active);
CREATE INDEX IF NOT EXISTS idx_profiles_created ON child_profiles(created_at);

-- Conversation sessions
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_profile ON conversation_sessions(profile_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON conversation_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON conversation_sessions(is_active);
CREATE INDEX IF NOT EXISTS idx_sessions_ended ON conversation_sessions(ended_at);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    tokens INTEGER DEFAULT 0,
    filtered BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_filtered ON messages(filtered);

-- Search index for encrypted conversations (HMAC token hashes)
CREATE TABLE IF NOT EXISTS message_search_index (
    id SERIAL PRIMARY KEY,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(message_id) ON DELETE CASCADE,
    FOREIGN KEY (conversation_id) REFERENCES conversation_sessions(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_search_token ON message_search_index(token_hash);
CREATE INDEX IF NOT EXISTS idx_search_conversation ON message_search_index(conversation_id);
CREATE INDEX IF NOT EXISTS idx_search_message ON message_search_index(message_id);

-- Safety incidents
CREATE TABLE IF NOT EXISTS safety_incidents (
    incident_id SERIAL PRIMARY KEY,
    profile_id TEXT NOT NULL,
    session_id TEXT,
    incident_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('minor', 'major', 'critical')),
    content_snippet TEXT,
    triggered_keywords TEXT,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    parent_notified BOOLEAN DEFAULT FALSE,
    parent_notified_at TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    metadata TEXT,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_profile ON safety_incidents(profile_id);
CREATE INDEX IF NOT EXISTS idx_incidents_session ON safety_incidents(session_id);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON safety_incidents(severity);
CREATE INDEX IF NOT EXISTS idx_incidents_timestamp ON safety_incidents(timestamp);
CREATE INDEX IF NOT EXISTS idx_incidents_notified ON safety_incidents(parent_notified);

-- Parent alerts (generated from safety incidents)
CREATE TABLE IF NOT EXISTS parent_alerts (
    alert_id SERIAL PRIMARY KEY,
    parent_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('minor', 'major', 'critical')),
    message TEXT NOT NULL,
    related_incident_id INTEGER,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_at TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES users(user_id) ON DELETE CASCADE,
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
    reset_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
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
    require_approval BOOLEAN DEFAULT FALSE,
    enable_web_search BOOLEAN DEFAULT TRUE,
    enable_file_upload BOOLEAN DEFAULT FALSE,
    enable_code_execution BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
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
    metadata JSONB,  -- PostgreSQL native JSON type
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_profile ON activity_log(profile_id);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_activity_type ON activity_log(activity_type);

-- Safety filter cache (for performance)
CREATE TABLE IF NOT EXISTS safety_filter_cache (
    cache_id TEXT PRIMARY KEY,
    content_hash TEXT UNIQUE NOT NULL,
    is_safe BOOLEAN NOT NULL,
    severity TEXT,
    reason TEXT,
    triggered_keywords TEXT,
    cached_at TIMESTAMP NOT NULL DEFAULT NOW(),
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
    last_used TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_usage_profile ON model_usage(profile_id);
CREATE INDEX IF NOT EXISTS idx_usage_model ON model_usage(model_name);
CREATE INDEX IF NOT EXISTS idx_usage_last_used ON model_usage(last_used);

-- System configuration (for admin settings)
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    setting_type TEXT NOT NULL CHECK (setting_type IN ('string', 'integer', 'boolean', 'json')),
    description TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_settings_key ON system_settings(setting_key);

-- Error tracking (production monitoring)
CREATE TABLE IF NOT EXISTS error_tracking (
    error_id BIGSERIAL PRIMARY KEY,  -- PostgreSQL auto-increment
    error_hash TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    module TEXT NOT NULL,
    function TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    stack_trace TEXT,
    first_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMP NOT NULL DEFAULT NOW(),
    occurrence_count INTEGER DEFAULT 1,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'error', 'warning')),
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP,
    resolution_notes TEXT,
    user_id TEXT,
    session_id TEXT,
    context TEXT
);

CREATE INDEX IF NOT EXISTS idx_errors_hash ON error_tracking(error_hash);
CREATE INDEX IF NOT EXISTS idx_errors_severity ON error_tracking(severity);
CREATE INDEX IF NOT EXISTS idx_errors_first_seen ON error_tracking(first_seen);
CREATE INDEX IF NOT EXISTS idx_errors_unresolved ON error_tracking(resolved) WHERE resolved = FALSE;

-- Audit log (security monitoring)
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id BIGSERIAL PRIMARY KEY,  -- PostgreSQL auto-increment
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_type TEXT NOT NULL CHECK (user_type IN ('admin', 'parent', 'user')),
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details TEXT,
    ip_address INET,  -- PostgreSQL native IP address type
    user_agent TEXT,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_success ON audit_log(success);
CREATE INDEX IF NOT EXISTS idx_audit_ip ON audit_log(ip_address);

-- Email notification queue (for reliable delivery)
CREATE TABLE IF NOT EXISTS email_queue (
    email_id BIGSERIAL PRIMARY KEY,
    recipient_user_id TEXT NOT NULL,
    recipient_email TEXT NOT NULL,  -- Should store encrypted email; decrypt at send time
    subject TEXT NOT NULL,
    body_html TEXT NOT NULL,
    email_type TEXT NOT NULL,
    priority INTEGER DEFAULT 5,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'failed')) DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMP,
    error_message TEXT,
    FOREIGN KEY (recipient_user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_email_queue_status ON email_queue(status, created_at);
CREATE INDEX IF NOT EXISTS idx_email_queue_recipient ON email_queue(recipient_user_id);
CREATE INDEX IF NOT EXISTS idx_email_queue_created ON email_queue(created_at);

-- Authentication tokens (email verification, password reset)
-- NOTE: token_hash stores SHA-256 hashed values. Raw tokens are never persisted.
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_type TEXT NOT NULL CHECK (token_type IN ('email_verification', 'password_reset')),
    token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    is_valid BOOLEAN DEFAULT TRUE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_tokens_hash ON auth_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_tokens_type ON auth_tokens(token_type);
CREATE INDEX IF NOT EXISTS idx_tokens_expires ON auth_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_tokens_valid ON auth_tokens(is_valid) WHERE is_valid = TRUE;
