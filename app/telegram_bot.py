"""
Bot Telegram: hubungkan akun web ke chat lewat /link, lalu /status,
/forecast, /unlink, /help. send_alert() dipanggil dari alerting.py untuk
push notification.

Dijalankan sebagai proses TERPISAH dari web server (lihat
run_telegram_bot.py) -- polling loop Telegram punya siklus hidup
sendiri, tidak boleh menjatuhkan web dashboard kalau reconnect/crash.

Prinsip: bot TIDAK BOLEH diam atau crash. Setiap handler dibungkus `_safe`
(error apa pun -> balasan ramah + log), kegagalan kirim tidak pernah
melempar exception ke pemanggil, dan token bot tidak pernah dicetak.
"""
import functools
import logging
import os
import re
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.database import AsyncSessionLocal
from app.ml_forecast import get_forecast
from app.models import CheckResult, MonitorTarget, TelegramLinkToken, User

logger = logging.getLogger("netmonitor.telegram_bot")

TOKEN_FORMAT = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
MAX_MESSAGE_LEN = 4000  # batas Telegram 4096
FRIENDLY_ERROR = "Terjadi kesalahan sementara di server. Coba lagi beberapa saat lagi; kalau berulang, hubungi admin."

HELP_TEXT = (
    "Perintah yang tersedia:\n"
    "/link KODE - hubungkan chat ini ke akun web (kode dari dashboard, berlaku 10 menit)\n"
    "/status - ringkasan semua target (ONLINE/OFFLINE)\n"
    "/forecast NAMA_TARGET - prediksi response time 24 jam ke depan\n"
    "/unlink - putuskan chat ini dari akun\n"
    "/help - tampilkan bantuan ini"
)

_FALLBACK_REASON_TEXT = {
    "no_model": "belum ada model terlatih untuk target ini",
    "model_stale": "model terlatih sudah terlalu lama (data > 7 hari), perlu dilatih ulang",
    "model_unreadable": "file model tidak bisa dibaca, perlu dilatih ulang",
    "invalid_forecast": "hasil model tidak valid, perlu dilatih ulang",
    "forecast_failed": "model gagal memprediksi, perlu dilatih ulang",
}


def _get_session() -> AsyncSession:
    """Terpisah jadi fungsi supaya gampang di-monkeypatch di test."""
    return AsyncSessionLocal()


def _safe(handler):
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await handler(update, context)
        except Exception:
            logger.exception(f"Handler {handler.__name__} gagal")
            try:
                await update.message.reply_text(FRIENDLY_ERROR)
            except Exception:
                logger.exception("Gagal mengirim balasan error ke pengguna")
    return wrapper


def _clip(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE_LEN else text[: MAX_MESSAGE_LEN - 1] + "…"


async def _find_user_by_chat(db: AsyncSession, chat_id: str) -> User | None:
    # .first() + urut id: data lama dengan chat_id ganda tidak boleh membuat bot crash.
    result = await db.execute(select(User).where(User.telegram_chat_id == chat_id).order_by(User.id))
    return result.scalars().first()


async def _linked_active_user(update: Update, db: AsyncSession) -> User | None:
    user = await _find_user_by_chat(db, str(update.effective_chat.id))
    if user is None:
        await update.message.reply_text("Chat ini belum terhubung ke akun. Kirim /link KODE (buat kode di dashboard) dulu.")
        return None
    if not user.is_active:
        await update.message.reply_text("Akun ini nonaktif. Hubungi admin.")
        return None
    return user


@_safe
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Halo! Saya bot Network Flow Monitor.\n\n" + HELP_TEXT)


@_safe
async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


@_safe
async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Perintah tidak dikenal. Ketik /help untuk daftar perintah.")


@_safe
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Pengguna sering menyalin "<KODE>" lengkap dengan kurung/kutip/spasi.
    raw = " ".join(context.args or []).strip()
    token_str = raw.strip("<>[]()'\"` \t").strip()
    if not token_str:
        await update.message.reply_text("Pakai format: /link KODE (buat kode di dashboard web, berlaku 10 menit).")
        return

    chat_id = str(update.effective_chat.id)
    now = datetime.utcnow()

    async with _get_session() as db:
        result = await db.execute(select(TelegramLinkToken).where(TelegramLinkToken.token == token_str))
        link = result.scalars().first()

        # Bersihkan semua token kedaluwarsa agar tabel tidak menumpuk.
        await db.execute(delete(TelegramLinkToken).where(TelegramLinkToken.expires_at < now))

        if link is None:
            await db.commit()
            await update.message.reply_text("Kode tidak valid atau sudah kedaluwarsa. Buat kode baru di dashboard.")
            return
        if link.expires_at < now:
            await db.commit()
            await update.message.reply_text("Kode sudah kedaluwarsa. Buat kode baru di dashboard.")
            return

        user = await db.get(User, link.user_id)
        if user is None or not user.is_active:
            await db.delete(link)
            await db.commit()
            await update.message.reply_text("Kode tidak valid (akun tidak ditemukan atau nonaktif).")
            return

        # Satu chat = satu akun: lepaskan chat ini dari akun lain.
        others = await db.execute(select(User).where(User.telegram_chat_id == chat_id, User.id != user.id))
        for other in others.scalars().all():
            other.telegram_chat_id = None

        user.telegram_chat_id = chat_id
        await db.execute(delete(TelegramLinkToken).where(TelegramLinkToken.user_id == user.id))
        await db.commit()

    await update.message.reply_text("Akun berhasil terhubung! Ketik /status untuk melihat ringkasan target kamu.")


@_safe
async def handle_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _get_session() as db:
        user = await _find_user_by_chat(db, str(update.effective_chat.id))
        if user is None:
            await update.message.reply_text("Chat ini belum terhubung ke akun mana pun.")
            return
        user.telegram_chat_id = None
        await db.commit()
    await update.message.reply_text("Koneksi diputus. Notifikasi ke chat ini dihentikan. Sambungkan lagi kapan saja dengan /link KODE.")


@_safe
async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with _get_session() as db:
        user = await _linked_active_user(update, db)
        if user is None:
            return

        result = await db.execute(select(MonitorTarget).where(MonitorTarget.user_id == user.id).order_by(MonitorTarget.id))
        targets = result.scalars().all()
        if not targets:
            await update.message.reply_text("Belum ada target monitoring. Tambahkan lewat dashboard web.")
            return

        lines = []
        for target in targets:
            latest_result = await db.execute(
                select(CheckResult).where(CheckResult.target_id == target.id)
                .order_by(CheckResult.checked_at.desc()).limit(1)
            )
            latest = latest_result.scalars().first()
            if latest is None:
                lines.append(f"- {target.name}: PENDING")
            elif latest.is_up:
                rt = f" ({latest.response_time_ms:.0f} ms)" if latest.response_time_ms is not None else ""
                lines.append(f"- {target.name}: ONLINE{rt}")
            else:
                lines.append(f"- {target.name}: OFFLINE")

    await update.message.reply_text(_clip("Ringkasan target:\n" + "\n".join(lines)))


def _format_forecast(name: str, forecast: dict) -> str:
    if forecast["source"] == "trained_model":
        points = list(zip(forecast["timestamps"], forecast["forecast_ms"]))
        rows = [f"{ts[11:16]}  {value:.0f} ms" for ts, value in points[:8]]
        values = forecast["forecast_ms"]
        return (
            f"Prediksi response time {name} (model AI)\n" + "\n".join(rows)
            + f"\n...\n{len(values)} jam ke depan: rata-rata {sum(values) / len(values):.0f} ms, "
              f"tertinggi {max(values):.0f} ms.\nData model sampai {forecast['trained_until'][:16].replace('T', ' ')} UTC."
        )
    reason = _FALLBACK_REASON_TEXT.get(forecast.get("fallback_reason", ""), "model tidak tersedia")
    direction = forecast["trend"].get("direction", "belum_cukup_data")
    return f"Prediksi model AI untuk {name} belum tersedia ({reason}).\nTren sementara (regresi sederhana): {direction}."


@_safe
async def handle_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = " ".join(context.args or []).strip()
    if not name:
        await update.message.reply_text("Pakai format: /forecast NAMA_TARGET (lihat nama di /status).")
        return

    async with _get_session() as db:
        user = await _linked_active_user(update, db)
        if user is None:
            return

        result = await db.execute(select(MonitorTarget).where(MonitorTarget.user_id == user.id).order_by(MonitorTarget.id))
        targets = result.scalars().all()
        matches = [t for t in targets if t.name.strip().lower() == name.lower()]
        if not matches:
            available = ", ".join(t.name for t in targets) or "(belum ada target)"
            await update.message.reply_text(f"Target '{name}' tidak ditemukan. Target kamu: {available}")
            return

        target = matches[0]
        forecast = await get_forecast(target.id, db, horizon_hours=24)

    await update.message.reply_text(_clip(_format_forecast(target.name, forecast)))


async def send_alert(chat_id: str, message: str) -> bool:
    """
    Kirim pesan push ke satu chat. TIDAK PERNAH melempar: mengembalikan True
    bila terkirim, False bila dry-run/gagal (dipanggil dari jalur monitoring
    yang tidak boleh terganggu masalah Telegram).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.info("[dry-run] TELEGRAM_BOT_TOKEN belum di-set, pesan tidak dikirim.")
        return False
    if not TOKEN_FORMAT.match(token):
        logger.warning("TELEGRAM_BOT_TOKEN formatnya tidak valid, pesan tidak dikirim.")
        return False

    try:
        async with Bot(token=token) as bot:
            await bot.send_message(chat_id=chat_id, text=_clip(message))
        return True
    except Exception as exc:
        logger.warning(f"Gagal kirim pesan Telegram ke chat {chat_id}: {type(exc).__name__}: {exc}")
        return False


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Error tak tertangani di bot", exc_info=context.error)


def build_application() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum di-set di .env (dapatkan dari @BotFather).")
    if not TOKEN_FORMAT.match(token):
        raise RuntimeError("TELEGRAM_BOT_TOKEN formatnya tidak valid (contoh: 123456789:AAE...). Salin ulang dari @BotFather.")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("help", handle_help))
    application.add_handler(CommandHandler("link", handle_link))
    application.add_handler(CommandHandler("unlink", handle_unlink))
    application.add_handler(CommandHandler("status", handle_status))
    application.add_handler(CommandHandler("forecast", handle_forecast))
    application.add_handler(MessageHandler(filters.TEXT | filters.COMMAND, handle_unknown))
    application.add_error_handler(_on_error)
    return application
