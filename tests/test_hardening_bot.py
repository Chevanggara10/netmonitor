from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from telegram.error import Forbidden, TelegramError

from app import telegram_bot
from app.models import MonitorTarget, TelegramLinkToken, User


class _Session:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def use_session(monkeypatch, db_session):
    monkeypatch.setattr(telegram_bot, "_get_session", lambda: _Session(db_session))
    return db_session


def _update(chat_id="chat-1"):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.message.reply_text = AsyncMock()
    return update


def _ctx(*args):
    ctx = MagicMock()
    ctx.args = list(args)
    return ctx


def _reply(update):
    return update.message.reply_text.call_args[0][0]


async def _token(db, user_id, value="tok-1", minutes=5):
    db.add(TelegramLinkToken(token=value, user_id=user_id, expires_at=datetime.utcnow() + timedelta(minutes=minutes)))
    await db.commit()


async def test_link_accepts_token_pasted_with_angle_brackets_and_spaces(use_session, sample_user):
    await _token(use_session, sample_user.id, "abc-DEF_123")
    update = _update("chat-9")

    await telegram_bot.handle_link(update, _ctx("<abc-DEF_123>"))

    await use_session.refresh(sample_user)
    assert sample_user.telegram_chat_id == "chat-9"


async def test_link_with_token_of_deleted_user_is_handled(use_session, sample_user):
    await _token(use_session, 9999, "ghost")
    update = _update()

    await telegram_bot.handle_link(update, _ctx("ghost"))

    assert "tidak valid" in _reply(update).lower() or "tidak ditemukan" in _reply(update).lower()
    assert (await use_session.execute(select(TelegramLinkToken))).scalars().all() == []


async def test_relink_same_chat_to_other_user_moves_ownership(use_session, sample_user):
    other = User(email="b@example.com", hashed_password="x", telegram_chat_id="chat-1")
    use_session.add(other)
    await use_session.commit()
    await _token(use_session, sample_user.id, "move")

    await telegram_bot.handle_link(_update("chat-1"), _ctx("move"))

    await use_session.refresh(other)
    await use_session.refresh(sample_user)
    assert sample_user.telegram_chat_id == "chat-1"
    assert other.telegram_chat_id is None


async def test_duplicate_chat_ids_in_db_do_not_crash_status(use_session, sample_user, sample_target):
    sample_user.telegram_chat_id = "dup"
    use_session.add(User(email="dup@example.com", hashed_password="x", telegram_chat_id="dup"))
    await use_session.commit()
    update = _update("dup")

    await telegram_bot.handle_status(update, _ctx())

    assert _reply(update)


async def test_inactive_user_gets_no_data(use_session, sample_user, sample_target):
    sample_user.telegram_chat_id = "chat-1"
    sample_user.is_active = False
    await use_session.commit()
    update = _update("chat-1")

    await telegram_bot.handle_status(update, _ctx())

    assert sample_target.name not in _reply(update)
    assert "nonaktif" in _reply(update).lower()


async def test_duplicate_target_names_do_not_crash_forecast(use_session, sample_user, sample_target):
    sample_user.telegram_chat_id = "chat-1"
    use_session.add(MonitorTarget(user_id=sample_user.id, name=sample_target.name, url="https://b.example.com"))
    await use_session.commit()
    update = _update("chat-1")

    await telegram_bot.handle_forecast(update, _ctx(*sample_target.name.split()))

    assert "kesalahan" not in _reply(update).lower()


async def test_forecast_name_match_is_case_insensitive_and_trimmed(use_session, sample_user, sample_target):
    sample_user.telegram_chat_id = "chat-1"
    await use_session.commit()
    update = _update("chat-1")

    await telegram_bot.handle_forecast(update, _ctx("  test", "TARGET "))

    assert "tidak ditemukan" not in _reply(update).lower()


async def test_unknown_target_lists_available_names(use_session, sample_user, sample_target):
    sample_user.telegram_chat_id = "chat-1"
    await use_session.commit()
    update = _update("chat-1")

    await telegram_bot.handle_forecast(update, _ctx("Nope"))

    assert "tidak ditemukan" in _reply(update).lower()
    assert sample_target.name in _reply(update)


async def test_forecast_without_args_and_no_arg_link_show_usage(use_session, sample_user):
    sample_user.telegram_chat_id = "chat-1"
    await use_session.commit()
    a, b = _update("chat-1"), _update("chat-1")

    await telegram_bot.handle_forecast(a, _ctx())
    await telegram_bot.handle_link(b, _ctx())

    assert "/forecast" in _reply(a)
    assert "/link" in _reply(b)


async def test_unlink_clears_chat_and_is_safe_when_not_linked(use_session, sample_user):
    sample_user.telegram_chat_id = "chat-1"
    await use_session.commit()
    first, second = _update("chat-1"), _update("chat-1")

    await telegram_bot.handle_unlink(first, _ctx())
    await telegram_bot.handle_unlink(second, _ctx())

    await use_session.refresh(sample_user)
    assert sample_user.telegram_chat_id is None
    assert "diputus" in _reply(first).lower()
    assert "belum" in _reply(second).lower()


async def test_start_and_help_list_all_commands():
    for handler in (telegram_bot.handle_start, telegram_bot.handle_help):
        update = _update()
        await handler(update, _ctx())
        text = _reply(update)
        for cmd in ("/link", "/status", "/forecast", "/unlink", "/help"):
            assert cmd in text


async def test_handler_exception_gives_friendly_reply_instead_of_silence(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(telegram_bot, "_get_session", boom)
    update = _update()

    await telegram_bot.handle_status(update, _ctx())

    assert "kesalahan" in _reply(update).lower()


async def test_unknown_message_hints_help():
    update = _update()
    await telegram_bot.handle_unknown(update, _ctx())
    assert "/help" in _reply(update)


class _FakeBot:
    sent = []
    closed = 0
    error = None

    def __init__(self, token=None, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        _FakeBot.closed += 1
        return False

    async def send_message(self, chat_id, text, **kw):
        if _FakeBot.error:
            raise _FakeBot.error
        _FakeBot.sent.append((chat_id, text))


@pytest.fixture
def fake_bot(monkeypatch):
    _FakeBot.sent, _FakeBot.closed, _FakeBot.error = [], 0, None
    monkeypatch.setattr(telegram_bot, "Bot", _FakeBot)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1" * 9 + ":" + "A" * 30)
    return _FakeBot


async def test_send_alert_success_returns_true_and_closes_client(fake_bot):
    assert await telegram_bot.send_alert("c1", "halo") is True
    assert fake_bot.sent == [("c1", "halo")]
    assert fake_bot.closed == 1


async def test_send_alert_without_token_is_dry_run_false(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert await telegram_bot.send_alert("c1", "halo") is False


async def test_send_alert_swallows_telegram_errors(fake_bot):
    for error in (Forbidden("bot was blocked by the user"), TelegramError("boom"), OSError("network down")):
        fake_bot.error = error
        assert await telegram_bot.send_alert("c1", "x") is False


async def test_send_alert_truncates_overlong_messages(fake_bot):
    await telegram_bot.send_alert("c1", "x" * 10_000)
    assert len(fake_bot.sent[0][1]) <= 4096


async def test_send_alert_with_malformed_token_does_not_raise(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bukan-token")
    assert await telegram_bot.send_alert("c1", "x") is False


def test_build_application_registers_all_commands(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    app = telegram_bot.build_application()
    commands = {c for group in app.handlers.values() for h in group for c in getattr(h, "commands", [])}
    assert {"start", "help", "link", "unlink", "status", "forecast"} <= commands
    assert app.error_handlers
