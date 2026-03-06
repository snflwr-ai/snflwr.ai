#!/usr/bin/env python3
"""
snflwr.ai — Interactive Production Setup

Walks you through setting up snflwr.ai for production use.
Generates all security keys automatically, asks only the questions
that require human input, and creates a ready-to-use .env file.

Usage:
    python scripts/setup_production.py

No technical knowledge required. Just answer the prompts.
"""

import os
import sys
import secrets
import string
from pathlib import Path

# Add parent directory so we can import project modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bold(text):
    return text

def _green(text):
    return text

def _yellow(text):
    return text

def _red(text):
    return text

def _blue(text):
    return text

def _mask_secret(value: str, visible: int = 4) -> str:
    """Show only last N chars of a secret for verification."""
    s = str(value)
    if len(s) <= visible:
        return '***'
    return f"***{s[-visible:]}"

def banner():
    print()
    print("=" * 64)
    print("  snflwr.ai — Production Setup")
    print("  This will create a secure configuration for your deployment.")
    print("=" * 64)
    print()

def ask(prompt, default=None, secret=False, required=True, validator=None):
    """Ask the user a question with optional default and validation."""
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            answer = input(f"  {prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nSetup cancelled.")
            sys.exit(1)

        if not answer and default is not None:
            answer = default
        if required and not answer:
            print(_red("    This field is required. Please enter a value."))
            continue
        if validator:
            error = validator(answer)
            if error:
                print(_red(f"    {error}"))
                continue
        return answer

def ask_yes_no(prompt, default=True):
    """Ask a yes/no question."""
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            answer = input(f"  {prompt} [{hint}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n\nSetup cancelled.")
            sys.exit(1)
        if not answer:
            return default
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        print(_yellow("    Please type 'y' or 'n'."))

def ask_choice(prompt, choices):
    """Ask the user to pick from a numbered list."""
    print(f"  {prompt}")
    for i, (label, _value) in enumerate(choices, 1):
        print(f"    {i}. {label}")
    while True:
        try:
            answer = input(f"  Enter number [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nSetup cancelled.")
            sys.exit(1)
        if not answer:
            return choices[0][1]
        try:
            idx = int(answer) - 1
            if 0 <= idx < len(choices):
                return choices[idx][1]
        except ValueError:
            pass
        print(_yellow(f"    Please enter a number between 1 and {len(choices)}."))

def generate_secret(length=32):
    """Generate a cryptographically secure random string."""
    return secrets.token_hex(length)

def generate_password(length=24):
    """Generate a strong random password."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(chars) for _ in range(length))

def generate_fernet_key():
    """Generate a Fernet encryption key.

    The cryptography package is required and validated in main() before
    this function is called, so ImportError should never happen here.
    """
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()

def validate_email(email):
    """Basic email validation."""
    if '@' not in email or '.' not in email.split('@')[-1]:
        return "That doesn't look like a valid email address."
    return None

def validate_domain(domain):
    """Basic domain validation."""
    if domain in ('localhost', '127.0.0.1'):
        return None  # Allow localhost for home use
    if '.' not in domain:
        return "Please enter a full domain (e.g. school.example.com) or 'localhost'"
    if domain.startswith('http'):
        return "Just the domain name, without http:// (e.g. school.example.com)"
    return None


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------

def step_basics():
    """Collect basic deployment info."""
    print(_bold(_blue("\n--- Step 1 of 5: Basic Information ---\n")))
    print("  Let's start with some basics about where this will run.\n")

    domain = ask(
        "What domain will snflwr.ai be available at?\n"
        "  (e.g. learn.myschool.org or snflwr.example.com)\n"
        "  Domain",
        validator=validate_domain
    )

    admin_email = ask(
        "\n  What email should receive security alerts?\n"
        "  (This is for the system administrator, not students or parents)\n"
        "  Admin email",
        validator=validate_email
    )

    return {
        'domain': domain,
        'admin_email': admin_email,
        'base_url': f"https://{domain}",
    }


def step_database():
    """Configure database."""
    print(_bold(_blue("\n--- Step 2 of 5: Database ---\n")))
    print("  snflwr.ai needs a database to store profiles and conversations.\n")

    db_type = ask_choice(
        "Which database will you use?",
        [
            ("SQLite (simpler, good for small deployments)", "sqlite"),
            ("PostgreSQL (recommended for schools with many students)", "postgresql"),
        ]
    )

    config = {'db_type': db_type}

    if db_type == 'postgresql':
        print(_yellow("\n  You'll need a PostgreSQL server running. If you don't have one yet,"))
        print(_yellow("  the Docker setup (docker-compose.yml) includes one automatically.\n"))
        config['pg_host'] = ask("PostgreSQL host", default="localhost")
        config['pg_port'] = ask("PostgreSQL port", default="5432")
        config['pg_database'] = ask("Database name", default="snflwr_production")
        config['pg_user'] = ask("Database username", default="snflwr_app")
        config['pg_password'] = generate_password()
        print(_green(f"\n  A secure database password has been generated automatically."))
    else:
        print(_green("\n  SQLite selected — no extra setup needed."))
        print("  The database file will be created automatically when you start the app.\n")

    return config


def step_email():
    """Configure email for parent notifications."""
    print(_bold(_blue("\n--- Step 3 of 5: Parent Email Alerts ---\n")))
    print("  snflwr.ai can email parents when safety concerns are detected.")
    print("  This is important for COPPA compliance (protecting children's privacy).\n")

    enable_email = ask_yes_no("Do you want to enable parent email alerts?", default=True)

    if not enable_email:
        print(_yellow("\n  Email alerts disabled. You can enable them later by re-running this setup."))
        return {'smtp_enabled': False}

    print("\n  You'll need an email sending service. Common free options:")
    print("    - SendGrid (100 emails/day free)")
    print("    - Mailgun (5,000 emails/month free)")
    print("    - Your school's SMTP server\n")

    provider = ask_choice(
        "Which email service will you use?",
        [
            ("SendGrid", "sendgrid"),
            ("Mailgun", "mailgun"),
            ("Custom SMTP server", "custom"),
        ]
    )

    defaults = {
        'sendgrid': {'host': 'smtp.sendgrid.net', 'port': '587', 'user_prompt': 'SendGrid API key'},
        'mailgun': {'host': 'smtp.mailgun.org', 'port': '587', 'user_prompt': 'Mailgun SMTP password'},
        'custom': {'host': '', 'port': '587', 'user_prompt': 'SMTP password'},
    }

    d = defaults[provider]

    smtp_host = ask("SMTP server address", default=d['host']) if d['host'] else ask("SMTP server address")
    smtp_port = ask("SMTP port", default=d['port'])

    if provider == 'sendgrid':
        smtp_username = 'apikey'
        print(f"  SMTP username: apikey (this is correct for SendGrid)")
    else:
        smtp_username = ask("SMTP username")

    smtp_password = ask(f"{d['user_prompt']}")
    from_email = ask("Send emails from this address", default="noreply@snflwr.ai")

    return {
        'smtp_enabled': True,
        'smtp_host': smtp_host,
        'smtp_port': smtp_port,
        'smtp_username': smtp_username,
        'smtp_password': smtp_password,
        'smtp_from_email': from_email,
    }


def step_redis():
    """Configure Redis (optional)."""
    print(_bold(_blue("\n--- Step 4 of 5: Performance & Rate Limiting ---\n")))
    print("  Redis improves performance and prevents abuse (rate limiting).")
    print("  It's recommended for production but not strictly required.\n")

    use_redis = ask_yes_no("Will you be using Redis?", default=True)

    if not use_redis:
        print(_yellow("  Redis disabled. Rate limiting will use in-memory storage (single server only)."))
        return {'redis_enabled': False}

    redis_host = ask("Redis host", default="localhost")
    redis_port = ask("Redis port", default="6379")

    use_redis_password = ask_yes_no("Does your Redis server require a password?", default=False)
    redis_password = ask("Redis password") if use_redis_password else ""

    return {
        'redis_enabled': True,
        'redis_host': redis_host,
        'redis_port': redis_port,
        'redis_password': redis_password,
    }


def step_review_and_write(basics, database, email, redis):
    """Generate secrets, show summary, write .env file."""
    is_local = basics['domain'] in ('localhost', '127.0.0.1')
    step_label = "Saving configuration..." if is_local else "Step 5 of 5: Review & Save"
    print(_bold(_blue(f"\n--- {step_label} ---\n")))

    # Auto-generate all security keys
    print("  Generating secure keys automatically...\n")
    jwt_secret = generate_secret(32)
    session_secret = generate_secret(32)
    csrf_secret = generate_secret(32)
    internal_api_key = secrets.token_hex(32)
    webui_secret_key = secrets.token_hex(32)
    db_encryption_key = secrets.token_urlsafe(32)
    pii_encryption_key = generate_fernet_key()
    admin_password = generate_password(16)

    print(_green("  All security keys generated.\n"))

    # Build the .env content
    lines = []
    env_label = "Local" if is_local else "Production"
    lines.append("# =============================================================================")
    lines.append(f"# snflwr.ai — {env_label} Configuration")
    lines.append(f"# Generated by setup script")
    lines.append("# IMPORTANT: Keep this file secure. Never commit it to git.")
    lines.append("# =============================================================================")
    lines.append("")
    lines.append(f"ENVIRONMENT={'development' if is_local else 'production'}")
    lines.append(f"BASE_URL={basics['base_url']}")
    cors_origins = "http://localhost:3000,http://localhost:5173,http://localhost:39150,http://localhost:8080" if is_local else basics['base_url']
    lines.append(f"CORS_ORIGINS={cors_origins}")
    lines.append(f"ADMIN_EMAIL={basics['admin_email']}")
    lines.append("")

    # Authentication
    lines.append("# Authentication (auto-generated — do not change unless rotating)")
    lines.append(f"JWT_SECRET_KEY={jwt_secret}")
    lines.append(f"SESSION_SECRET={session_secret}")
    lines.append(f"CSRF_SECRET={csrf_secret}")
    lines.append(f"INTERNAL_API_KEY={internal_api_key}")
    lines.append(f"WEBUI_SECRET_KEY={webui_secret_key}")
    lines.append("")

    # Database
    lines.append("# Database")
    lines.append(f"DATABASE_TYPE={database['db_type']}")
    if database['db_type'] == 'postgresql':
        lines.append(f"POSTGRES_HOST={database['pg_host']}")
        lines.append(f"POSTGRES_PORT={database['pg_port']}")
        lines.append(f"POSTGRES_DATABASE={database['pg_database']}")
        lines.append(f"POSTGRES_USER={database['pg_user']}")
        lines.append(f"POSTGRES_PASSWORD={database['pg_password']}")
    lines.append("")

    # Encryption
    lines.append("# Encryption (auto-generated — BACK THESE UP to a password manager)")
    lines.append("DB_ENCRYPTION_ENABLED=true")
    lines.append(f"DB_ENCRYPTION_KEY={db_encryption_key}")
    lines.append("DB_KDF_ITERATIONS=256000")
    lines.append(f"ENCRYPTION_KEY={pii_encryption_key}")
    lines.append("")

    # Email
    lines.append("# Email Notifications")
    lines.append(f"SMTP_ENABLED={'true' if email.get('smtp_enabled') else 'false'}")
    if email.get('smtp_enabled'):
        lines.append(f"SMTP_HOST={email['smtp_host']}")
        lines.append(f"SMTP_PORT={email['smtp_port']}")
        lines.append(f"SMTP_USERNAME={email['smtp_username']}")
        lines.append(f"SMTP_PASSWORD={email['smtp_password']}")
        lines.append(f"SMTP_FROM_EMAIL={email['smtp_from_email']}")
        lines.append("SMTP_USE_TLS=true")
    lines.append("")

    # Redis
    lines.append("# Redis")
    lines.append(f"REDIS_ENABLED={'true' if redis.get('redis_enabled') else 'false'}")
    if redis.get('redis_enabled'):
        lines.append(f"REDIS_HOST={redis['redis_host']}")
        lines.append(f"REDIS_PORT={redis['redis_port']}")
        if redis.get('redis_password'):
            lines.append(f"REDIS_PASSWORD={redis['redis_password']}")
    lines.append("")

    # Safety (always on)
    lines.append("# Safety (always enabled — cannot be disabled in production)")
    lines.append("ENABLE_SAFETY_MONITORING=true")
    lines.append("SAFETY_PIPELINE_ENABLED=true")
    lines.append("")

    # Admin bootstrap
    lines.append("# First Admin Account")
    lines.append(f"# Email: {basics['admin_email']}")
    lines.append(f"# Password: {admin_password}")
    lines.append("# >>> Change this password after your first login! <<<")
    lines.append(f"ADMIN_BOOTSTRAP_EMAIL={basics['admin_email']}")
    lines.append(f"ADMIN_BOOTSTRAP_PASSWORD={admin_password}")
    lines.append("")

    # Server
    lines.append("# Server")
    lines.append("API_HOST=0.0.0.0")
    lines.append("API_PORT=39150")
    lines.append("API_WORKERS=4")
    lines.append("API_RELOAD=false")
    lines.append("LOG_LEVEL=INFO")
    lines.append("")

    env_content = "\n".join(lines) + "\n"

    # Show summary
    print(_bold("  Here's what will be set up:\n"))
    print(f"    Domain:           {basics['domain']}")
    print(f"    Admin email:      {basics['admin_email']}")
    print(f"    Database:         {database['db_type']}")
    print(f"    Email alerts:     {'Enabled' if email.get('smtp_enabled') else 'Disabled'}")
    print(f"    Redis:            {'Enabled' if redis.get('redis_enabled') else 'Disabled'}")
    print(f"    Encryption:       Enabled (auto-generated keys)")
    print(f"    Safety pipeline:  Always on")
    print()

    # Write file
    env_path = Path(__file__).parent.parent / '.env.production'

    if env_path.exists():
        overwrite = ask_yes_no(
            f"\n  {_yellow('.env.production already exists.')} Overwrite it?",
            default=False
        )
        if not overwrite:
            # Write to alternate path
            env_path = Path(__file__).parent.parent / '.env.production.new'
            print(f"  Writing to {env_path.name} instead.")

    fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(env_content)

    return env_path, admin_password, db_encryption_key, pii_encryption_key


def print_next_steps(env_path, admin_password, db_encryption_key, pii_encryption_key, is_local=False):
    """Print what to do next."""
    print(_bold(_green("\n" + "=" * 64)))
    print(_bold(_green("  Setup complete!")))
    print(_bold(_green("=" * 64)))

    if is_local:
        print(f"""
  Your configuration has been saved to: {_bold(str(env_path))}

  {_bold("Your account:")}
    Password: (saved in {env_path})
    {_yellow("Change this password after your first login!")}

  {_bold("What to do next:")}

    1. Start snflwr.ai:
       {_blue(f"export $(cat {env_path} | grep -v '^#' | xargs) && python -m api.server")}

    2. Open your browser to:
       {_blue("http://localhost:39150")}

    3. Log in with the email you entered and the password from {env_path}

    4. The setup wizard will walk you through adding your child's profile

  That's it! Your child's data stays on this computer.
""")
    else:
        print(f"""
  Your configuration has been saved to: {_bold(str(env_path))}
  File permissions set to owner-only (600).

  {_bold(_red("IMPORTANT — Save these keys somewhere safe (like a password manager):"))}
  {_bold(_red("If you lose them, encrypted data cannot be recovered."))}

    DB Encryption Key:  (saved in {env_path})
    PII Encryption Key: (saved in {env_path})

  {_bold("Your first admin account:")}
    Email:    (the admin email you entered above)
    Password: (saved in {env_path})
    {_yellow("Change this password after your first login!")}

  {_bold("What to do next:")}

    1. Save the encryption keys from {env_path} to a password manager (1Password, Bitwarden, etc.)

    2. Start the application:
       {_blue("docker compose up -d")}
       or
       {_blue(f"export $(cat {env_path} | grep -v '^#' | xargs) && python -m api.server")}

    3. Open your browser and go to your domain

    4. Log in with the admin credentials above and change your password

    5. Start creating student profiles!

  {_bold("Need help?")}
    Run the configuration checker anytime:
      {_blue("python scripts/validate_env.py")}
""")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def setup_local():
    """
    Fast-path setup for running on a home computer.

    Skips domain, email, and Redis — auto-configures everything for localhost
    with SQLite. The parent only needs to provide an email and password for
    their admin account.
    """
    print(_bold(_blue("\n--- Home Setup ---\n")))
    print("  Great! We'll keep things simple.\n")
    print("  snflwr.ai will run on this computer at http://localhost:39150")
    print("  Your child's data stays on this machine — nothing is sent to the cloud.\n")

    admin_email = ask(
        "What email address do you want for your account?\n"
        "  (This is just for logging in — we won't send anything to it)\n"
        "  Email",
        validator=validate_email
    )

    basics = {
        'domain': 'localhost',
        'admin_email': admin_email,
        'base_url': 'http://localhost:39150',
    }
    database = {'db_type': 'sqlite'}
    email = {'smtp_enabled': False}
    redis = {'redis_enabled': False}

    return basics, database, email, redis


def main():
    banner()

    # Check for cryptography package
    try:
        import cryptography
    except ImportError:
        print(_red("  The 'cryptography' package is not installed."))
        print(_red("  It's required for encrypting student data.\n"))
        print(f"  Install it with: {_blue('pip install cryptography')}\n")
        sys.exit(1)

    print("  This will walk you through setting up snflwr.ai.")
    print("  All security keys are generated automatically — no technical knowledge needed.\n")

    mode = ask_choice(
        "How will you be using snflwr.ai?",
        [
            ("Home use — just me and my family on this computer (fastest)", "local"),
            ("School or organization — deploying to a server for multiple users", "production"),
        ]
    )

    if mode == 'local':
        basics, database, email, redis = setup_local()
    else:
        print()
        ready = ask_yes_no("Ready to begin? (5 short steps, ~2 minutes)", default=True)
        if not ready:
            print("\n  No problem! Run this script again when you're ready.")
            sys.exit(0)

        basics = step_basics()
        database = step_database()
        email = step_email()
        redis = step_redis()

    env_path, admin_password, db_key, pii_key = step_review_and_write(
        basics, database, email, redis
    )

    is_local = basics['domain'] in ('localhost', '127.0.0.1')
    print_next_steps(env_path, admin_password, db_key, pii_key, is_local=is_local)


if __name__ == '__main__':
    main()
