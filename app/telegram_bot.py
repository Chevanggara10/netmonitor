"""
Bot Telegram: hubungkan akun web ke chat lewat /link, lalu /status dan
/forecast untuk query, plus send_alert() dipanggil dari alerting.py
untuk push notification.

Dijalankan sebagai proses TERPISAH dari web server (lihat
run_telegram_bot.py) -- polling loop Telegram punya siklus hidup
sendiri, tidak boleh menjatuhkan web dashboard kalau reconnect/crash.
"""
import logging
import os
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.database import AsyncSessionLocal
from app.models import User, MonitorTarget, CheckResult, TelegramLinkToken
from app.ml_forecast import get_forecast

logger = logging.getLogger("netmonitor.telegram_bot")


def _get_session() -> AsyncSession:
    """Terpisah jadi fungsi supaya gampang di-monkeypatch di test."""
    return AsyncSessionLocal()


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Pakai format: /link <token> (dapatkan token dari dashboard web)")
        return

    token_str = context.args[0]
    chat_id = str(update.effective_chat.id)

    async with _get_session() as db:
        result = await db.execute(select(TelegramLinkToken).where(TelegramLinkToken.token == token_str))
        link_token = result.scalar_one_or_none()

        if link_token is None:
            await update.message.reply_text("Token tidak valid.")
            return
        if link_token.expires_at < datetime.utcnow():
            await update.message.reply_text("Token sudah kedaluwarsa, generate token baru dari dashboard.")
            return

        user = await db.get(User, link_token.user_id)
        user.telegram_chat_id = chat_id
        await db.delete(link_token)
        await db.commit()

    await update.message.reply_text("Akun berhasil terhubung! Coba /status untuk lihat ringkasan target kamu.")


async def _find_user_by_chat(db: AsyncSession, chat_id: str) -> User | None:
    result = await db.execute(select(User).where(User.telegram_chat_id == chat_id))
    return result.scalar_one_or_none()


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)

    async with _get_session() as db:
        user = await _find_user_by_chat(db, chat_id)
        if user is None:
            await update.message.reply_text("Akun Telegram ini belum terhubung. Gunakan /link <token> dulu.")
            return

        result = await db.execute(select(MonitorTarget).where(MonitorTarget.user_id == user.id))
        targets = result.scalars().all()

        if not targets:
            await update.message.reply_text("Belum ada target monitoring.")
            return

        lines = []
        for target in targets:
            latest_result = await db.execute(
                select(CheckResult)
                .where(CheckResult.target_id == target.id)
                .order_by(CheckResult.checked_at.desc())
                .limit(1)
            )
            latest = latest_result.scalar_one_or_none()
            status = "ONLINE" if latest and latest.is_up else "OFFLINE" if latest else "PENDING"
            lines.append(f"- {target.name}: {status}")

        await update.message.reply_text("Ringkasan target:\n" + "\n".join(lines))


async def handle_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)

    if not context.args:
        await update.message.reply_text("Pakai format: /forecast <nama_target>")
        return
    target_name = " ".join(context.args)

    async with _get_session() as db:
        user = await _find_user_by_chat(db, chat_id)
        if user is None:
            await update.message.reply_text("Akun Telegram ini belum terhubung. Gunakan /link <token> dulu.")
            return

        result = await db.execute(
            select(MonitorTarget).where(MonitorTarget.user_id == user.id, MonitorTarget.name == target_name)
        )
        target = result.scalar_one_or_none()
        if target is None:
            await update.message.reply_text(f"Target '{target_name}' tidak ditemukan.")
            return

        forecast = await get_forecast(target.id, db, horizon_hours=24)

    if forecast["source"] == "trained_model":
        preview = ", ".join(f"{v}ms" for v in forecast["forecast_ms"][:6])
        await update.message.reply_text(f"Prediksi 24 jam ke depan ({target_name}): {preview}, ...")
    else:
        direction = forecast["trend"].get("direction", "belum_cukup_data")
        await update.message.reply_text(
            f"Belum ada model terlatih untuk {target_name}. Tren sementara (regresi sederhana): {direction}."
        )


async def send_alert(chat_id: str, message: str) -> None:
    """Kirim pesan push ke satu chat -- dipanggil dari app/alerting.py."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.info(f"[dry-run] TELEGRAM_BOT_TOKEN belum di-set, alert tidak dikirim: {message}")
        return

    from telegram import Bot
    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        logger.warning(f"Gagal kirim alert Telegram ke chat {chat_id}: {e}")


def build_application() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum di-set di environment.")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("link", handle_link))
    application.add_handler(CommandHandler("status", handle_status))
    application.add_handler(CommandHandler("forecast", handle_forecast))
    return application
