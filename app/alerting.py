"""
Logika alert: dipanggil setiap kali ada CheckResult baru untuk menentukan
apakah notifikasi email perlu dikirim, berdasarkan AlertRule target tsb.

Aturan:
- Alert DOWN dikirim setelah consecutive_failures mencapai failure_threshold,
  DAN cooldown_minutes sejak alert terakhir sudah lewat (supaya tidak spam
  kalau downnya berkepanjangan).
- Alert RECOVERY dikirim sekali saat target pulih dari status "sudah pernah
  alert down" -- ditandai dengan consecutive_failures kembali di-reset ke 0
  setelah sebelumnya sempat alert terkirim.
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import MonitorTarget, CheckResult, AlertRule
from app.email_service import send_email, build_down_alert_email, build_recovery_email

logger = logging.getLogger("netmonitor.alerting")


async def process_alert(target: MonitorTarget, result: CheckResult, alert_rule: AlertRule | None, db: AsyncSession) -> None:
    """
    Update state AlertRule berdasarkan hasil check terbaru, dan kirim email
    kalau kondisi alert terpenuhi. Tidak melakukan apa-apa kalau target
    belum punya AlertRule atau alert_rule.is_enabled == False.
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

    if threshold_reached and cooldown_passed and alert_rule.notify_email:
        subject, body = build_down_alert_email(
            target_name=target.name,
            target_url=target.url,
            error_message=result.error_message,
            consecutive_failures=alert_rule.consecutive_failures,
        )
        email_result = await send_email(alert_rule.notify_email, subject, body)
        if email_result.sent or email_result.dry_run:
            # Update last_alert_sent_at bahkan saat dry-run, supaya perilaku
            # cooldown tetap bisa diuji/diverifikasi tanpa API key sungguhan.
            alert_rule.last_alert_sent_at = datetime.utcnow()
        logger.info(
            f"Alert DOWN untuk target={target.id} ({target.name}): "
            f"consecutive_failures={alert_rule.consecutive_failures}, {email_result.detail}"
        )

    await db.commit()


async def _handle_up(target: MonitorTarget, alert_rule: AlertRule, db: AsyncSession) -> None:
    # Kalau sebelumnya sempat gagal cukup untuk trigger alert (ditandai
    # last_alert_sent_at terisi) dan sekarang sudah UP lagi, kirim recovery
    # email sekali, lalu reset state.
    was_in_alerted_down_state = (
        alert_rule.consecutive_failures >= alert_rule.failure_threshold
        and alert_rule.last_alert_sent_at is not None
    )

    if was_in_alerted_down_state and alert_rule.notify_email:
        subject, body = build_recovery_email(target_name=target.name, target_url=target.url)
        email_result = await send_email(alert_rule.notify_email, subject, body)
        logger.info(f"Alert RECOVERY untuk target={target.id} ({target.name}): {email_result.detail}")

    if alert_rule.consecutive_failures != 0 or alert_rule.last_alert_sent_at is not None:
        alert_rule.consecutive_failures = 0
        alert_rule.last_alert_sent_at = None
        await db.commit()
