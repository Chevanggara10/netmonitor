from unittest.mock import AsyncMock

from app.models import CheckResult, AlertRule
from app.alerting import process_alert


async def test_process_alert_sends_telegram_when_chat_id_set(db_session, sample_user, sample_target, monkeypatch):
    sample_user.telegram_chat_id = "chat-xyz"
    await db_session.commit()

    alert_rule = AlertRule(target_id=sample_target.id, is_enabled=True, failure_threshold=1, cooldown_minutes=30, notify_email=None)
    db_session.add(alert_rule)
    await db_session.commit()
    await db_session.refresh(alert_rule)

    result = CheckResult(target_id=sample_target.id, is_up=False, error_message="Connection refused")
    db_session.add(result)
    await db_session.commit()
    await db_session.refresh(result)

    mock_send_alert = AsyncMock()
    monkeypatch.setattr("app.alerting.send_alert", mock_send_alert)

    await process_alert(sample_target, result, alert_rule, db_session)

    mock_send_alert.assert_called_once()
    call_chat_id, call_message = mock_send_alert.call_args[0]
    assert call_chat_id == "chat-xyz"
    assert sample_target.name in call_message


async def test_process_alert_skips_telegram_when_no_chat_id(db_session, sample_target, monkeypatch):
    alert_rule = AlertRule(target_id=sample_target.id, is_enabled=True, failure_threshold=1, cooldown_minutes=30, notify_email=None)
    db_session.add(alert_rule)
    await db_session.commit()
    await db_session.refresh(alert_rule)

    result = CheckResult(target_id=sample_target.id, is_up=False, error_message="timeout")
    db_session.add(result)
    await db_session.commit()
    await db_session.refresh(result)

    mock_send_alert = AsyncMock()
    monkeypatch.setattr("app.alerting.send_alert", mock_send_alert)

    await process_alert(sample_target, result, alert_rule, db_session)

    mock_send_alert.assert_not_called()
