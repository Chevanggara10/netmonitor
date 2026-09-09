"""
Modul pengiriman email -- mendukung 2 provider: Resend (direkomendasikan)
dan SendGrid (alternatif).

Memakai HTTP request langsung ke REST API masing-masing (bukan library
resmi mereka) supaya tidak menambah dependency besar -- httpx yang
sudah dipakai di checker.py cukup.

Kenapa Resend jadi rekomendasi utama: setup jauh lebih simpel (tidak
perlu verifikasi nomor telepon seperti SendGrid/Twilio yang kadang
bermasalah untuk nomor Indonesia), gratis 3.000 email/bulan (100/hari),
dan API-nya lebih modern/sederhana.

Konfigurasi lewat environment variable -- pilih SALAH SATU provider:

Resend (direkomendasikan):
- RESEND_API_KEY   : API key dari dashboard resend.com
- RESEND_FROM_EMAIL: alamat pengirim (bisa pakai onboarding@resend.dev
                      untuk testing tanpa perlu verifikasi domain dulu)

SendGrid (alternatif):
- SENDGRID_API_KEY   : API key dari dashboard SendGrid
- SENDGRID_FROM_EMAIL: alamat pengirim, harus sudah diverifikasi

Kalau tidak ada API key provider manapun yang di-set, modul ini masuk
"dry-run mode": tidak mencoba mengirim (supaya tidak crash di
development), cukup mencatat ke log bahwa email *akan* dikirim kalau
API key sudah ada.
"""
import os
import logging
import httpx

logger = logging.getLogger("netmonitor.email")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")
RESEND_API_URL = "https://api.resend.com/emails"

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "alerts@netmonitor.local")
SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"


class EmailResult:
    """Hasil percobaan kirim email -- dipakai supaya caller (endpoint API,
    scheduler alert) bisa tahu persis apa yang terjadi tanpa perlu parsing
    exception."""
    def __init__(self, sent: bool, dry_run: bool, detail: str):
        self.sent = sent
        self.dry_run = dry_run
        self.detail = detail

    def to_dict(self) -> dict:
        return {"sent": self.sent, "dry_run": self.dry_run, "detail": self.detail}


async def send_email(to_email: str, subject: str, content: str) -> EmailResult:
    """
    Kirim satu email. Selalu mengembalikan EmailResult, tidak pernah
    melempar exception ke pemanggil -- kegagalan kirim email tidak boleh
    menjatuhkan scheduler/API yang memanggilnya.

    Prioritas provider: Resend dulu (kalau RESEND_API_KEY ada), baru
    SendGrid (kalau SENDGRID_API_KEY ada), baru dry-run kalau tidak ada
    keduanya.
    """
    if RESEND_API_KEY:
        return await _send_via_resend(to_email, subject, content)
    if SENDGRID_API_KEY:
        return await _send_via_sendgrid(to_email, subject, content)

    msg = (
        "Tidak ada API key email (RESEND_API_KEY / SENDGRID_API_KEY) yang di-set. "
        f"Email TIDAK dikirim (dry-run). Andai dikirim: to={to_email}, subject='{subject}'"
    )
    logger.warning(msg)
    return EmailResult(sent=False, dry_run=True, detail=msg)


async def _send_via_resend(to_email: str, subject: str, content: str) -> EmailResult:
    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "text": content,
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(RESEND_API_URL, json=payload, headers=headers)
        # Resend mengembalikan 200 dengan {"id": "..."} kalau berhasil diterima untuk dikirim
        if response.status_code == 200:
            detail = f"Email terkirim ke {to_email} (via Resend)"
            logger.info(detail)
            return EmailResult(sent=True, dry_run=False, detail=detail)
        else:
            detail = f"Resend menolak request: HTTP {response.status_code} - {response.text[:300]}"
            logger.error(detail)
            return EmailResult(sent=False, dry_run=False, detail=detail)
    except httpx.RequestError as e:
        detail = f"Gagal menghubungi Resend: {e}"
        logger.error(detail)
        return EmailResult(sent=False, dry_run=False, detail=detail)


async def _send_via_sendgrid(to_email: str, subject: str, content: str) -> EmailResult:
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": SENDGRID_FROM_EMAIL},
        "subject": subject,
        "content": [{"type": "text/plain", "value": content}],
    }
    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(SENDGRID_API_URL, json=payload, headers=headers)
        # SendGrid mengembalikan 202 Accepted kalau berhasil diterima untuk dikirim
        if response.status_code == 202:
            detail = f"Email terkirim ke {to_email} (via SendGrid)"
            logger.info(detail)
            return EmailResult(sent=True, dry_run=False, detail=detail)
        else:
            detail = f"SendGrid menolak request: HTTP {response.status_code} - {response.text[:300]}"
            logger.error(detail)
            return EmailResult(sent=False, dry_run=False, detail=detail)
    except httpx.RequestError as e:
        detail = f"Gagal menghubungi SendGrid: {e}"
        logger.error(detail)
        return EmailResult(sent=False, dry_run=False, detail=detail)


def build_down_alert_email(target_name: str, target_url: str, error_message: str | None, consecutive_failures: int) -> tuple[str, str]:
    """Menyusun subject & body email untuk notifikasi target DOWN."""
    subject = f"⚠️ [NetMonitor] {target_name} sedang DOWN"
    body = (
        f"Target monitoring berikut terdeteksi DOWN:\n\n"
        f"Nama    : {target_name}\n"
        f"URL     : {target_url}\n"
        f"Gagal   : {consecutive_failures}x berturut-turut\n"
        f"Error   : {error_message or '-'}\n\n"
        f"Cek dashboard Network Flow Monitor kamu untuk detail lebih lanjut."
    )
    return subject, body


def build_recovery_email(target_name: str, target_url: str) -> tuple[str, str]:
    """Menyusun subject & body email untuk notifikasi target sudah pulih (UP lagi)."""
    subject = f"✅ [NetMonitor] {target_name} sudah kembali ONLINE"
    body = (
        f"Kabar baik -- target berikut sudah kembali normal:\n\n"
        f"Nama : {target_name}\n"
        f"URL  : {target_url}\n\n"
        f"Tidak perlu tindakan lebih lanjut."
    )
    return subject, body
