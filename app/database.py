"""
Konfigurasi koneksi database.
Default: SQLite + SQLAlchemy async engine untuk development lokal.
Production (Heroku/dst): di-override lewat environment variable
DATABASE_URL, yang otomatis di-set oleh addon PostgreSQL platform-nya.
"""
import os
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SQLITE_PREFIX = "sqlite+aiosqlite:///"


def resolve_database_url(raw_url: str | None) -> str:
    """
    Menentukan URL database final.

    SQLite default/relatif SENGAJA di-resolve ke path absolut berdasarkan
    lokasi project (bukan folder tempat proses dijalankan). Web server dan
    bot Telegram adalah 2 proses terpisah -- kalau path relatif ke cwd,
    menjalankan bot dari folder lain diam-diam memakai file DB berbeda
    (kosong), dan /link jadi selalu "token tidak valid".
    """
    url = raw_url or f"{_SQLITE_PREFIX}netmonitor.db"

    # Heroku (dan beberapa provider lain) memberi DATABASE_URL dengan skema
    # "postgres://" atau "postgresql://" (skema sync), tapi kita pakai driver
    # async (asyncpg) -- perlu diubah ke "postgresql+asyncpg://" supaya
    # SQLAlchemy async engine bisa memakainya.
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)

    if url.startswith(_SQLITE_PREFIX):
        db_path = url[len(_SQLITE_PREFIX):]
        is_memory = db_path in ("", ":memory:")
        # "////abs" (4 slash) = absolut POSIX; "C:/..." = absolut Windows.
        is_absolute = db_path.startswith("/") or os.path.isabs(db_path)
        if not is_memory and not is_absolute:
            absolute = (PROJECT_ROOT / db_path).resolve()
            return f"{_SQLITE_PREFIX}{absolute.as_posix()}"

    return url


DATABASE_URL = resolve_database_url(os.environ.get("DATABASE_URL"))

# Web server dan bot menulis ke file SQLite yang sama dari 2 proses; timeout
# lebih panjang dari default 5 detik supaya tabrakan tulis singkat menunggu
# (bukan langsung error "database is locked").
_connect_args = {"timeout": 30} if DATABASE_URL.startswith("sqlite") else {}

engine = create_async_engine(DATABASE_URL, echo=False, connect_args=_connect_args)

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
