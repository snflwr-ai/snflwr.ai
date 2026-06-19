import pytest
from sqlalchemy import select
from app import db, models


@pytest.mark.asyncio
async def test_create_and_read_account():
    engine = db.make_engine("sqlite+aiosqlite:///:memory:")
    await db.init_models(engine)
    Session = db.make_session_factory(engine)
    async with Session() as s:
        s.add(models.Account(account_id="acct_1", email="a@b.com", created_at=1))
        await s.commit()
    async with Session() as s:
        row = (await s.execute(select(models.Account))).scalar_one()
        assert row.email == "a@b.com"
