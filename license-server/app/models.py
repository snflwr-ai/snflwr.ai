from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Account(Base):
    __tablename__ = "accounts"
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[int] = mapped_column(Integer)


class Subscription(Base):
    __tablename__ = "subscriptions"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), primary_key=True)
    ls_subscription_id: Mapped[str] = mapped_column(String, index=True)
    plan: Mapped[str] = mapped_column(String, default="family")
    status: Mapped[str] = mapped_column(String)
    current_period_end: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[int] = mapped_column(Integer)
    # Monotonic per-subscription version from the provider event (Lemon Squeezy
    # attributes.updated_at, epoch). Rejects stale/out-of-order webhook
    # deliveries and makes re-deliveries idempotent. 0 = no event applied yet.
    last_event_ts: Mapped[int] = mapped_column(Integer, default=0)


class AuthCode(Base):
    __tablename__ = "auth_codes"
    email: Mapped[str] = mapped_column(String, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String)
    expires_at: Mapped[int] = mapped_column(Integer)


class Activation(Base):
    __tablename__ = "activations"
    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    first_seen: Mapped[int] = mapped_column(Integer)
    last_seen: Mapped[int] = mapped_column(Integer)
