from sqlalchemy import delete, select

from app import checker
from app.models import CheckResult, MonitorTarget


async def _fake_attempt(target):
    return {"is_up": True, "status_code": 200, "response_time_ms": 123.0, "error_message": None, "response_size_bytes": 10}


async def test_check_result_is_saved_for_existing_target(db_session, sample_target, monkeypatch):
    monkeypatch.setattr(checker, "_dispatch_single_attempt", _fake_attempt)

    result = await checker.perform_check(sample_target, db_session)

    assert result is not None and result.id is not None
    assert (await db_session.execute(select(CheckResult))).scalars().all()


async def test_check_finishing_after_target_deletion_is_discarded_not_orphaned(db_session, sample_target, monkeypatch):
    async def slow_attempt_then_delete(target):
        # target dihapus user saat pengecekan masih berjalan
        await db_session.execute(delete(MonitorTarget).where(MonitorTarget.id == target.id))
        await db_session.commit()
        return await _fake_attempt(target)

    monkeypatch.setattr(checker, "_dispatch_single_attempt", slow_attempt_then_delete)

    result = await checker.perform_check(sample_target, db_session)

    assert result is None
    assert (await db_session.execute(select(CheckResult))).scalars().all() == []
