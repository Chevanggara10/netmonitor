"""
Konfigurasi koneksi database.
Default: SQLite + SQLAlchemy async engine untuk development lokal.
Production (Heroku/dst): di-override lewat environment variable
DATABASE_URL, yang otomatis di-set oleh addon PostgreSQL platform-nya.
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

_raw_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./netmonitor.db")

# Heroku (dan beberapa provider lain) memberi DATABASE_URL dengan skema
# "postgres://" atau "postgresql://" (skema sync), tapi kita pakai driver
# async (asyncpg) -- perlu diubah ke "postgresql+asyncpg://" supaya
# SQLAlchemy async engine bisa memakainya.
if _raw_url.startswith("postgres://"):
    _raw_url = _raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _raw_url.startswith("postgresql://"):
    _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

DATABASE_URL = _raw_url

engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Membuat semua tabel jika belum ada."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """Dependency untuk FastAPI: menyediakan session database per-request."""
    async with AsyncSessionLocal() as session:
        yield session
