"""
Scheduler: menjalankan pengecekan berkala untuk setiap target yang aktif,
lalu menyiarkan (broadcast) hasilnya ke semua client dashboard via WebSocket,
dan memproses alert (kirim email) kalau perlu.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import MonitorTarget, AlertRule
from app.checker import perform_check
from app.alerting import process_alert
from app.anomaly import detect_anomaly
from app.ws_manager import manager

scheduler = AsyncIOScheduler()


async def check_target_job(target_id: int):
    """Job yang dijalankan scheduler untuk satu target spesifik."""
    async with AsyncSessionLocal() as db:
        target = await db.get(MonitorTarget, target_id)
        if not target or not target.is_active:
            return
        result = await perform_check(target, db)

        # Anomaly detection: hanya relevan untuk hasil yang berhasil (UP) --
        # kalau targetnya down, itu sudah jelas bukan "anomali" biasa, itu
        # kasus yang sudah ditangani jalur alert down.
        if result.is_up and result.response_time_ms is not None:
            is_anomaly, z_score = await detect_anomaly(target.id, result.response_time_ms, db)
            # Simpan z_score selalu (kalau berhasil dihitung), bukan cuma
            # saat is_anomaly=True -- supaya tetap bisa dilihat/debug
            # seberapa jauh dari baseline meski belum melewati ambang batas.
            if z_score is not None or is_anomaly:
                result.is_anomaly = is_anomaly
                result.anomaly_z_score = z_score
                await db.commit()
                await db.refresh(result)

        alert_result = await db.execute(select(AlertRule).where(AlertRule.target_id == target.id))
        alert_rule = alert_result.scalar_one_or_none()
        await process_alert(target, result, alert_rule, db)

        await manager.broadcast({
            "type": "check_result",
            "target_id": target.id,
            "target_name": target.name,
            "status_code": result.status_code,
            "response_time_ms": result.response_time_ms,
            "is_up": result.is_up,
            "error_message": result.error_message,
            "checked_at": result.checked_at.isoformat(),
            "is_anomaly": result.is_anomaly,
            "anomaly_z_score": result.anomaly_z_score,
        })


def schedule_target(target: MonitorTarget):
    """Mendaftarkan/memperbarui jadwal pengecekan untuk sebuah target."""
    job_id = f"target_{target.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if target.is_active:
        scheduler.add_job(
            check_target_job,
            "interval",
            seconds=target.interval_seconds,
            args=[target.id],
            id=job_id,
            # Tidak set next_run_time -> APScheduler otomatis menjalankan
            # job pertama kali setelah 'interval_seconds' dari sekarang.
        )


def unschedule_target(target_id: int):
    job_id = f"target_{target_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def load_all_targets_on_startup():
    """Dipanggil saat aplikasi start: memuat semua target aktif dari DB ke scheduler."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(MonitorTarget).where(MonitorTarget.is_active == True))
        targets = result.scalars().all()
        for target in targets:
            schedule_target(target)
