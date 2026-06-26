import hashlib

from sqlalchemy import delete, select

from app import models


def account_id_for_email(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return "acct_" + digest[:24]


async def get_account_by_email(s, email: str):
    return (await s.execute(
        select(models.Account).where(models.Account.email == email.strip().lower())
    )).scalar_one_or_none()


async def get_subscription(s, account_id: str):
    return (await s.execute(
        select(models.Subscription).where(models.Subscription.account_id == account_id)
    )).scalar_one_or_none()


async def upsert_subscription(s, *, email, ls_subscription_id, plan, status,
                              current_period_end, now, event_ts=None):
    """Apply a subscription state change.

    event_ts (the provider event's own monotonic timestamp) guards against
    stale/out-of-order webhook deliveries: if it is OLDER than the last event
    already applied to this subscription, the event is ignored (the account row
    is still ensured to exist). A re-delivered event with the same ts re-applies
    the same state (idempotent). event_ts=None disables the guard (always apply)
    for non-webhook callers.
    """
    email = email.strip().lower()
    account = await get_account_by_email(s, email)
    if account is None:
        account = models.Account(
            account_id=account_id_for_email(email), email=email, created_at=now)
        s.add(account)
    sub = await get_subscription(s, account.account_id)
    if (
        sub is not None
        and event_ts is not None
        and sub.last_event_ts
        and event_ts < sub.last_event_ts
    ):
        # Stale / out-of-order delivery — a newer event already won. Ignore.
        return account
    if sub is None:
        sub = models.Subscription(account_id=account.account_id)
        s.add(sub)
    sub.ls_subscription_id = ls_subscription_id
    sub.plan = plan
    sub.status = status
    sub.current_period_end = current_period_end
    sub.updated_at = now
    if event_ts is not None:
        sub.last_event_ts = event_ts
    return account


async def set_auth_code(s, email: str, code_hash: str, expires_at: int) -> None:
    email = email.strip().lower()
    await s.execute(delete(models.AuthCode).where(models.AuthCode.email == email))
    s.add(models.AuthCode(email=email, code_hash=code_hash, expires_at=expires_at))


async def pop_auth_code(s, email: str):
    email = email.strip().lower()
    row = (await s.execute(
        select(models.AuthCode).where(models.AuthCode.email == email)
    )).scalar_one_or_none()
    if row is not None:
        await s.execute(delete(models.AuthCode).where(models.AuthCode.email == email))
    return row
