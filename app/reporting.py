"""
Agregasi data historis & export laporan (Fase 5).

Kenapa agregasi per jam diperlukan: grafik jangka panjang (mis. 30 hari)
kalau langsung ambil semua baris CheckResult mentah bisa jadi puluhan ribu
titik data -- lambat di-query dan tidak berguna divisualisasikan (terlalu
padat). Agregasi per jam meringkas itu jadi 1 titik data per jam per
target, jauh lebih ringan untuk grafik jangka panjang.

Pendekatan: agregasi dihitung on-the-fly lewat SQL GROUP BY per jam
(bukan tabel terpisah yang perlu dijaga sinkron) -- lebih sederhana untuk
skala saat ini, dan tetap cukup cepat karena kolom checked_at sudah
diindeks. Kalau nanti skalanya jauh lebih besar (jutaan baris), baru
perlu upgrade ke tabel agregat yang di-refresh berkala.
"""
import csv
import io
from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import CheckResult


async def get_hourly_aggregate(target_id: int, db: AsyncSession, days: int = 7) -> list[dict]:
    """
    Meringkas riwayat check sebuah target jadi 1 baris per jam:
    rata-rata response time, persentase uptime, dan jumlah check pada
    jam tersebut. Dipakai untuk grafik jangka panjang di dashboard.
    """
    since = datetime.utcnow() - timedelta(days=days)

    # strftime('%Y-%m-%d %H') mengelompokkan baris ke jam yang sama --
    # spesifik untuk SQLite. Kalau nanti migrasi ke PostgreSQL, ganti ke
    # date_trunc('hour', checked_at).
    hour_bucket = func.strftime('%Y-%m-%d %H:00:00', CheckResult.checked_at)

    result = await db.execute(
        select(
            hour_bucket.label("hour"),
            func.avg(CheckResult.response_time_ms).label("avg_response_time_ms"),
            func.count(CheckResult.id).label("total_checks"),
            func.sum(func.iif(CheckResult.is_up == True, 1, 0)).label("up_count"),
        )
        .where(CheckResult.target_id == target_id, CheckResult.checked_at >= since)
        .group_by(hour_bucket)
        .order_by(hour_bucket.asc())
    )

    rows = result.all()
    return [
        {
            "hour": row.hour,
            "avg_response_time_ms": round(row.avg_response_time_ms, 2) if row.avg_response_time_ms else None,
            "total_checks": row.total_checks,
            "uptime_percent": round((row.up_count or 0) / row.total_checks * 100, 2) if row.total_checks else 0.0,
        }
        for row in rows
    ]


async def export_checks_to_csv(target_id: int, target_name: str, db: AsyncSession, days: int = 30) -> str:
    """
    Menghasilkan konten CSV (sebagai string) berisi riwayat check mentah
    sebuah target, untuk didownload sebagai laporan.
    """
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(CheckResult)
        .where(CheckResult.target_id == target_id, CheckResult.checked_at >= since)
        .order_by(CheckResult.checked_at.asc())
    )
    checks = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "target_name", "checked_at", "status_code", "response_time_ms",
        "is_up", "error_message", "is_anomaly", "anomaly_z_score",
    ])
    for c in checks:
        writer.writerow([
            target_name, c.checked_at.isoformat(), c.status_code, c.response_time_ms,
            c.is_up, c.error_message or "", c.is_anomaly, c.anomaly_z_score,
        ])

    return output.getvalue()
