-- Database Migration: Add Privacy Policy Version Tracking
-- COPPA/FERPA Compliance Enhancement
-- Date: 2025-12-27
-- Purpose: Track which version of privacy policy and terms each user accepted

-- Add privacy policy version tracking columns to users table
ALTER TABLE users ADD COLUMN privacy_policy_version TEXT;
ALTER TABLE users ADD COLUMN privacy_policy_accepted_date TEXT;
ALTER TABLE users ADD COLUMN terms_accepted_version TEXT;
ALTER TABLE users ADD COLUMN terms_accepted_date TEXT;

-- Update existing users to current version (if any exist)
-- This assumes existing users accepted the current version when they registered
UPDATE users
SET privacy_policy_version = '1.0',
    privacy_policy_accepted_date = created_at,
    terms_accepted_version = '1.0',
    terms_accepted_date = created_at
WHERE privacy_policy_version IS NULL;

-- Create index for compliance auditing
CREATE INDEX IF NOT EXISTS idx_users_privacy_policy_version ON users(privacy_policy_version);

-- Verification query
SELECT
    COUNT(*) as total_users,
    COUNT(privacy_policy_version) as users_with_policy_version,
    privacy_policy_version,
    COUNT(*) as count_per_version
FROM users
GROUP BY privacy_policy_version;
