"""
Fixture bersama semua test: DB in-memory (bukan netmonitor.db asli),
dan factory untuk bikin target + check result dummy dengan cepat.
"""
import pytest_asyncio
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.database import Base
from app.models import User, MonitorTarget, CheckResult


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def sample_user(db_session):
    user = User(email="test@example.com", hashed_password="hashed")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def sample_target(db_session, sample_user):
    target = MonitorTarget(
        user_id=sample_user.id,
        name="Test Target",
        url="https://example.com",
        interval_seconds=10,
    )
    db_session.add(target)
    await db_session.commit()
    await db_session.refresh(target)
    return target


async def add_check_results(db_session, target_id: int, count: int, base_time: datetime | None = None):
    """Helper: tambah `count` CheckResult berurutan (1 menit terpisah), UP semua."""
    base_time = base_time or datetime.utcnow() - timedelta(hours=count)
    for i in range(count):
        db_session.add(CheckResult(
            target_id=target_id,
            checked_at=base_time + timedelta(minutes=i),
            status_code=200,
            response_time_ms=100.0 + i,
            is_up=True,
        ))
    await db_session.commit()
