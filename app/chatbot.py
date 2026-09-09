"""
Chatbot rule-based: menjawab pertanyaan seputar status monitoring dari
data yang tersimpan di database -- bukan LLM, murni pencocokan kata kunci
+ query terstruktur ke database.

Kenapa rule-based cukup untuk kasus ini: pertanyaan seputar status server
sifatnya terstruktur dan jawabannya adalah angka/fakta pasti dari database
("server mana paling sering down", "berapa rata-rata uptime") -- bukan
open-ended reasoning yang butuh pemahaman bahasa bebas. LLM API sungguhan
bisa dipasang di atas ini nanti sebagai lapisan bahasa yang lebih natural
(lihat catatan di akhir file), tapi logika pengambilan datanya tetap sama.
"""
import re
from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import MonitorTarget, CheckResult


async def answer_question(question: str, user_id: int, db: AsyncSession) -> str:
    """
    Mencocokkan pertanyaan ke salah satu pola yang dikenali, menjalankan
    query yang sesuai, dan mengembalikan jawaban dalam bahasa natural.
    Semua query dibatasi ke target milik user_id ini saja.
    """
    q = question.lower().strip()

    if _matches(q, ["paling sering down", "paling sering mati", "sering down", "paling banyak down"]):
        return await _answer_most_down(user_id, db)

    if _matches(q, ["rata-rata uptime", "uptime rata-rata", "uptime keseluruhan"]):
        return await _answer_average_uptime(user_id, db)

    if _matches(q, ["berapa target", "jumlah target", "ada berapa server", "berapa server"]):
        return await _answer_target_count(user_id, db)

    if _matches(q, ["paling lambat", "response time paling tinggi", "latency tertinggi", "paling lemot"]):
        return await _answer_slowest_target(user_id, db)

    if _matches(q, ["paling cepat", "response time paling rendah", "latency terendah"]):
        return await _answer_fastest_target(user_id, db)

    if _matches(q, ["yang down", "mana yang down", "server down", "target down", "sedang down"]):
        return await _answer_currently_down(user_id, db)

    if _matches(q, ["anomali", "ada yang aneh"]):
        return await _answer_anomalies(user_id, db)

    return (
        "Maaf, saya belum paham pertanyaan itu. Beberapa hal yang bisa saya jawab:\n"
        "- \"server mana yang paling sering down?\"\n"
        "- \"berapa rata-rata uptime?\"\n"
        "- \"ada berapa target?\"\n"
        "- \"server mana yang paling lambat?\"\n"
        "- \"target mana yang sedang down?\"\n"
        "- \"ada anomali tidak?\""
    )


def _matches(question: str, keywords: list[str]) -> bool:
    return any(kw in question for kw in keywords)


async def _get_user_target_ids(user_id: int, db: AsyncSession) -> list[int]:
    result = await db.execute(select(MonitorTarget.id).where(MonitorTarget.user_id == user_id))
    return [row[0] for row in result.all()]


async def _answer_most_down(user_id: int, db: AsyncSession) -> str:
    since = datetime.utcnow() - timedelta(days=7)
    result = await db.execute(
        select(MonitorTarget.name, func.count(CheckResult.id).label("down_count"))
        .join(CheckResult, CheckResult.target_id == MonitorTarget.id)
        .where(MonitorTarget.user_id == user_id, CheckResult.is_up == False, CheckResult.checked_at >= since)
        .group_by(MonitorTarget.name)
        .order_by(func.count(CheckResult.id).desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return "Kabar baik! Tidak ada target yang down dalam 7 hari terakhir."
    return f"Target yang paling sering down dalam 7 hari terakhir adalah \"{row[0]}\", dengan {row[1]}x gagal check."


async def _answer_average_uptime(user_id: int, db: AsyncSession) -> str:
    since = datetime.utcnow() - timedelta(hours=24)
    target_ids = await _get_user_target_ids(user_id, db)
    if not target_ids:
        return "Kamu belum punya target yang dipantau."
    result = await db.execute(
        select(func.count(CheckResult.id), func.sum(func.iif(CheckResult.is_up == True, 1, 0)))
        .where(CheckResult.target_id.in_(target_ids), CheckResult.checked_at >= since)
    )
    total, up = result.one()
    if not total:
        return "Belum ada data check dalam 24 jam terakhir untuk dihitung."
    pct = round((up or 0) / total * 100, 1)
    return f"Rata-rata uptime seluruh target dalam 24 jam terakhir adalah {pct}%."


async def _answer_target_count(user_id: int, db: AsyncSession) -> str:
    result = await db.execute(
        select(func.count(MonitorTarget.id), func.sum(func.iif(MonitorTarget.is_active == True, 1, 0)))
        .where(MonitorTarget.user_id == user_id)
    )
    total, active = result.one()
    if not total:
        return "Kamu belum menambahkan target apapun."
    return f"Kamu punya {total} target, {active or 0} di antaranya sedang aktif dipantau."


async def _answer_slowest_target(user_id: int, db: AsyncSession) -> str:
    since = datetime.utcnow() - timedelta(hours=24)
    result = await db.execute(
        select(MonitorTarget.name, func.avg(CheckResult.response_time_ms).label("avg_rt"))
        .join(CheckResult, CheckResult.target_id == MonitorTarget.id)
        .where(MonitorTarget.user_id == user_id, CheckResult.is_up == True, CheckResult.checked_at >= since)
        .group_by(MonitorTarget.name)
        .order_by(func.avg(CheckResult.response_time_ms).desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return "Belum ada data response time yang cukup untuk dibandingkan."
    return f"Target paling lambat adalah \"{row[0]}\", dengan rata-rata response time {round(row[1], 1)}ms dalam 24 jam terakhir."


async def _answer_fastest_target(user_id: int, db: AsyncSession) -> str:
    since = datetime.utcnow() - timedelta(hours=24)
    result = await db.execute(
        select(MonitorTarget.name, func.avg(CheckResult.response_time_ms).label("avg_rt"))
        .join(CheckResult, CheckResult.target_id == MonitorTarget.id)
        .where(MonitorTarget.user_id == user_id, CheckResult.is_up == True, CheckResult.checked_at >= since)
        .group_by(MonitorTarget.name)
        .order_by(func.avg(CheckResult.response_time_ms).asc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return "Belum ada data response time yang cukup untuk dibandingkan."
    return f"Target paling cepat adalah \"{row[0]}\", dengan rata-rata response time {round(row[1], 1)}ms dalam 24 jam terakhir."


async def _answer_currently_down(user_id: int, db: AsyncSession) -> str:
    target_ids = await _get_user_target_ids(user_id, db)
    if not target_ids:
        return "Kamu belum punya target yang dipantau."

    down_names = []
    for target_id in target_ids:
        result = await db.execute(
            select(MonitorTarget.name, CheckResult.is_up)
            .join(CheckResult, CheckResult.target_id == MonitorTarget.id)
            .where(MonitorTarget.id == target_id)
            .order_by(CheckResult.checked_at.desc())
            .limit(1)
        )
        row = result.first()
        if row and not row[1]:
            down_names.append(row[0])

    if not down_names:
        return "Semua target sedang ONLINE. Tidak ada yang down saat ini."
    return f"Target yang sedang DOWN: {', '.join(down_names)}."


async def _answer_anomalies(user_id: int, db: AsyncSession) -> str:
    since = datetime.utcnow() - timedelta(hours=24)
    target_ids = await _get_user_target_ids(user_id, db)
    if not target_ids:
        return "Kamu belum punya target yang dipantau."
    result = await db.execute(
        select(MonitorTarget.name, func.count(CheckResult.id))
        .join(CheckResult, CheckResult.target_id == MonitorTarget.id)
        .where(
            MonitorTarget.id.in_(target_ids),
            CheckResult.is_anomaly == True,
            CheckResult.checked_at >= since,
        )
        .group_by(MonitorTarget.name)
    )
    rows = result.all()
    if not rows:
        return "Tidak ada anomali response time yang terdeteksi dalam 24 jam terakhir."
    parts = [f"{name} ({count}x)" for name, count in rows]
    return f"Anomali terdeteksi pada: {', '.join(parts)}."


# ---------------------------------------------------------------------------
# Catatan pengembangan lanjutan (opsional, di luar cakupan MVP ini):
# Kalau nanti ingin jawaban lebih natural/fleksibel (bukan cuma cocok kata
# kunci persis), lapisan LLM API (Claude/OpenAI) bisa ditambahkan di sini:
# ambil intent + data mentah dari fungsi-fungsi di atas, lalu minta LLM
# merangkainya jadi kalimat yang lebih luwes. Struktur data yang sudah
# terstruktur di atas tetap jadi sumber kebenaran -- LLM hanya memformat
# ulang, bukan menebak angka sendiri.
# ---------------------------------------------------------------------------
