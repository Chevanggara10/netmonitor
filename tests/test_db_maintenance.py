import os
import sqlite3
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app import db_maintenance
from app.db_maintenance import (
    backup_database, integrity_check, latest_backup_age_hours, prune_old_checks, sqlite_path_from_url,
)
from app.models import CheckResult


def _make_db(path, rows=5):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


def test_backup_creates_consistent_copy_and_keeps_only_newest(tmp_path):
    src = str(tmp_path / "live.db")
    _make_db(src, rows=7)
    backups = str(tmp_path / "backups")

    for i in range(4):
        backup_database(src, backups, keep=2, now=datetime(2026, 9, 1 + i, 3, 0, 0))

    kept = sorted(os.listdir(backups))
    assert kept == ["netmonitor-20260903-030000.db", "netmonitor-20260904-030000.db"]
    copy = sqlite3.connect(os.path.join(backups, kept[-1]))
    assert copy.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 7
    copy.close()
    assert integrity_check(os.path.join(backups, kept[-1])) == "ok"


def test_backup_of_missing_database_returns_none_and_does_not_raise(tmp_path):
    assert backup_database(str(tmp_path / "nope.db"), str(tmp_path / "b"), keep=3) is None


def test_backup_never_leaves_partial_files(tmp_path):
    src = str(tmp_path / "live.db")
    _make_db(src)
    backup_database(src, str(tmp_path / "b"), keep=3)
    assert all(name.endswith(".db") for name in os.listdir(tmp_path / "b"))


def test_latest_backup_age_hours(tmp_path):
    assert latest_backup_age_hours(str(tmp_path / "none")) is None
    src = str(tmp_path / "live.db")
    _make_db(src)
    path = backup_database(src, str(tmp_path / "b"), keep=3)
    old = time.time() - 5 * 3600
    os.utime(path, (old, old))
    age = latest_backup_age_hours(str(tmp_path / "b"))
    assert 4.9 < age < 5.1


def test_sqlite_path_from_url_only_for_sqlite():
    assert sqlite_path_from_url("sqlite+aiosqlite:///L:/x/y.db") == "L:/x/y.db"
    assert sqlite_path_from_url("postgresql+asyncpg://u:p@h/db") is None


async def test_prune_deletes_only_old_rows_in_batches(db_session, sample_target):
    now = datetime.utcnow()
    for days_old in (500, 450, 401, 399, 10, 0):
        db_session.add(CheckResult(target_id=sample_target.id, checked_at=now - timedelta(days=days_old), is_up=True))
    await db_session.commit()

    deleted = await prune_old_checks(db_session, retention_days=400, batch_size=2)

    assert deleted == 3
    remaining = (await db_session.execute(select(func.count(CheckResult.id)))).scalar_one()
    assert remaining == 3


async def test_prune_with_invalid_retention_is_a_noop(db_session, sample_target):
    db_session.add(CheckResult(target_id=sample_target.id, checked_at=datetime.utcnow() - timedelta(days=9999), is_up=True))
    await db_session.commit()

    assert await prune_old_checks(db_session, retention_days=0) == 0
    assert await prune_old_checks(db_session, retention_days=-5) == 0


def test_schedule_registers_daily_job_and_catchup_when_backup_is_missing(monkeypatch, tmp_path):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    monkeypatch.setattr(db_maintenance, "BACKUP_DIR", str(tmp_path / "empty"))
    scheduler = AsyncIOScheduler()

    db_maintenance.schedule_maintenance_jobs(scheduler)

    ids = {job.id for job in scheduler.get_jobs()}
    assert ids == {"daily_maintenance", "maintenance_catchup"}


def test_schedule_skips_catchup_when_recent_backup_exists(monkeypatch, tmp_path):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    src = str(tmp_path / "live.db")
    _make_db(src)
    backup_database(src, str(tmp_path / "b"), keep=3)
    monkeypatch.setattr(db_maintenance, "BACKUP_DIR", str(tmp_path / "b"))
    scheduler = AsyncIOScheduler()

    db_maintenance.schedule_maintenance_jobs(scheduler)

    assert {job.id for job in scheduler.get_jobs()} == {"daily_maintenance"}


def test_backup_skips_non_sqlite_database(monkeypatch):
    monkeypatch.setattr(db_maintenance, "DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    assert db_maintenance.run_backup() is None
