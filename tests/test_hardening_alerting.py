import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import alerting
from app.alerting import process_alert
from app.models import AlertRule, CheckResult


@pytest.fixture(autouse=True)
def _no_real_email(monkeypatch):
    # .env asli (RESEND_API_KEY) bisa ikut termuat lewat app.main -> jangan pernah kirim email sungguhan di test.
    ok = SimpleNamespace(sent=False, dry_run=True, detail="dry-run")
    monkeypatch.setattr(alerting, "send_email", AsyncMock(return_value=ok))


async def _rule(db, target, email=None, threshold=1):
    rule = AlertRule(target_id=target.id, is_enabled=True, failure_threshold=threshold,
                     cooldown_minutes=30, notify_email=email)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def _result(db, target, up=False, error="boom"):
    result = CheckResult(target_id=target.id, is_up=up, error_message=None if up else error)
    db.add(result)
    await db.commit()
    await db.refresh(result)
    return result


async def _link(db, user, chat="chat-1"):
    user.telegram_chat_id = chat
    await db.commit()


async def test_telegram_exception_never_breaks_pipeline_and_email_still_counts(db_session, sample_user, sample_target, monkeypatch):
    await _link(db_session, sample_user)
    rule = await _rule(db_session, sample_target, email="ops@example.com")
    result = await _result(db_session, sample_target)
    monkeypatch.setattr(alerting, "send_alert", AsyncMock(side_effect=RuntimeError("telegram down")))

    await process_alert(sample_target, result, rule, db_session)  # tidak boleh melempar

    assert rule.last_alert_sent_at is not None  # email (dry-run) tetap tercatat terkirim


async def test_email_exception_does_not_block_telegram(db_session, sample_user, sample_target, monkeypatch):
    await _link(db_session, sample_user)
    rule = await _rule(db_session, sample_target, email="ops@example.com")
    result = await _result(db_session, sample_target)
    monkeypatch.setattr(alerting, "send_email", AsyncMock(side_effect=RuntimeError("smtp down")))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(alerting, "send_alert", send)

    await process_alert(sample_target, result, rule, db_session)

    send.assert_called_once()
    assert rule.last_alert_sent_at is not None


async def test_failed_delivery_does_not_burn_cooldown(db_session, sample_user, sample_target, monkeypatch):
    await _link(db_session, sample_user)
    rule = await _rule(db_session, sample_target)  # tanpa email
    send = AsyncMock(return_value=False)
    monkeypatch.setattr(alerting, "send_alert", send)

    await process_alert(sample_target, await _result(db_session, sample_target), rule, db_session)
    assert rule.last_alert_sent_at is None

    await process_alert(sample_target, await _result(db_session, sample_target), rule, db_session)
    assert send.call_count == 2  # dicoba lagi di check berikutnya


async def test_inactive_user_is_not_notified(db_session, sample_user, sample_target, monkeypatch):
    await _link(db_session, sample_user)
    sample_user.is_active = False
    await db_session.commit()
    rule = await _rule(db_session, sample_target)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(alerting, "send_alert", send)

    await process_alert(sample_target, await _result(db_session, sample_target), rule, db_session)

    send.assert_not_called()


async def test_slow_telegram_is_cut_off_by_timeout(db_session, sample_user, sample_target, monkeypatch):
    await _link(db_session, sample_user)
    rule = await _rule(db_session, sample_target)

    async def very_slow(chat_id, message):
        await asyncio.sleep(30)
        return True

    monkeypatch.setattr(alerting, "send_alert", very_slow)
    monkeypatch.setattr(alerting, "TELEGRAM_TIMEOUT_SECONDS", 0.2)

    loop = asyncio.get_running_loop()
    start = loop.time()
    await process_alert(sample_target, await _result(db_session, sample_target), rule, db_session)

    assert loop.time() - start < 2
    assert rule.last_alert_sent_at is None


async def test_recovery_message_sent_once_and_failure_is_contained(db_session, sample_user, sample_target, monkeypatch):
    await _link(db_session, sample_user)
    rule = await _rule(db_session, sample_target)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(alerting, "send_alert", send)

    await process_alert(sample_target, await _result(db_session, sample_target), rule, db_session)  # DOWN
    assert send.call_count == 1

    send.side_effect = RuntimeError("telegram down")
    await process_alert(sample_target, await _result(db_session, sample_target, up=True), rule, db_session)  # RECOVERY

    assert rule.consecutive_failures == 0 and rule.last_alert_sent_at is None  # state tetap di-reset
