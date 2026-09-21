"""
Perawatan database (SQLite): backup harian aman, retensi data lama, cek integritas.

Kenapa perlu: data monitoring adalah data historis yang tidak bisa dibuat
ulang, dan tabel check_results tumbuh terus (1 target @60 detik = ~525 ribu
baris/tahun). Tanpa backup satu file rusak/terhapus = semua riwayat hilang;
tanpa retensi DB membengkak dan semua query melambat.

- Backup memakai API online-backup sqlite3: konsisten walau DB sedang dipakai
  web/bot, ditulis ke file sementara lalu di-rename (tidak ada backup setengah jadi).
- Retensi menghapus bertahap (batch) agar tidak mengunci DB lama-lama.
- Semua fungsi TIDAK melempar ke scheduler: kegagalan dicatat ke log saja.

CLI:  python -m app.db_maintenance backup | prune | check
"""
import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.database import AsyncSessionLocal, DATABASE_URL, PROJECT_ROOT
from app.models import CheckResult

logger = logging.getLogger("netmonitor.db_maintenance")

BACKUP_DIR = os.environ.get("BACKUP_DIR") or str(PROJECT_ROOT / "backups")
BACKUP_PREFIX = "netmonitor-"
DEFAULT_KEEP = int(os.environ.get("BACKUP_KEEP", "14"))
DEFAULT_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "400"))
BACKUP_OVERDUE_HOURS = 26
_SQLITE_PREFIX = "sqlite+aiosqlite:///"


def sqlite_path_from_url(url: str) -> str | None:
    return url[len(_SQLITE_PREFIX):] if url.startswith(_SQLITE_PREFIX) else None


def _rotate(backup_dir: str, keep: int) -> None:
    files = sorted(f for f in os.listdir(backup_dir) if f.startswith(BACKUP_PREFIX) and f.endswith(".db"))
    for old in files[:-keep] if keep > 0 else []:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            logger.warning(f"Gagal menghapus backup lama {old}")


def backup_database(src_path: str, backup_dir: str, keep: int = DEFAULT_KEEP, now: datetime | None = None) -> str | None:
    """Kembalikan path backup, atau None kalau gagal (tidak pernah melempar)."""
    if not os.path.exists(src_path):
        logger.warning(f"Backup dilewati: file database tidak ditemukan ({src_path})")
        return None

    os.makedirs(backup_dir, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    final_path = os.path.join(backup_dir, f"{BACKUP_PREFIX}{stamp}.db")
    fd, tmp_path = tempfile.mkstemp(dir=backup_dir, prefix=".partial-", suffix=".tmp")
    os.close(fd)
    try:
        src = sqlite3.connect(src_path, timeout=30)
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        os.replace(tmp_path, final_path)
    except Exception:
        logger.exception("Backup database gagal")
        return None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    _rotate(backup_dir, keep)
    logger.info(f"Backup database dibuat: {final_path}")
    return final_path


def latest_backup_age_hours(backup_dir: str) -> float | None:
    if not os.path.isdir(backup_dir):
        return None
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
             if f.startswith(BACKUP_PREFIX) and f.endswith(".db")]
    if not files:
        return None
    return (time.time() - max(os.path.getmtime(f) for f in files)) / 3600


def integrity_check(db_path: str) -> str:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def run_backup() -> str | None:
    path = sqlite_path_from_url(DATABASE_URL)
    if path is None:
        logger.info("Database bukan SQLite: backup memakai pg_dump (lihat README bagian Database).")
        return None
    return backup_database(path, BACKUP_DIR, DEFAULT_KEEP)


async def prune_old_checks(db, retention_days: int = DEFAULT_RETENTION_DAYS, batch_size: int = 5000) -> int:
    """Hapus check_results lebih tua dari retention_days, bertahap. Return jumlah terhapus."""
    if retention_days is None or retention_days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    total = 0
    while True:
        ids = (await db.execute(
            select(CheckResult.id).where(CheckResult.checked_at < cutoff).limit(batch_size)
        )).scalars().all()
        if not ids:
            break
        await db.execute(delete(CheckResult).where(CheckResult.id.in_(ids)))
        await db.commit()
        total += len(ids)
    if total:
        logger.info(f"Retensi: {total} baris check_results lebih tua dari {retention_days} hari dihapus")
    return total


async def run_daily_maintenance() -> None:
    """Dipanggil scheduler tiap hari; setiap langkah terisolasi dari kegagalan langkah lain."""
    try:
        await asyncio.to_thread(run_backup)
    except Exception:
        logger.exception("Job backup gagal")

    try:
        async with AsyncSessionLocal() as db:
            await prune_old_checks(db)
    except Exception:
        logger.exception("Job retensi gagal")

    if datetime.now().weekday() == 6:  # Minggu: cek integritas mingguan
        try:
            path = sqlite_path_from_url(DATABASE_URL)
            if path:
                result = await asyncio.to_thread(integrity_check, path)
                (logger.info if result == "ok" else logger.error)(f"integrity_check: {result}")
        except Exception:
            logger.exception("Job integrity_check gagal")


def schedule_maintenance_jobs(scheduler) -> None:
    """Daftarkan job harian 03:00 + backup susulan bila backup terakhir sudah basi (server sempat mati)."""
    scheduler.add_job(run_daily_maintenance, "cron", hour=3, minute=0, id="daily_maintenance",
                      replace_existing=True, misfire_grace_time=6 * 3600, coalesce=True)
    age = latest_backup_age_hours(BACKUP_DIR)
    if age is None or age > BACKUP_OVERDUE_HOURS:
        scheduler.add_job(run_daily_maintenance, "date", run_date=datetime.now() + timedelta(seconds=90),
                          id="maintenance_catchup", replace_existing=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Perawatan database Netmonitor")
    parser.add_argument("action", choices=["backup", "prune", "check"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.action == "backup":
        path = run_backup()
        print(f"Backup: {path}" if path else "Backup gagal/dilewati (lihat pesan di atas).")
        sys.exit(0 if path else 1)
    if args.action == "prune":
        async def _prune():
            async with AsyncSessionLocal() as db:
                return await prune_old_checks(db)
        print(f"Baris terhapus: {asyncio.run(_prune())}")
    if args.action == "check":
        path = sqlite_path_from_url(DATABASE_URL)
        result = integrity_check(path) if path else "bukan SQLite"
        print(f"integrity_check: {result}")
        sys.exit(0 if result == "ok" else 1)


if __name__ == "__main__":
    main()
