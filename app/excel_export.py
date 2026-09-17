"""
Export riwayat check ke file Excel (.xlsx) -- dipakai untuk download
dari dashboard, dan sebagai dataset input untuk training model forecast
(training/train_forecast.py).

Dua sheet:
- "Raw Checks": data mentah, sama isinya dengan export CSV (reporting.py).
- "Hourly Aggregate": agregat per jam (reuse get_hourly_aggregate),
  supaya training script punya fitur time-series siap pakai tanpa
  perlu resample ulang dari jutaan baris mentah.
"""
import io
from datetime import datetime, timedelta
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import CheckResult
from app.reporting import get_hourly_aggregate

RAW_HEADER = [
    "target_name", "checked_at", "status_code", "response_time_ms",
    "is_up", "error_message", "is_anomaly", "anomaly_z_score",
]
HOURLY_HEADER = ["hour", "avg_response_time_ms", "total_checks", "uptime_percent"]


async def export_checks_to_excel(target_id: int, target_name: str, db: AsyncSession, days: int = 30) -> bytes:
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(CheckResult)
        .where(CheckResult.target_id == target_id, CheckResult.checked_at >= since)
        .order_by(CheckResult.checked_at.asc())
    )
    checks = result.scalars().all()

    wb = Workbook()
    raw_ws = wb.active
    raw_ws.title = "Raw Checks"
    raw_ws.append(RAW_HEADER)
    for c in checks:
        raw_ws.append([
            target_name, c.checked_at.isoformat(), c.status_code, c.response_time_ms,
            c.is_up, c.error_message or "", c.is_anomaly, c.anomaly_z_score,
        ])

    hourly_ws = wb.create_sheet("Hourly Aggregate")
    hourly_ws.append(HOURLY_HEADER)
    hourly_rows = await get_hourly_aggregate(target_id, db, days=days)
    for row in hourly_rows:
        hourly_ws.append([row["hour"], row["avg_response_time_ms"], row["total_checks"], row["uptime_percent"]])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
