"""
Anomaly detection ringan berbasis statistik (z-score) -- bukan machine
learning yang butuh training, cukup mean & standar deviasi dari histori
response time target yang bersangkutan.

Cara kerja:
1. Ambil N hasil check terakhir yang BERHASIL (is_up=True) untuk target ini,
   tidak termasuk hasil yang baru saja masuk.
2. Hitung rata-rata (mean) dan standar deviasi (std) dari response_time_ms-nya.
   Ini jadi "baseline" -- pola normal target tersebut.
3. Hitung z-score hasil check TERBARU: seberapa jauh (dalam satuan std dev)
   dari baseline. z = (nilai - mean) / std
4. Kalau z-score melewati ambang batas (default 3), tandai sebagai anomali
   -- response time-nya jauh LEBIH LAMBAT dari kebiasaan target ini.
   Sengaja one-tailed (cuma arah "lebih lambat"): response time yang
   tiba-tiba jauh lebih cepat dari biasanya bukan sinyal masalah untuk
   tujuan monitoring uptime/performa.

Kenapa ini cukup tanpa model ML yang lebih canggih: response time server
pada kondisi normal cenderung mengikuti distribusi yang mendekati normal
(banyak nilai berkumpul di sekitar rata-rata, jarang yang ekstrem). Z-score
adalah cara standar mendeteksi outlier dari distribusi semacam ini, dan
sudah cukup untuk kasus "server tiba-tiba jadi jauh lebih lambat dari
biasanya" tanpa perlu training data terpisah atau computing power besar.
"""
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import CheckResult

# Berapa banyak hasil check terakhir yang dipakai sebagai baseline "normal"
BASELINE_WINDOW = 20
# Minimum jumlah data historis sebelum anomaly detection mulai aktif --
# dengan data terlalu sedikit, mean/std belum representatif, jadi baru
# nge-flag apa-apa setelah ada cukup riwayat.
MIN_SAMPLES_FOR_BASELINE = 10
# Ambang batas z-score: semakin tinggi, semakin longgar (butuh penyimpangan
# lebih ekstrem baru dianggap anomali). 3.0 adalah nilai umum dipakai
# untuk mendeteksi outlier tanpa terlalu banyak false-positive.
Z_SCORE_THRESHOLD = 3.0


async def detect_anomaly(target_id: int, latest_response_time_ms: float, db: AsyncSession) -> tuple[bool, float | None]:
    """
    Mengembalikan (is_anomaly, z_score). z_score None kalau data historis
    belum cukup untuk membentuk baseline yang layak dipakai.
    """
    result = await db.execute(
        select(CheckResult.response_time_ms)
        .where(CheckResult.target_id == target_id, CheckResult.is_up == True)
        .order_by(CheckResult.checked_at.desc())
        .limit(BASELINE_WINDOW)
    )
    historical_times = [row[0] for row in result.all() if row[0] is not None]

    if len(historical_times) < MIN_SAMPLES_FOR_BASELINE:
        return False, None

    baseline = np.array(historical_times)
    mean = float(np.mean(baseline))
    std = float(np.std(baseline))

    if std == 0:
        # Semua nilai historis identik persis -- std dev 0 bikin z-score
        # tidak terdefinisi (pembagian dengan nol). Anggap anomali kalau
        # nilai baru jauh LEBIH LAMBAT dari nilai konstan itu.
        is_anomaly = latest_response_time_ms > mean
        return is_anomaly, None

    z_score = (latest_response_time_ms - mean) / std

    # Sengaja one-tailed: hanya tandai anomali kalau response time jauh
    # LEBIH LAMBAT dari baseline (z_score positif tinggi). Response time
    # yang tiba-tiba jauh lebih cepat dari biasanya (z_score negatif
    # ekstrem) secara statistik juga "outlier", tapi untuk tujuan
    # monitoring itu bukan sinyal masalah -- server jadi lebih cepat
    # bukan sesuatu yang perlu di-alert.
    is_anomaly = z_score > Z_SCORE_THRESHOLD
    return is_anomaly, round(z_score, 2)
