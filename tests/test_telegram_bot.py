from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.models import TelegramLinkToken


class _NonClosingSessionWrapper:
    """
    Bungkus db_session fixture supaya `async with _get_session() as db`
    di handler tidak menutup session test sungguhan saat keluar block --
    fixture db_session dipakai lagi setelahnya untuk assert (mis. refresh).
    """
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


def _make_update_and_context(text: str, chat_id: str = "12345"):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.text = text
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.args = text.split()[1:] if " " in text else []
    return update, context


async def test_handle_link_with_valid_token_sets_chat_id(db_session, sample_user, monkeypatch):
    from app.telegram_bot import handle_link

    token = TelegramLinkToken(
        token="valid-token-123",
        user_id=sample_user.id,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db_session.add(token)
    await db_session.commit()

    monkeypatch.setattr("app.telegram_bot._get_session", lambda: _NonClosingSessionWrapper(db_session))

    update, context = _make_update_and_context("/link valid-token-123", chat_id="chat-abc")
    context.args = ["valid-token-123"]

    await handle_link(update, context)

    await db_session.refresh(sample_user)
    assert sample_user.telegram_chat_id == "chat-abc"
    update.message.reply_text.assert_called_once()
    assert "berhasil" in update.message.reply_text.call_args[0][0].lower()


async def test_handle_link_with_expired_token_rejects(db_session, sample_user, monkeypatch):
    from app.telegram_bot import handle_link

    token = TelegramLinkToken(
        token="expired-token",
        user_id=sample_user.id,
        expires_at=datetime.utcnow() - timedelta(minutes=1),
    )
    db_session.add(token)
    await db_session.commit()

    monkeypatch.setattr("app.telegram_bot._get_session", lambda: _NonClosingSessionWrapper(db_session))

    update, context = _make_update_and_context("/link expired-token", chat_id="chat-abc")
    context.args = ["expired-token"]

    await handle_link(update, context)

    await db_session.refresh(sample_user)
    assert sample_user.telegram_chat_id is None
    reply = update.message.reply_text.call_args[0][0].lower()
    assert "kedaluwarsa" in reply or "tidak valid" in reply


async def test_handle_status_reports_target_summary(db_session, sample_user, sample_target, monkeypatch):
    from app.telegram_bot import handle_status

    sample_user.telegram_chat_id = "chat-abc"
    await db_session.commit()

    monkeypatch.setattr("app.telegram_bot._get_session", lambda: _NonClosingSessionWrapper(db_session))

    update, context = _make_update_and_context("/status", chat_id="chat-abc")

    await handle_status(update, context)

    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert sample_target.name in reply


async def test_handle_status_unlinked_chat_asks_to_link(db_session, monkeypatch):
    from app.telegram_bot import handle_status

    monkeypatch.setattr("app.telegram_bot._get_session", lambda: _NonClosingSessionWrapper(db_session))

    update, context = _make_update_and_context("/status", chat_id="unknown-chat")

    await handle_status(update, context)

    reply = update.message.reply_text.call_args[0][0]
    assert "/link" in reply


async def test_handle_forecast_unknown_target_name_reports_not_found(db_session, sample_user, monkeypatch):
    from app.telegram_bot import handle_forecast

    sample_user.telegram_chat_id = "chat-abc"
    await db_session.commit()

    monkeypatch.setattr("app.telegram_bot._get_session", lambda: _NonClosingSessionWrapper(db_session))

    update, context = _make_update_and_context("/forecast NamaTidakAda", chat_id="chat-abc")
    context.args = ["NamaTidakAda"]

    await handle_forecast(update, context)

    reply = update.message.reply_text.call_args[0][0]
    assert "tidak ditemukan" in reply.lower()
