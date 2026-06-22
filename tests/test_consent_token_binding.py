"""C2: parental-consent tokens are profile-bound and cannot collide with
account email-verification tokens (which hash the bare token)."""
import hashlib

from core.age_verification import (
    generate_consent_verification_token,
    consent_token_hash,
    verify_consent_token,
)


def test_hash_is_profile_bound():
    token, stored = generate_consent_verification_token("parent1", "profileA")
    # Same token, different profile => different hash (cannot reuse across kids).
    assert consent_token_hash(token, "profileA") == stored
    assert consent_token_hash(token, "profileB") != stored


def test_hash_differs_from_bare_account_token_hash():
    """An account email-verification token hashes the bare token; a consent
    token folds in profile_id — so they can never match the same lookup."""
    token, stored = generate_consent_verification_token("parent1", "profileA")
    bare = hashlib.sha256(token.encode()).hexdigest()  # account-flow hashing
    assert stored != bare


def test_verify_consent_token_requires_matching_profile():
    token, stored = generate_consent_verification_token("parent1", "profileA")
    assert verify_consent_token(token, stored, "parent1", "profileA") is True
    # Replay against another profile fails.
    assert verify_consent_token(token, stored, "parent1", "profileB") is False
    # A bare-token hash (account flow) does not verify as consent.
    bare = hashlib.sha256(token.encode()).hexdigest()
    assert verify_consent_token(token, bare, "parent1", "profileA") is False
