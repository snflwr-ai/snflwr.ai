from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    return create_async_engine(url, future=True)


def make_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_models(engine) -> None:
    import app.models  # noqa: F401 - ensure models register on Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
