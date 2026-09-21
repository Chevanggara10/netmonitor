"""
Logika alert: dipanggil setiap kali ada CheckResult baru untuk menentukan
apakah notifikasi (email + Telegram) perlu dikirim, berdasarkan AlertRule
target tsb.

Aturan:
- Alert DOWN dikirim setelah consecutive_failures mencapai failure_threshold,
  DAN cooldown_minutes sejak alert terakhir sudah lewat (supaya tidak spam
  kalau downnya berkepanjangan). Cooldown baru "terpakai" kalau minimal satu
  kanal BENAR-BENAR terkirim -- kalau semua gagal, dicoba lagi di check berikut.
- Alert RECOVERY dikirim sekali saat target pulih dari status "sudah pernah
  alert down" -- ditandai dengan consecutive_failures kembali di-reset ke 0
  setelah sebelumnya sempat alert terkirim.
- Kegagalan kanal notifikasi (email/Telegram error, timeout) TIDAK PERNAH
  boleh mengganggu pipeline monitoring (scheduler menyiarkan hasil check ke
  dashboard setelah process_alert selesai).
"""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import MonitorTarget, CheckResult, AlertRule, User
from app.email_service import send_email, build_down_alert_email, build_recovery_email
from app.telegram_bot import send_alert

logger = logging.getLogger("netmonitor.alerting")

# Panggilan jaringan ke Telegram dibatasi supaya job scheduler tidak macet.
TELEGRAM_TIMEOUT_SECONDS = 15


async def _get_telegram_chat_id(target: MonitorTarget, db: AsyncSession) -> str | None:
    """Ambil telegram_chat_id pemilik target lewat query langsung -- tidak
    lewat target.owner (lazy-load relationship di context async bisa error).
    Pemilik nonaktif tidak dikirimi notifikasi."""
    if target.user_id is None:
        return None
    user = await db.get(User, target.user_id)
    if user is None or not user.is_active:
        return None
    return user.telegram_chat_id


async def _notify_telegram(target: MonitorTarget, db: AsyncSession, message: str) -> bool:
    """True hanya kalau pesan benar-benar terkirim. Tidak pernah melempar."""
    try:
        chat_id = await _get_telegram_chat_id(target, db)
        if not chat_id:
            return False
        return bool(await asyncio.wait_for(send_alert(chat_id, message), timeout=TELEGRAM_TIMEOUT_SECONDS))
    except asyncio.TimeoutError:
        logger.warning(f"Kirim Telegram untuk target={target.id} timeout > {TELEGRAM_TIMEOUT_SECONDS}s")
        return False
    except Exception:
        logger.exception(f"Kirim Telegram untuk target={target.id} gagal")
        return False


async def _notify_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """(terkirim_atau_dry_run, detail). Tidak pernah melempar."""
    try:
        email_result = await send_email(to_email, subject, body)
        return bool(email_result.sent or email_result.dry_run), email_result.detail
    except Exception as exc:
        logger.exception("Kirim email alert gagal")
        return False, f"error: {exc}"


async def process_alert(target: MonitorTarget, result: CheckResult, alert_rule: AlertRule | None, db: AsyncSession) -> None:
    """
    Update state AlertRule berdasarkan hasil check terbaru, dan kirim
    notifikasi kalau kondisi alert terpenuhi. Tidak melakukan apa-apa kalau
    target belum punya AlertRule atau alert_rule.is_enabled == False.
    """
    if alert_rule is None or not alert_rule.is_enabled:
        return

    if result.is_up:
        await _handle_up(target, alert_rule, db)
    else:
        await _handle_down(target, result, alert_rule, db)


async def _handle_down(target: MonitorTarget, result: CheckResult, alert_rule: AlertRule, db: AsyncSession) -> None:
    alert_rule.consecutive_failures += 1

    threshold_reached = alert_rule.consecutive_failures >= alert_rule.failure_threshold
    cooldown_passed = (
        alert_rule.last_alert_sent_at is None
        or datetime.utcnow() - alert_rule.last_alert_sent_at >= timedelta(minutes=alert_rule.cooldown_minutes)
    )

    if threshold_reached and cooldown_passed:
        delivered = False

        if alert_rule.notify_email:
            subject, body = build_down_alert_email(
                target_name=target.name,
                target_url=target.url,
                error_message=result.error_message,
                consecutive_failures=alert_rule.consecutive_failures,
            )
            email_ok, detail = await _notify_email(alert_rule.notify_email, subject, body)
            delivered = delivered or email_ok
            # dry-run dihitung terkirim supaya cooldown bisa diuji tanpa API key sungguhan.
            logger.info(
                f"Alert DOWN untuk target={target.id} ({target.name}): "
                f"consecutive_failures={alert_rule.consecutive_failures}, {detail}"
            )

        telegram_ok = await _notify_telegram(
            target, db,
            f"\U0001F534 DOWN: {target.name} ({target.url})\n"
            f"Error: {result.error_message or 'tidak diketahui'}\n"
            f"Gagal berturut-turut: {alert_rule.consecutive_failures}x",
        )
        delivered = delivered or telegram_ok

        if delivered:
            alert_rule.last_alert_sent_at = datetime.utcnow()

    await db.commit()


async def _handle_up(target: MonitorTarget, alert_rule: AlertRule, db: AsyncSession) -> None:
    # Kalau sebelumnya sempat gagal cukup untuk trigger alert (ditandai
    # last_alert_sent_at terisi) dan sekarang sudah UP lagi, kirim recovery
    # sekali, lalu reset state.
    was_in_alerted_down_state = (
        alert_rule.consecutive_failures >= alert_rule.failure_threshold
        and alert_rule.last_alert_sent_at is not None
    )

    if was_in_alerted_down_state:
        if alert_rule.notify_email:
            subject, body = build_recovery_email(target_name=target.name, target_url=target.url)
            _, detail = await _notify_email(alert_rule.notify_email, subject, body)
            logger.info(f"Alert RECOVERY untuk target={target.id} ({target.name}): {detail}")

        await _notify_telegram(
            target, db, f"\U0001F7E2 RECOVERY: {target.name} ({target.url}) sudah ONLINE lagi.",
        )

    if alert_rule.consecutive_failures != 0 or alert_rule.last_alert_sent_at is not None:
        alert_rule.consecutive_failures = 0
        alert_rule.last_alert_sent_at = None
        await db.commit()
