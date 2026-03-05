# Contributing to snflwr.ai

Thank you for your interest in contributing. snflwr.ai is a children's safety product, so contributions are held to a higher standard around security, privacy, and test coverage.

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

## Development Setup

### Prerequisites

- Python 3.11+
- Git

### Getting Started

```bash
git clone https://github.com/tmartin2113/snflwr-ai.git
cd snflwr-ai

python3.11 -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows

pip install -r requirements-dev.txt
cp .env.example .env
```

### Running the API Server

```bash
python -m api.server
# or: uvicorn api.server:app --reload
```

The API starts on `http://localhost:39150`.

### Running Tests

```bash
# Unit tests (no external services needed)
pytest tests/ -v -m "not integration"

# Integration tests (requires Redis + Ollama running)
pytest tests/ -v -m integration

# With coverage
pytest tests/ -v -m "not integration" --cov=api --cov=core --cov=safety --cov=storage
```

### Compile-Check a File

```bash
python3 -m py_compile path/to/file.py
```

## Code Style

### Formatting

We use **black** for formatting:

```bash
black api/ core/ safety/ storage/ utils/ tasks/
```

### Imports

Absolute imports only. No relative imports. Standard ordering:

```python
# 1. stdlib
import os
from pathlib import Path

# 2. third-party
from fastapi import APIRouter, HTTPException, Depends

# 3. local
from config import system_config
from utils.logger import get_logger

logger = get_logger(__name__)
```

### Logging

Always use the project logger:

```python
from utils.logger import get_logger
logger = get_logger(__name__)
```

Never use bare `import logging`. The project logger includes correlation IDs and structured output.

### Configuration

Use the existing singletons from `config.py`:

```python
from config import system_config, safety_config
```

Do not instantiate new config objects.

### Database Errors

Use the shared exception tuples:

```python
from storage.db_adapters import DB_ERRORS

try:
    db.execute_query(...)
except DB_ERRORS as e:
    logger.error(f"Database error: {e}")
```

## What to Contribute

Contributions are especially welcome in these areas:

- **Safety filter improvements** - Better detection, fewer false positives
- **Multi-language support** - Content filtering for non-English languages
- **Test coverage** - Especially for COPPA/FERPA compliance paths
- **Documentation** - Guides, examples, translations
- **Accessibility** - Making the UI work for all students

## Guidelines

### Safety-Critical Code

Files in `safety/` use broad `except Exception` blocks intentionally to **fail closed**. If you're modifying the safety pipeline, do not narrow exception handling without understanding the implications. A missed exception means unsafe content gets through to children.

### Test Requirements

- New features must include tests
- Bug fixes should include a regression test
- The coverage ratchet in `pytest.ini` (`--cov-fail-under`) must not decrease
- Tests that need optional packages (`psycopg2`, `Flask`, `email-validator`) should use `pytest.importorskip`

### Privacy

- Never log or store plaintext email addresses (use `encrypted_email` + `email_hash`)
- Never log student conversation content outside the encrypted audit trail
- Never add third-party analytics or tracking

### Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes with clear commit messages
3. Ensure all tests pass: `pytest tests/ -v -m "not integration"`
4. Run the formatter: `black api/ core/ safety/ storage/ utils/ tasks/`
5. Open a PR with a description of what changed and why

### Commit Messages

Use clear, descriptive commit messages. Prefix with the area of change:

```
safety: improve Unicode normalization for emoji bypass attempts
auth: fix session expiry check for timezone-aware datetimes
tests: add regression test for COPPA consent token expiry
```

## Reporting Security Issues

**Do not open a public issue for security vulnerabilities.** See [SECURITY.md](SECURITY.md) for responsible disclosure instructions.

## License

By contributing, you agree that your contributions will be licensed under the [AGPL-3.0 License](LICENSE).
