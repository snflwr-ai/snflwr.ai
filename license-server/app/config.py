import os
from dataclasses import dataclass


@dataclass
class _Settings:
    DATABASE_URL: str = os.getenv("LS_DATABASE_URL", "sqlite+aiosqlite:///./license.db")
    SIGNING_KEY_PATH: str = os.getenv("LS_SIGNING_KEY_PATH", "./signing_key.pem")
    LS_WEBHOOK_SECRET: str = os.getenv("LS_WEBHOOK_SECRET", "")
    SESSION_TTL_SECONDS: int = int(os.getenv("LS_SESSION_TTL_SECONDS", "3600"))
    CODE_TTL_SECONDS: int = int(os.getenv("LS_CODE_TTL_SECONDS", "600"))


settings = _Settings()
