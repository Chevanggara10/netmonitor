"""
Prediksi tren ringan berbasis regresi linear sederhana (numpy polyfit
derajat 1) -- bukan model time-series kompleks seperti ARIMA/LSTM.

Cara kerja:
1. Ambil N hasil check terakhir yang berhasil (is_up=True).
2. Fit garis lurus terhadap response_time_ms-nya (sumbu-x = urutan check,
   sumbu-y = response time). Slope garis ini menunjukkan arah tren:
   - slope positif -> response time cenderung NAIK (makin lambat)
   - slope negatif/nol -> response time stabil/membaik
3. Kalau slope-nya cukup signifikan dibanding rata-rata response time saat
   ini, tandai sebagai "tren memburuk" -- sinyal dini kalau performa
   perlahan menurun sebelum sampai ke titik down beneran.

Kenapa ini cukup tanpa model time-series yang lebih canggih: tujuan di sini
cuma menangkap tren jangka pendek (makin lambat atau tidak), bukan
forecasting presisi jangka panjang. Regresi linear pada jendela data
terakhir sudah cukup untuk sinyal peringatan dini semacam ini.
"""
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import CheckResult

# Jumlah hasil check terakhir yang dipakai untuk fit garis tren
TREND_WINDOW = 15
# Minimum data sebelum prediksi tren mulai aktif
MIN_SAMPLES_FOR_TREND = 8
# Ambang batas: kalau proyeksi kenaikan response time selama TREND_WINDOW
# check berikutnya melebihi persentase ini dari rata-rata saat ini,
# dianggap tren memburuk yang layak diperingatkan.
TREND_WARNING_RATIO = 0.2  # 20%
# Catatan: nilai ini sempat diuji dengan data nyata (target dengan slope
# ~22ms/check, rata-rata ~1350ms, proyeksi kenaikan ~24%) -- pada 40%
# kasus ini tidak ter-flag, terlalu longgar untuk skenario nyata. Diturunkan
# ke 20% supaya lebih sensitif menangkap tren memburuk sejak dini.


def analyze_trend(response_times: list[float]) -> dict:
    """
    response_times: list response time (ms), urutan dari LAMA ke BARU.
    Mengembalikan dict berisi arah tren, slope, dan apakah perlu peringatan.
    """
    n = len(response_times)
    if n < MIN_SAMPLES_FOR_TREND:
        return {"direction": "belum_cukup_data", "slope_ms_per_check": None, "warning": False}

    y = np.array(response_times)
    x = np.arange(n)
    slope, intercept = np.polyfit(x, y, 1)

    mean_rt = float(np.mean(y))
    projected_increase = slope * TREND_WINDOW  # proyeksi kenaikan selama window berikutnya
    ratio = (projected_increase / mean_rt) if mean_rt > 0 else 0

    if slope <= 0:
        direction = "stabil_atau_membaik"
        warning = False
    elif ratio > TREND_WARNING_RATIO:
        direction = "memburuk"
        warning = True
    else:
        direction = "naik_ringan"
        warning = False

    return {
        "direction": direction,
        "slope_ms_per_check": round(float(slope), 3),
        "projected_increase_ms": round(float(projected_increase), 2),
        "warning": warning,
    }


async def get_trend_for_target(target_id: int, db: AsyncSession) -> dict:
    """Ambil histori response time target dan analisis trennya."""
    result = await db.execute(
        select(CheckResult.response_time_ms)
        .where(CheckResult.target_id == target_id, CheckResult.is_up == True)
        .order_by(CheckResult.checked_at.desc())
        .limit(TREND_WINDOW)
    )
    # Query mengambil dari TERBARU ke TERLAMA (desc), tapi analisis butuh
    # urutan LAMA ke BARU supaya slope positif = makin lambat seiring waktu.
    times = [row[0] for row in result.all() if row[0] is not None]
    times.reverse()
    return analyze_trend(times)
