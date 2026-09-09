"""
Konfigurasi koneksi database.
Menggunakan SQLite + SQLAlchemy async engine.
Nanti kalau mau upgrade ke PostgreSQL, cukup ganti DATABASE_URL.
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "sqlite+aiosqlite:///./netmonitor.db"

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
