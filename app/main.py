"""
Aplikasi utama Network Flow Monitor.

Menjalankan:
- REST API autentikasi (register/login) dengan JWT
- REST API untuk mengelola target monitoring (tambah/hapus/lihat) -- semua
  diproteksi, tiap user hanya bisa lihat/kelola targetnya sendiri
- WebSocket untuk streaming hasil monitoring secara realtime
- Serve halaman dashboard (frontend sederhana)

Cara menjalankan (lihat README.md untuk detail):
    alembic upgrade head
    uvicorn app.main:app --reload
"""
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import logging
import secrets

from dotenv import load_dotenv
# Harus dipanggil sebelum modul app lain di-import -- auth.py dan
# email_service.py membaca os.environ.get(...) langsung di top-level
# modul (saat import), jadi .env wajib termuat lebih dulu.
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.log_safety import install_token_redaction
install_token_redaction()

from app.database import get_db
from app.models import MonitorTarget, CheckResult, User, AlertRule, TelegramLinkToken
from app.schemas import (
    TargetCreate, TargetOut, CheckResultOut, TargetSummary,
    UserCreate, UserOut, Token, AlertRuleCreate, AlertRuleOut,
    ChatQuestion, ChatAnswer,
)
from app.scheduler import scheduler, schedule_target, unschedule_target, load_all_targets_on_startup
from app.ws_manager import manager
from app.auth import hash_password, verify_password, create_access_token, get_current_user, decode_access_token
from app.email_service import send_email
from app.url_safety import validate_target_url, UnsafeURLError
from app.prediction import get_trend_for_target
from app.chatbot import answer_question
from app.reporting import get_hourly_aggregate, export_checks_to_csv
from app.excel_export import export_checks_to_excel
from app.ml_forecast import get_forecast

# Rate limiting berbasis IP pemanggil. Dipakai di endpoint yang rawan
# disalahgunakan: login (cegah brute-force password) dan pembuatan target
# (cegah satu akun spam ratusan target sekaligus).
limiter = Limiter(key_func=get_remote_address)

# Batas jumlah target aktif per user -- mencegah satu akun menghabiskan
# resource scheduler dengan ratusan target sekaligus.
MAX_TARGETS_PER_USER = 20


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    # Skema database dikelola via Alembic migration ("alembic upgrade head"),
    # bukan auto-create di sini. Jalankan migrasi sebelum start aplikasi.
    scheduler.start()
    await load_all_targets_on_startup()
    yield
    # --- Shutdown ---
    scheduler.shutdown(wait=False)


app = FastAPI(title="Network Flow Monitor", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------- Endpoint: Autentikasi ----------

@app.post("/api/auth/register", response_model=Token, status_code=201)
@limiter.limit("5/minute")
async def register(request: Request, payload: UserCreate, db: AsyncSession = Depends(get_db)):
    """Membuat akun baru. Langsung mengembalikan token supaya user tidak perlu login ulang."""
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email sudah terdaftar")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id, user.email)
    return Token(access_token=token, user=UserOut.model_validate(user))


@app.post("/api/auth/login", response_model=Token)
@limiter.limit("10/minute")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """
    Login. Memakai OAuth2PasswordRequestForm (field: username, password) supaya
    kompatibel dengan skema OAuth2PasswordBearer standar FastAPI -- di form ini
    'username' diisi dengan email.
    """
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Email atau password salah")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Akun nonaktif")

    token = create_access_token(user.id, user.email)
    return Token(access_token=token, user=UserOut.model_validate(user))


@app.get("/api/auth/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Cek siapa user yang sedang login berdasarkan token -- dipakai frontend saat load awal."""
    return current_user


# ---------- Endpoint: Kelola Target (diproteksi, per-user) ----------

@app.post("/api/targets", response_model=TargetOut)
@limiter.limit("20/minute")
async def create_target(
    request: Request,
    payload: TargetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Menambahkan web/server baru untuk dipantau. Langsung mulai dijadwalkan."""
    try:
        validate_target_url(str(payload.url))
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    count_result = await db.execute(
        select(func.count(MonitorTarget.id)).where(MonitorTarget.user_id == current_user.id)
    )
    existing_count = count_result.scalar_one()
    if existing_count >= MAX_TARGETS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"Batas maksimum {MAX_TARGETS_PER_USER} target per akun sudah tercapai. Hapus target lain dulu untuk menambah yang baru.",
        )

    target = MonitorTarget(
        user_id=current_user.id,
        name=payload.name,
        url=str(payload.url),
        interval_seconds=payload.interval_seconds,
        check_type=payload.check_type,
        tcp_port=payload.tcp_port,
        expected_content=payload.expected_content,
    )
    db.add(target)
    await db.commit()
    await db.refresh(target)
    schedule_target(target)
    return target


@app.get("/api/targets", response_model=list[TargetOut])
async def list_targets(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MonitorTarget)
        .where(MonitorTarget.user_id == current_user.id)
        .order_by(MonitorTarget.created_at.desc())
    )
    return result.scalars().all()


async def _get_owned_target_or_404(target_id: int, db: AsyncSession, current_user: User) -> MonitorTarget:
    """Helper: ambil target milik user ini, atau 404 kalau bukan miliknya/tidak ada.
    Sengaja 404 (bukan 403) untuk target milik user lain -- supaya tidak bocor
    informasi bahwa ID tersebut valid tapi milik orang lain."""
    target = await db.get(MonitorTarget, target_id)
    if not target or target.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Target tidak ditemukan")
    return target


@app.delete("/api/targets/{target_id}")
async def delete_target(
    target_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = await _get_owned_target_or_404(target_id, db, current_user)
    unschedule_target(target_id)
    await db.delete(target)
    await db.commit()
    return {"detail": "Target dihapus"}


@app.patch("/api/targets/{target_id}/toggle", response_model=TargetOut)
async def toggle_target(
    target_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mengaktifkan/menonaktifkan monitoring untuk sebuah target tanpa menghapusnya."""
    target = await _get_owned_target_or_404(target_id, db, current_user)
    target.is_active = not target.is_active
    await db.commit()
    await db.refresh(target)
    if target.is_active:
        schedule_target(target)
    else:
        unschedule_target(target_id)
    return target


# ---------- Endpoint: Data Monitoring (diproteksi, per-user) ----------

@app.get("/api/targets/{target_id}/checks", response_model=list[CheckResultOut])
async def get_target_checks(
    target_id: int,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Riwayat pengecekan terbaru untuk sebuah target (untuk grafik)."""
    await _get_owned_target_or_404(target_id, db, current_user)
    result = await db.execute(
        select(CheckResult)
        .where(CheckResult.target_id == target_id)
        .order_by(CheckResult.checked_at.desc())
        .limit(limit)
    )
    checks = result.scalars().all()
    return list(reversed(checks))


@app.get("/api/targets/{target_id}/history/hourly")
async def get_target_hourly_history(
    target_id: int,
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Riwayat teragregasi per jam -- untuk grafik jangka panjang (mingguan)
    yang tetap ringan walau data mentahnya sudah banyak.
    """
    target = await _get_owned_target_or_404(target_id, db, current_user)
    return await get_hourly_aggregate(target.id, db, days=min(days, 90))


@app.get("/api/targets/{target_id}/export/csv")
async def export_target_csv(
    target_id: int,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download riwayat check sebuah target sebagai file CSV."""
    target = await _get_owned_target_or_404(target_id, db, current_user)
    csv_content = await export_checks_to_csv(target.id, target.name, db, days=min(days, 365))

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in target.name)
    filename = f"netmonitor_{safe_name}_{datetime.utcnow().strftime('%Y%m%d')}.csv"

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/targets/{target_id}/export/excel")
async def export_target_excel(
    target_id: int,
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download riwayat check sebuah target sebagai file Excel (.xlsx)."""
    target = await _get_owned_target_or_404(target_id, db, current_user)
    excel_content = await export_checks_to_excel(target.id, target.name, db, days=min(days, 365))

    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in target.name)
    filename = f"netmonitor_{safe_name}_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"

    return StreamingResponse(
        iter([excel_content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/targets/{target_id}/forecast")
async def get_target_forecast(
    target_id: int,
    horizon: int = 24,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Prediksi response time N jam ke depan, pakai model terlatih kalau ada."""
    await _get_owned_target_or_404(target_id, db, current_user)
    return await get_forecast(target_id, db, horizon_hours=min(horizon, 168))


@app.get("/api/summary", response_model=list[TargetSummary])
async def get_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ringkasan status semua target milik user ini: untuk dashboard utama."""
    targets_result = await db.execute(
        select(MonitorTarget)
        .where(MonitorTarget.user_id == current_user.id)
        .order_by(MonitorTarget.created_at.desc())
    )
    targets = targets_result.scalars().all()

    summaries = []
    since = datetime.utcnow() - timedelta(hours=24)

    for target in targets:
        latest_result = await db.execute(
            select(CheckResult)
            .where(CheckResult.target_id == target.id)
            .order_by(CheckResult.checked_at.desc())
            .limit(1)
        )
        latest_check = latest_result.scalar_one_or_none()

        stats_result = await db.execute(
            select(
                func.count(CheckResult.id),
                func.sum(func.iif(CheckResult.is_up == True, 1, 0)),
                func.avg(CheckResult.response_time_ms),
            ).where(CheckResult.target_id == target.id, CheckResult.checked_at >= since)
        )
        total, up_count, avg_rt = stats_result.one()
        uptime_percent = ((up_count or 0) / total * 100) if total else 0.0

        trend = await get_trend_for_target(target.id, db)

        summaries.append(TargetSummary(
            target=TargetOut.model_validate(target),
            latest_check=CheckResultOut.model_validate(latest_check) if latest_check else None,
            uptime_percent_24h=round(uptime_percent, 2),
            avg_response_time_ms=round(avg_rt, 2) if avg_rt else None,
            trend=trend,
        ))

    return summaries


# ---------- Endpoint: Alert Rule per Target ----------

@app.put("/api/targets/{target_id}/alert-rule", response_model=AlertRuleOut)
async def upsert_alert_rule(
    target_id: int,
    payload: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Membuat atau memperbarui aturan alert untuk sebuah target. Dipanggil
    ulang dengan payload baru akan meng-update aturan yang sudah ada
    (bukan bikin duplikat), karena relasinya one-to-one per target.
    """
    target = await _get_owned_target_or_404(target_id, db, current_user)

    result = await db.execute(select(AlertRule).where(AlertRule.target_id == target.id))
    rule = result.scalar_one_or_none()

    if rule is None:
        rule = AlertRule(target_id=target.id)
        db.add(rule)

    rule.is_enabled = payload.is_enabled
    rule.failure_threshold = payload.failure_threshold
    rule.cooldown_minutes = payload.cooldown_minutes
    rule.notify_email = str(payload.notify_email) if payload.notify_email else current_user.email

    await db.commit()
    await db.refresh(rule)
    return rule


@app.get("/api/targets/{target_id}/alert-rule", response_model=AlertRuleOut | None)
async def get_alert_rule(
    target_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_owned_target_or_404(target_id, db, current_user)
    result = await db.execute(select(AlertRule).where(AlertRule.target_id == target_id))
    return result.scalar_one_or_none()


# ---------- Endpoint: Test Email (SendGrid) ----------

@app.post("/api/test-email")
async def test_email(current_user: User = Depends(get_current_user)):
    """
    Kirim email percobaan ke alamat akun yang sedang login. Dipakai untuk
    memverifikasi integrasi SendGrid sudah benar sebelum dipakai untuk
    alert sungguhan. Kalau SENDGRID_API_KEY belum di-set, tetap mengembalikan
    200 dengan dry_run=true (bukan error) supaya jelas ini masalah konfigurasi,
    bukan bug aplikasi.
    """
    result = await send_email(
        to_email=current_user.email,
        subject="✅ Test Email dari Network Flow Monitor",
        content=(
            "Ini email percobaan dari aplikasi Network Flow Monitor kamu.\n\n"
            "Kalau kamu menerima email ini, integrasi SendGrid sudah benar "
            "dan siap dipakai untuk alert otomatis saat target down."
        ),
    )
    return result.to_dict()


# ---------- Endpoint: Telegram Link ----------

async def _generate_telegram_link_token(user_id: int, db: AsyncSession) -> str:
    """Buat token sekali-pakai (expire 10 menit) untuk menghubungkan akun ke bot Telegram."""
    token = secrets.token_urlsafe(24)
    link = TelegramLinkToken(
        token=token,
        user_id=user_id,
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db.add(link)
    await db.commit()
    return token


@app.post("/api/telegram/link-token")
async def create_telegram_link_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate token sekali-pakai untuk dikirim user ke bot Telegram lewat
    perintah /link <token>. Token berlaku 10 menit.
    """
    token = await _generate_telegram_link_token(current_user.id, db)
    return {"token": token, "expires_in_minutes": 10, "instruction": "Kirim '/link <token>' ke bot Telegram kamu dalam 10 menit."}


# ---------- Endpoint: Chatbot Berbasis Data ----------

@app.post("/api/ask", response_model=ChatAnswer)
@limiter.limit("30/minute")
async def ask_chatbot(
    request: Request,
    payload: ChatQuestion,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Jawab pertanyaan seputar status monitoring dari data tersimpan
    (rule-based, bukan LLM). Hanya menjawab berdasarkan target milik
    user yang sedang login.
    """
    answer = await answer_question(payload.question, current_user.id, db)
    return ChatAnswer(answer=answer)


# ---------- WebSocket: Streaming Realtime ----------
# Wajib bawa token JWT valid lewat query param ?token=... saat connect --
# tanpa ini koneksi ditolak sebelum accept(). Broadcast hasil check juga
# difilter per user_id di ws_manager.py, supaya data monitoring satu akun
# tidak bocor ke koneksi akun lain yang sedang online.

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None):
    if not token:
        await websocket.close(code=1008)  # policy violation
        return
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (HTTPException, KeyError, ValueError, TypeError):
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------- Serve Dashboard (Frontend) ----------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_dashboard():
    return FileResponse("static/index.html")
