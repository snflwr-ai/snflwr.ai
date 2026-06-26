import pytest
from app import db, store, models
from sqlalchemy import select


@pytest.fixture
async def session():
    engine = db.make_engine("sqlite+aiosqlite:///:memory:")
    await db.init_models(engine)
    Session = db.make_session_factory(engine)
    async with Session() as s:
        yield s


@pytest.mark.asyncio
async def test_upsert_creates_then_updates(session):
    acct = await store.upsert_subscription(
        session, email="p@x.com", ls_subscription_id="sub_1", plan="family",
        status="active", current_period_end=100, now=1)
    await session.commit()
    assert acct.email == "p@x.com"
    # second upsert same email updates status
    await store.upsert_subscription(
        session, email="p@x.com", ls_subscription_id="sub_1", plan="family",
        status="canceled", current_period_end=200, now=2)
    await session.commit()
    sub = await store.get_subscription(session, acct.account_id)
    assert sub.status == "canceled"
    assert sub.current_period_end == 200
    # only one account row
    rows = (await session.execute(select(models.Account))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_webhook_ordering_and_idempotency(session):
    """event_ts guards against stale/out-of-order deliveries and re-deliveries."""
    acct = await store.upsert_subscription(
        session, email="p@x.com", ls_subscription_id="s1", plan="family",
        status="active", current_period_end=200, now=1, event_ts=100)
    await session.commit()

    # Stale/out-of-order event (older ts, e.g. a late-retried past_due) must NOT win.
    await store.upsert_subscription(
        session, email="p@x.com", ls_subscription_id="s1", plan="family",
        status="past_due", current_period_end=0, now=2, event_ts=50)
    await session.commit()
    sub = await store.get_subscription(session, acct.account_id)
    assert sub.status == "active"
    assert sub.current_period_end == 200

    # Re-delivery of the SAME event (same ts) is idempotent (same end state).
    await store.upsert_subscription(
        session, email="p@x.com", ls_subscription_id="s1", plan="family",
        status="active", current_period_end=200, now=3, event_ts=100)
    await session.commit()
    sub = await store.get_subscription(session, acct.account_id)
    assert sub.status == "active"

    # A genuinely newer event applies.
    await store.upsert_subscription(
        session, email="p@x.com", ls_subscription_id="s1", plan="family",
        status="canceled", current_period_end=300, now=4, event_ts=150)
    await session.commit()
    sub = await store.get_subscription(session, acct.account_id)
    assert sub.status == "canceled"
    assert sub.current_period_end == 300


@pytest.mark.asyncio
async def test_auth_code_is_one_time(session):
    await store.set_auth_code(session, "p@x.com", "hash123", 999)
    await session.commit()
    got = await store.pop_auth_code(session, "p@x.com")
    await session.commit()
    assert got.code_hash == "hash123"
    assert await store.pop_auth_code(session, "p@x.com") is None
