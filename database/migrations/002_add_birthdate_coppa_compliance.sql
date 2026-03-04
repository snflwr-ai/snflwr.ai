-- Migration: Add birthdate and COPPA compliance fields
-- Version: 002
-- Date: 2025-12-28
-- Purpose: Add birthdate field for accurate age verification and COPPA compliance

-- Add birthdate to child_profiles
ALTER TABLE child_profiles ADD COLUMN birthdate TEXT;  -- ISO 8601 date (YYYY-MM-DD)

-- Add parental consent tracking
ALTER TABLE child_profiles ADD COLUMN parental_consent_given INTEGER DEFAULT 0;
ALTER TABLE child_profiles ADD COLUMN parental_consent_date TEXT;
ALTER TABLE child_profiles ADD COLUMN parental_consent_method TEXT;  -- 'email_verification', 'electronic_signature', 'fax', 'mail'

-- Add COPPA-specific fields
ALTER TABLE child_profiles ADD COLUMN coppa_verified INTEGER DEFAULT 0;  -- 1 if age < 13 and consent obtained
ALTER TABLE child_profiles ADD COLUMN age_verified_at TEXT;  -- When age was last verified

-- Create index for compliance queries
CREATE INDEX IF NOT EXISTS idx_profiles_underage ON child_profiles(age) WHERE age < 13;
CREATE INDEX IF NOT EXISTS idx_profiles_consent ON child_profiles(parental_consent_given, coppa_verified);

-- Create parental_consent_log table for audit trail
CREATE TABLE IF NOT EXISTS parental_consent_log (
    consent_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    consent_type TEXT NOT NULL CHECK (consent_type IN ('initial', 'renewed', 'revoked')),
    consent_method TEXT NOT NULL,
    consent_date TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    electronic_signature TEXT,  -- Parent's typed name or signature data
    verification_token TEXT,     -- Email verification token if applicable
    verified_at TEXT,
    is_active INTEGER DEFAULT 1,
    notes TEXT,
    FOREIGN KEY (profile_id) REFERENCES child_profiles(profile_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_consent_profile ON parental_consent_log(profile_id);
CREATE INDEX IF NOT EXISTS idx_consent_parent ON parental_consent_log(parent_id);
CREATE INDEX IF NOT EXISTS idx_consent_date ON parental_consent_log(consent_date);
CREATE INDEX IF NOT EXISTS idx_consent_active ON parental_consent_log(is_active);

-- Data migration: Set consent for existing profiles (assume parental consent for existing data)
UPDATE child_profiles
SET
    parental_consent_given = 1,
    parental_consent_date = created_at,
    parental_consent_method = 'grandfathered',
    coppa_verified = CASE WHEN age < 13 THEN 1 ELSE 0 END,
    age_verified_at = created_at
WHERE parental_consent_given IS NULL OR parental_consent_given = 0;
