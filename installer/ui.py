"""Console UI helpers: styled output and interactive prompts."""

import secrets


def _mask_secret(value: str, visible: int = 4) -> str:
    """Show only last N chars of a secret for verification."""
    s = str(value)
    if len(s) <= visible:
        return "***"
    return f"***{s[-visible:]}"


def print_header(text):
    """Print styled header"""
    print(f"\n{'='*70}")
    print(text.center(70))
    print(f"{'='*70}\n")


def print_success(text):
    print(f"  [OK] {text}")


def print_error(text):
    print(f"  [ERROR] {text}")


def print_warning(text):
    print(f"  [WARN] {text}")


def print_info(text):
    print(f"  [INFO] {text}")


def ask_question(question, default=None):
    """Ask user a question"""
    if default:
        prompt = f"? {question} [{default}]: "
    else:
        prompt = f"? {question}: "

    while True:
        answer = input(prompt).strip()
        if answer:
            return answer
        if default is not None:
            return default
        print_warning("A value is required. Please try again.")


def ask_yes_no(question, default=True):
    """Ask yes/no question"""
    default_text = "Y/n" if default else "y/N"
    prompt = f"? {question} [{default_text}]: "

    answer = input(prompt).strip().lower()
    if not answer:
        return default
    return answer in ["y", "yes"]


def generate_secure_token():
    """Generate secure random token"""
    return secrets.token_hex(32)
