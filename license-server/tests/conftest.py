import asyncio
import os
import sys

# Make `app` importable when running pytest from license-server/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_key_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from app.config import settings as _settings  # noqa: E402

# Use a dedicated, fresh test database file. Set BEFORE any app module builds
# its engine (router modules read settings.DATABASE_URL at import time).
_test_db = os.path.join(_key_dir, "test_license.db")
if os.path.exists(_test_db):
    os.remove(_test_db)
_settings.DATABASE_URL = f"sqlite+aiosqlite:///{_test_db}"

from app.keygen import main as keygen_main  # noqa: E402

# Ensure a signing key exists for tests that sign/verify sessions + tokens.
if not os.path.exists(_settings.SIGNING_KEY_PATH):
    keygen_main(_key_dir)
    _settings.SIGNING_KEY_PATH = os.path.join(_key_dir, "signing_key.pem")

# Create the schema on the test database up front.
from app import db  # noqa: E402


async def _init():
    engine = db.make_engine(_settings.DATABASE_URL)
    await db.init_models(engine)
    await engine.dispose()


asyncio.run(_init())
