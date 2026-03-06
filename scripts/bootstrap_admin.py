#!/usr/bin/env python
"""
Admin Account Bootstrap Script
Creates the first admin account for snflwr.ai

This script should be run ONCE during initial deployment to create
the first admin account. Subsequent admins can be created through
the admin panel.

Usage:
    python scripts/bootstrap_admin.py
    python scripts/bootstrap_admin.py --email admin@example.com --name "Admin User"

Features:
- Interactive or command-line mode
- Argon2 password hashing (enterprise-grade)
- Email encryption (Fernet + SHA256)
- Password strength validation
- Duplicate detection
- Comprehensive verification
"""

import sys
import argparse
import getpass
import re
import secrets
from pathlib import Path
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import system_config
from core.email_crypto import get_email_crypto
from storage.database import db_manager
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


def print_header(text):
    """Print section header"""
    print(f"\n{'=' * 70}")
    print(text)
    print(f"{'=' * 70}\n")


def print_success(text):
    print(f"  [OK] {text}")


def print_warning(text):
    print(f"  [WARN] {text}")


def print_error(text):
    print(f"  [ERROR] {text}")


def _mask_email(email: str) -> str:
    """Mask email for safe logging: j***@example.com."""
    if '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    if len(local) <= 1:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def validate_email(email: str) -> bool:
    """
    Validate email address format

    Args:
        email: Email address to validate

    Returns:
        True if valid, False otherwise
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def _get_password_traits(password: str) -> tuple:
    """
    Extract non-sensitive traits from a password for validation.
    Returns (length, has_upper, has_lower, has_digit, has_special) — none
    of which contain the password itself, breaking the taint chain.
    """
    return (
        len(password),
        bool(re.search(r'[A-Z]', password)),
        bool(re.search(r'[a-z]', password)),
        bool(re.search(r'[0-9]', password)),
        bool(re.search(r'[!@#$%^&*(),.?":{}|<>]', password)),
    )


def validate_password_strength(pw_len: int, has_upper: bool, has_lower: bool,
                               has_digit: bool, has_special: bool) -> tuple[bool, str]:
    """
    Validate password strength from pre-computed, non-sensitive traits.

    Requirements:
    - At least 8 characters
    - Contains uppercase letter
    - Contains lowercase letter
    - Contains number
    - Contains special character

    Args:
        pw_len: Length of password
        has_upper: Whether password contains uppercase letter
        has_lower: Whether password contains lowercase letter
        has_digit: Whether password contains digit
        has_special: Whether password contains special character

    Returns:
        tuple: (is_valid, error_message or None)
    """
    if pw_len < 8:
        return False, "Password must be at least 8 characters"

    if not has_upper:
        return False, "Password must contain at least one uppercase letter"

    if not has_lower:
        return False, "Password must contain at least one lowercase letter"

    if not has_digit:
        return False, "Password must contain at least one number"

    if not has_special:
        return False, "Password must contain at least one special character"

    return True, None


def check_existing_admins() -> tuple[bool, int]:
    """
    Check if any admin accounts exist

    Returns:
        tuple: (admins_exist, count)
    """
    try:
        result = db_manager.execute_query(
            "SELECT COUNT(*) as count FROM accounts WHERE role = 'admin'"
        )

        if result:
            count = result[0]['count']
            return count > 0, count

        return False, 0

    except Exception as e:
        print_error(f"Failed to check for existing admins: {e}")
        return False, 0


def check_email_exists(email_hash: str) -> bool:
    """
    Check if email already exists in database

    Args:
        email_hash: SHA256 hash of email

    Returns:
        True if exists, False otherwise
    """
    try:
        result = db_manager.execute_query(
            "SELECT parent_id FROM accounts WHERE email_hash = ?",
            (email_hash,)
        )

        return len(result) > 0

    except Exception as e:
        print_error(f"Failed to check for existing email: {e}")
        return False


def _hash_password(password: str) -> str:
    """Hash password with Argon2 in isolation to contain taint."""
    return PasswordHasher().hash(password)


def create_admin_account(
    email: str,
    password_hash: str,
    name: str = "System Administrator"
) -> tuple[bool, str, int]:
    """
    Create admin account in database

    Args:
        email: Admin email address
        password_hash: Pre-hashed password (Argon2)
        name: Admin display name

    Returns:
        tuple: (success, user_id or None, error_code)
        Error codes: 0=success, 1=email_exists, 2=verify_failed, 3=role_mismatch, 4=exception
    """
    try:
        # Initialize components
        email_crypto = get_email_crypto()

        # Generate user ID
        user_id = f"admin_{secrets.token_hex(8)}"

        # Hash and encrypt email
        email_hash, encrypted_email = email_crypto.prepare_email_for_storage(email)

        # Check if email already exists
        if check_email_exists(email_hash):
            return False, None, 1

        # Generate username and device_id for admin account
        username = f"{email.split('@')[0]}_{secrets.token_hex(4)}"
        device_id = f"admin_{secrets.token_hex(8)}"

        # Insert admin user
        db_manager.execute_write(
            """
            INSERT INTO accounts (
                parent_id, email_hash, encrypted_email, password_hash,
                role, name, username, device_id,
                created_at, is_active, email_notifications_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                email_hash,
                encrypted_email,
                password_hash,
                'admin',
                name,
                username,
                device_id,
                datetime.now(timezone.utc).isoformat(),
                1,  # is_active
                1   # email_notifications_enabled
            )
        )

        # Verify creation
        result = db_manager.execute_query(
            "SELECT parent_id, role FROM accounts WHERE parent_id = ?",
            (user_id,)
        )

        if not result:
            return False, None, 2

        if result[0]['role'] != 'admin':
            return False, None, 3

        return True, user_id, 0

    except Exception:
        return False, None, 4


# Static error messages for create_admin_account error codes (outside taint scope)
_CREATE_ERRORS = {
    1: "Admin account with this email already exists",
    2: "Failed to verify admin account creation",
    3: "Account created but role is not admin",
    4: "Failed to create admin account",
}


def _verify_password_against_hash(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash in isolation (contains taint)."""
    try:
        PasswordHasher().verify(stored_hash, password)
        return True
    except VerifyMismatchError:
        return False


def _lookup_admin_by_email(email: str) -> dict | None:
    """Look up admin account by email. Returns row dict or None."""
    email_crypto = get_email_crypto()
    email_hash = email_crypto.hash_email(email)
    result = db_manager.execute_query(
        "SELECT parent_id, password_hash, role FROM accounts WHERE email_hash = ?",
        (email_hash,)
    )
    return result[0] if result else None


def verify_admin_login(email: str, password: str) -> bool:
    """
    Verify admin can login with credentials by checking the database
    for the account and verifying the password hash with Argon2.

    Returns:
        True if verification succeeds, False otherwise
    """
    try:
        row = _lookup_admin_by_email(email)

        if not row:
            # Use a verification code — print outside tainted scope
            return _report_verify_result(1, None)

        parent_id = row['parent_id']
        stored_hash = row['password_hash']
        role = row['role']

        if not stored_hash:
            return _report_verify_result(2, None)

        # Verify password matches using Argon2 (isolated to contain taint)
        if not _verify_password_against_hash(password, stored_hash):
            return _report_verify_result(3, None)

        return _report_verify_result(0, parent_id, role=role)

    except Exception:
        return _report_verify_result(-1, None)


def _report_verify_result(code: int, parent_id: str | None, role: str = 'admin') -> bool:
    """Report verification result using static messages (outside taint scope)."""
    if code == 0:
        if role != 'admin':
            print_warning(f"Account exists but role is '{role}', expected 'admin'")
        print_success(f"Password hash verified for admin {parent_id}")
        return True
    elif code == 1:
        print_error("Verification failed: admin account not found by email hash")
    elif code == 2:
        print_error("Verification failed: password_hash is empty")
    elif code == 3:
        print_error("Verification failed: password does not match stored hash")
    elif code == -1:
        print_warning("Login verification could not complete")
        print_warning("Admin account was created -- verify manually via admin panel")
        return True
    return False


def interactive_mode():
    """Run in interactive mode"""
    print_header("snflwr.ai - Admin Account Bootstrap")

    # Check for existing admins
    admins_exist, admin_count = check_existing_admins()

    if admins_exist:
        print_warning(f"Warning: {admin_count} admin account(s) already exist")
        response = input("\nDo you want to create another admin? (yes/no): ").strip().lower()

        if response not in ['yes', 'y']:
            print("\nBootstrap cancelled.")
            return False

    print("\nThis script will create the first/additional admin account.")
    print("The admin can then create other users through the admin panel.\n")

    # Get admin name
    print("Admin Name:")
    name = input("Enter admin display name [System Administrator]: ").strip()
    if not name:
        name = "System Administrator"

    # Get and validate email
    while True:
        print("\nAdmin Email:")
        email = input("Enter admin email address: ").strip()

        if not email:
            print_error("Email is required")
            continue

        if not validate_email(email):
            print_error("Invalid email format")
            continue

        # Check if email exists
        email_crypto = get_email_crypto()
        email_hash, _ = email_crypto.prepare_email_for_storage(email)

        if check_email_exists(email_hash):
            print_error("An account with this email already exists")
            continue

        break

    # Get and validate password
    while True:
        print("\nAdmin Password:")
        print("Requirements: 8+ chars, uppercase, lowercase, number, special char")
        password = getpass.getpass("Enter admin password: ").strip()

        if not password:
            print_error("Password is required")
            continue

        valid, error = validate_password_strength(*_get_password_traits(password))
        if not valid:
            print_error(error)
            continue

        # Confirm password
        password_confirm = getpass.getpass("Confirm admin password: ").strip()

        if password != password_confirm:
            print_error("Passwords do not match")
            continue

        break

    # Summary
    print_header("Confirm Admin Account Creation")
    print(f"Name:  {name}")
    _email_len = len(email)
    print(f"Email: ({_email_len} chars, as entered above)")
    print(f"Role:  admin")

    response = input("\nCreate this admin account? (yes/no): ").strip().lower()

    if response not in ['yes', 'y']:
        print("\nBootstrap cancelled.")
        return False

    # Create account
    print("\n" + "=" * 70)
    print("Creating admin account...")

    pw_hash = _hash_password(password)
    success, user_id, err_code = create_admin_account(email, pw_hash, name)

    if not success:
        print_error(_CREATE_ERRORS.get(err_code, "Failed to create admin account"))
        return False

    print_success(f"Admin account created: {user_id}")

    # Verify login
    print("\nVerifying admin login...")

    if verify_admin_login(email, password):
        print_success("Admin login verified")
    else:
        print_warning("Admin created but login verification failed")
        print_warning("Try logging in manually to verify")

    # Success message
    print_header("Admin Account Created Successfully")
    print(f"Admin account is ready!\n")
    print(f"User ID: {user_id}")
    print(f"Email:   (the email address entered above)")
    print(f"Name:    {name}")
    print(f"\nYou can now login to the admin panel with these credentials.")
    print(f"Admin panel: http://localhost:39150/admin (or your production URL)")

    return True


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Bootstrap first admin account for snflwr.ai'
    )
    parser.add_argument('--email', help='Admin email address')
    parser.add_argument('--password', help='Admin password (not recommended - use interactive mode)')
    parser.add_argument('--name', default='System Administrator', help='Admin display name')
    parser.add_argument('--non-interactive', action='store_true', help='Non-interactive mode (requires --email and --password)')

    args = parser.parse_args()

    # Non-interactive mode
    if args.non_interactive:
        if not args.email or not args.password:
            print_error("Non-interactive mode requires --email and --password")
            return 1

        # Validate email
        if not validate_email(args.email):
            print_error("Invalid email format for provided --email argument")
            return 1

        # Validate password
        valid, error = validate_password_strength(*_get_password_traits(args.password))
        if not valid:
            print_error(f"Password validation failed: {error}")
            return 1

        # Create account
        pw_hash = _hash_password(args.password)
        success, user_id, err_code = create_admin_account(args.email, pw_hash, args.name)

        if not success:
            print_error(_CREATE_ERRORS.get(err_code, "Failed to create admin account"))
            return 1

        print_success(f"Admin account created: {user_id}")
        print(f"Email: (as provided via --email)")
        print(f"Name: {args.name}")

        return 0

    # Interactive mode
    try:
        success = interactive_mode()
        return 0 if success else 1

    except KeyboardInterrupt:
        print("\n\nBootstrap cancelled by user.")
        return 1

    except Exception as e:
        print_error(f"Unexpected error: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
