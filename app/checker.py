"""
Modul inti yang melakukan pengecekan ke sebuah target.
Ini adalah 'sensor' yang mengukur network flow: response time, status, ukuran data.

Mendukung 4 jenis pengecekan (Fase 3):
- http    : GET request biasa, cek status code
- content : seperti http, tapi juga cek teks tertentu ada di body response
- ping    : ICMP ping lewat command sistem (tidak perlu privilege root)
- tcp     : coba buka koneksi TCP ke host:port tertentu (mis. cek database up)

Termasuk retry logic: 1x gagal belum tentu server benar-benar down --
bisa jadi hiccup jaringan sesaat. Sebelum menandai DOWN, dicoba ulang
beberapa kali dengan jeda singkat. Ini mengurangi false alarm alert.
"""
import asyncio
import platform
import re
import time
import httpx
from urllib.parse import urlparse
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import MonitorTarget, CheckResult
from app.url_safety import validate_target_url, UnsafeURLError

# Jumlah percobaan ulang SETELAH percobaan pertama gagal (total percobaan = 1 + RETRY_ATTEMPTS)
RETRY_ATTEMPTS = 2
# Jeda antar percobaan ulang, dalam detik
RETRY_DELAY_SECONDS = 1.5

# Banyak situs (Cloudflare, WAF, dsb) menolak request dengan 403 kalau
# User-Agent kosong/default seperti "python-httpx/x.x" -- itu pola umum
# yang dipakai untuk mendeteksi bot/scraper. Header ini membuat request
# monitoring terlihat seperti browser biasa, mengurangi false-positive 403.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 NetworkFlowMonitor/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
}


# ============================================================
# HTTP & CONTENT CHECK
# ============================================================

async def _http_attempt(url: str, expected_content: str | None = None) -> dict:
    """Satu kali percobaan HTTP GET ke url. Tidak pernah raise -- semua
    kegagalan dikonversi jadi dict dengan is_up=False.

    Kalau expected_content diisi (check_type="content"), target hanya
    dianggap UP kalau status code sukses DAN teks tsb ditemukan di body --
    berguna untuk mendeteksi "halaman 200 tapi isinya error" yang tidak
    tertangkap oleh cek status code biasa.

    Fallback SSL: sebagian server (terutama situs pemerintah/institusi
    Indonesia) tidak mengirim sertifikat perantara (intermediate cert)
    yang lengkap. Browser modern otomatis menambal ini, tapi Python tidak.
    Kalau verifikasi SSL standar gagal karena ini, dicoba sekali lagi
    tanpa verifikasi ketat -- tapi hasilnya tetap diberi catatan jelas.
    """
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=DEFAULT_HEADERS) as client:
            response = await client.get(url)
            return _evaluate_http_response(response, start, expected_content)
    except httpx.ConnectError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            return await _retry_without_ssl_verification(url, start, expected_content)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": "Connection error: tidak bisa terhubung ke server", "response_size_bytes": None}
    except httpx.TimeoutException:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": "Timeout: server tidak merespons dalam 10 detik", "response_size_bytes": None}
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": f"Error: {str(e)}", "response_size_bytes": None}


def _evaluate_http_response(response: httpx.Response, start: float, expected_content: str | None) -> dict:
    elapsed_ms = (time.perf_counter() - start) * 1000
    status_ok = 200 <= response.status_code < 400
    error_message = None

    if status_ok and expected_content:
        if expected_content not in response.text:
            status_ok = False
            error_message = f"Konten tidak ditemukan: teks '{expected_content}' tidak ada di response"

    return {
        "status_code": response.status_code,
        "response_time_ms": round(elapsed_ms, 2),
        "is_up": status_ok,
        "error_message": error_message,
        "response_size_bytes": len(response.content),
    }


async def _retry_without_ssl_verification(url: str, start: float, expected_content: str | None) -> dict:
    """Dipanggil hanya setelah verifikasi SSL standar gagal karena sertifikat
    rantai tidak lengkap dari server. Kalau percobaan tanpa verifikasi ini
    berhasil, target tetap ditandai UP -- tapi error_message diisi peringatan
    (bukan None) supaya tetap terlihat sebagai catatan di tooltip dashboard."""
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=DEFAULT_HEADERS, verify=False) as client:
            response = await client.get(url)
            result = _evaluate_http_response(response, start, expected_content)
            if result["is_up"]:
                result["error_message"] = (
                    "⚠️ Peringatan SSL: server tidak mengirim sertifikat rantai "
                    "lengkap (verifikasi dilewati untuk cek ini)"
                )
            return result
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": f"Error setelah fallback SSL: {str(e)}", "response_size_bytes": None}


# ============================================================
# PING (ICMP) CHECK
# ============================================================

async def _ping_attempt(url: str) -> dict:
    """
    Ping host lewat command sistem operasi (bukan raw socket ICMP) --
    supaya tidak butuh privilege root/administrator, yang mana raw socket
    ICMP mengharuskannya di Linux/Mac.

    Cross-platform: parameter command 'ping' beda antara Windows dan
    Unix (Linux/Mac), jadi dideteksi otomatis lewat platform.system().
    """
    host = urlparse(url).hostname or url  # dukung input host polos tanpa skema
    start = time.perf_counter()

    is_windows = platform.system().lower() == "windows"
    if is_windows:
        cmd = ["ping", "-n", "1", "-w", "5000", host]
    else:
        cmd = ["ping", "-c", "1", "-W", "5", host]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8.0)
        elapsed_ms = (time.perf_counter() - start) * 1000
        output = stdout.decode(errors="ignore")

        if proc.returncode == 0:
            # Coba ekstrak waktu round-trip asli dari output ping kalau ada,
            # supaya latency yang ditampilkan lebih akurat daripada cuma
            # waktu subprocess (yang termasuk overhead spawn process).
            match = re.search(r"time[=<]([\d.]+)\s*ms", output, re.IGNORECASE)
            actual_latency = float(match.group(1)) if match else round(elapsed_ms, 2)
            return {
                "status_code": None,
                "response_time_ms": actual_latency,
                "is_up": True,
                "error_message": None,
                "response_size_bytes": None,
            }
        else:
            return {
                "status_code": None,
                "response_time_ms": round(elapsed_ms, 2),
                "is_up": False,
                "error_message": "Ping gagal: host tidak merespons (timeout atau unreachable)",
                "response_size_bytes": None,
            }
    except asyncio.TimeoutError:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": "Ping timeout", "response_size_bytes": None}
    except FileNotFoundError:
        return {"status_code": None, "response_time_ms": 0.0, "is_up": False,
                "error_message": "Command 'ping' tidak ditemukan di sistem ini", "response_size_bytes": None}
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": f"Error: {str(e)}", "response_size_bytes": None}


# ============================================================
# TCP PORT CHECK
# ============================================================

async def _tcp_attempt(url: str, port: int) -> dict:
    """
    Coba buka koneksi TCP mentah ke host:port -- berguna untuk cek servis
    non-HTTP seperti database (PostgreSQL 5432, Redis 6379, dst) yang tidak
    punya endpoint HTTP untuk di-GET.
    """
    host = urlparse(url).hostname or url
    start = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=8.0
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        writer.close()
        await writer.wait_closed()
        return {
            "status_code": None,
            "response_time_ms": round(elapsed_ms, 2),
            "is_up": True,
            "error_message": None,
            "response_size_bytes": None,
        }
    except asyncio.TimeoutError:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": f"Timeout: port {port} tidak merespons", "response_size_bytes": None}
    except (ConnectionRefusedError, OSError) as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": f"Tidak bisa terhubung ke port {port}: {e}", "response_size_bytes": None}
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"status_code": None, "response_time_ms": round(elapsed_ms, 2), "is_up": False,
                "error_message": f"Error: {str(e)}", "response_size_bytes": None}


# ============================================================
# DISPATCHER + RETRY LOGIC
# ============================================================

async def _dispatch_single_attempt(target: MonitorTarget) -> dict:
    """Memanggil fungsi pengecekan yang sesuai berdasarkan target.check_type."""
    check_type = target.check_type or "http"

    if check_type == "ping":
        return await _ping_attempt(target.url)
    elif check_type == "tcp":
        if not target.tcp_port:
            return {"status_code": None, "response_time_ms": 0.0, "is_up": False,
                    "error_message": "Konfigurasi salah: check_type 'tcp' butuh tcp_port", "response_size_bytes": None}
        return await _tcp_attempt(target.url, target.tcp_port)
    elif check_type == "content":
        return await _http_attempt(target.url, expected_content=target.expected_content)
    else:  # "http" atau default
        return await _http_attempt(target.url)


async def perform_check(target: MonitorTarget, db: AsyncSession) -> CheckResult:
    """
    Melakukan pengecekan ke target sesuai check_type-nya, dengan retry:
    kalau percobaan pertama gagal, dicoba lagi sampai RETRY_ATTEMPTS kali
    dengan jeda singkat. Begitu ada 1 percobaan yang sukses, langsung
    dipakai hasilnya. Hasil akhir disimpan sebagai satu CheckResult.

    Ini mengurangi false alarm dari hiccup jaringan sesaat -- target baru
    benar-benar ditandai DOWN kalau semua percobaan gagal.

    Re-validasi SSRF di sini (bukan cuma saat target dibuat): domain publik
    yang lolos validasi awal bisa saja belakangan resolve ke IP privat/internal
    (DNS rebinding). Kalau itu terjadi, check dibatalkan dan ditandai gagal --
    tidak pernah benar-benar melakukan request ke alamat tsb.
    """
    try:
        validate_target_url(target.url)
    except UnsafeURLError as e:
        result = CheckResult(
            target_id=target.id, status_code=None, response_time_ms=0.0,
            is_up=False, error_message=f"Dibatalkan (validasi keamanan URL gagal): {e}",
            response_size_bytes=None,
        )
        db.add(result)
        await db.commit()
        await db.refresh(result)
        return result

    attempt_result = await _dispatch_single_attempt(target)
    attempts_made = 1

    while not attempt_result["is_up"] and attempts_made <= RETRY_ATTEMPTS:
        await asyncio.sleep(RETRY_DELAY_SECONDS)
        attempt_result = await _dispatch_single_attempt(target)
        attempts_made += 1

    # Kalau akhirnya tetap gagal setelah semua percobaan, catat berapa kali dicoba
    # supaya kelihatan di riwayat ini bukan cuma 1x gagal kebetulan.
    if not attempt_result["is_up"] and attempts_made > 1:
        original_error = attempt_result["error_message"] or "Unknown error"
        attempt_result["error_message"] = f"{original_error} (gagal setelah {attempts_made}x percobaan)"

    result = CheckResult(
        target_id=target.id,
        status_code=attempt_result["status_code"],
        response_time_ms=attempt_result["response_time_ms"],
        is_up=attempt_result["is_up"],
        error_message=attempt_result["error_message"],
        response_size_bytes=attempt_result["response_size_bytes"],
    )
    db.add(result)
    await db.commit()
    await db.refresh(result)
    return result
