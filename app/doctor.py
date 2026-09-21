"""
Pra-pemeriksaan ("dokter") sebelum sistem dijalankan. Setiap pemeriksaan
mengembalikan CheckResult dengan pesan + langkah perbaikan berbahasa
Indonesia, jadi pengguna tidak perlu membaca stack trace.

status: ok | warn (boleh lanjut) | fail (harus diperbaiki dulu)
"""
import importlib.util
import json
import os
import socket
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from app.database import PROJECT_ROOT, resolve_database_url
from app.single_instance import DEFAULT_LOCK_PORT

MIN_PYTHON = (3, 10)
REQUIRED_MODULES = [
    "fastapi", "uvicorn", "sqlalchemy", "aiosqlite", "alembic", "apscheduler",
    "slowapi", "jose", "httpx", "openpyxl", "pandas", "statsmodels", "joblib",
    "telegram", "matplotlib", "dotenv",
]
DEFAULT_SECRET = "dev-secret-key-ganti-saat-production"
WEB_PORT = int(os.environ.get("PORT", "8000"))  # Railway/Heroku memberi $PORT


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    fix: str = ""


def check_python(version: tuple | None = None) -> CheckResult:
    v = tuple(version or sys.version_info[:3])
    label = ".".join(str(x) for x in v)
    if v[:2] < MIN_PYTHON:
        return CheckResult("Python", "fail", f"Python {label} terlalu lama.",
                           f"Pasang Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} atau lebih baru dari python.org.")
    return CheckResult("Python", "ok", f"Python {label}")


def check_dependencies(modules: Sequence[str] | None = None) -> CheckResult:
    missing = [m for m in (modules or REQUIRED_MODULES) if importlib.util.find_spec(m) is None]
    if missing:
        return CheckResult("Paket Python", "fail", "Belum terpasang: " + ", ".join(missing),
                           "Jalankan: pip install -r requirements.txt  (atau klik start.bat)")
    return CheckResult("Paket Python", "ok", "Semua paket terpasang")


def check_env_file(root: str = PROJECT_ROOT) -> CheckResult:
    if os.path.isfile(os.path.join(root, ".env")):
        return CheckResult("File .env", "ok", "Ditemukan")
    return CheckResult("File .env", "warn", "File .env belum ada (memakai nilai bawaan).",
                       "Buat file .env berisi NETMONITOR_SECRET_KEY, RESEND_API_KEY, TELEGRAM_BOT_TOKEN.")


def check_secret_key(env: Mapping[str, str], strict: bool = False) -> CheckResult:
    key = env.get("NETMONITOR_SECRET_KEY", "")
    if not key or key == DEFAULT_SECRET or len(key) < 32:
        return CheckResult(
            "Kunci rahasia", "fail" if strict else "warn",
            "NETMONITOR_SECRET_KEY belum diisi / terlalu pendek (login tidak aman).",
            'Buat: python -c "import secrets; print(secrets.token_urlsafe(48))" lalu taruh di .env')
    return CheckResult("Kunci rahasia", "ok", "Terisi")


def _telegram_getme(token: str) -> str | None:
    """Kembalikan username bot bila token valid, None bila ditolak/gagal."""
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=8) as r:
            data = json.load(r)
        return data["result"]["username"] if data.get("ok") else None
    except Exception:
        return None


def check_bot_token(env: Mapping[str, str], verify: Callable[[str], str | None] | None = None) -> CheckResult:
    from app.telegram_bot import TOKEN_FORMAT
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return CheckResult("Bot Telegram", "warn", "TELEGRAM_BOT_TOKEN kosong: notifikasi Telegram nonaktif.",
                           "Ambil token dari @BotFather lalu isi di .env")
    if not TOKEN_FORMAT.match(token):
        return CheckResult("Bot Telegram", "fail", "Format token salah.",
                           "Salin ulang token dari @BotFather (bentuk 123456789:AAxxxx).")
    username = (verify or _telegram_getme)(token)
    if not username:
        return CheckResult("Bot Telegram", "fail", "Token ditolak Telegram atau internet tidak tersedia.",
                           "Cek koneksi internet; bila token pernah bocor, buat baru via @BotFather /revoke.")
    return CheckResult("Bot Telegram", "ok", f"Token valid (@{username})")


def _sqlite_path(url: str) -> str | None:
    marker = "sqlite+aiosqlite:///"
    return url[len(marker):] if url.startswith(marker) else None


def alembic_head(root: str = PROJECT_ROOT) -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        cfg = Config(os.path.join(root, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(root, "migrations"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        return None


def check_database(url: str | None = None, head_revision: str | None = None) -> CheckResult:
    url = url or resolve_database_url(os.environ.get("DATABASE_URL"))
    path = _sqlite_path(url)
    if path is None:
        return CheckResult("Database", "ok", "Database non-SQLite (dilewati)")
    head = head_revision if head_revision is not None else alembic_head()
    folder = os.path.dirname(path) or "."
    if not os.path.isdir(folder) or not os.access(folder, os.W_OK):
        return CheckResult("Database", "fail", f"Folder database tidak bisa ditulis: {folder}",
                           "Pindahkan project ke folder yang dapat ditulis (bukan Program Files).")
    if not os.path.exists(path):
        return CheckResult("Database", "ok", "Database baru akan dibuat saat pertama jalan")
    try:
        con = sqlite3.connect(path, timeout=5)
        try:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            row = None
        finally:
            con.close()
    except sqlite3.Error as exc:
        return CheckResult("Database", "fail", f"Database tidak bisa dibuka: {exc}",
                           "Pulihkan dari folder backups/ (salin backup terbaru menjadi netmonitor.db).")
    if head and (row is None or row[0] != head):
        return CheckResult("Database", "warn", "Skema database belum versi terbaru.",
                           "Jalankan: alembic upgrade head  (run_all.py melakukannya otomatis)")
    return CheckResult("Database", "ok", "Dapat dibuka dan skema terbaru")


def check_port(port: int, label: str) -> CheckResult:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0" if os.environ.get("NETMONITOR_HOST") == "0.0.0.0" else "127.0.0.1", port))
    except OSError:
        return CheckResult(f"Port {port} ({label})", "fail", "Port sudah dipakai program lain.",
                           "Tutup instance Netmonitor lain yang sedang berjalan, lalu coba lagi.")
    finally:
        s.close()
    return CheckResult(f"Port {port} ({label})", "ok", "Bebas")


def check_models_dir(path: str | None = None) -> CheckResult:
    path = path or os.path.join(PROJECT_ROOT, "training", "models")
    if not os.path.isdir(path):
        return CheckResult("Model prediksi", "warn", "Belum ada model terlatih (memakai tren linear).",
                           "Opsional: python -m training.train_forecast setelah data cukup (>48 jam).")
    n = len([f for f in os.listdir(path) if f.endswith(".pkl")])
    return CheckResult("Model prediksi", "ok" if n else "warn", f"{n} model terlatih")


def check_backup(backup_dir: str | None = None) -> CheckResult:
    from app.db_maintenance import latest_backup_age_hours
    backup_dir = backup_dir or os.environ.get("BACKUP_DIR") or os.path.join(PROJECT_ROOT, "backups")
    age = latest_backup_age_hours(backup_dir)
    if age is None:
        return CheckResult("Backup", "warn", "Belum ada backup.",
                           "Backup otomatis harian; manual: python -m app.db_maintenance backup")
    if age > 48:
        return CheckResult("Backup", "warn", f"Backup terakhir {age:.0f} jam lalu.",
                           "Pastikan sistem menyala agar backup harian berjalan.")
    return CheckResult("Backup", "ok", f"Backup terakhir {age:.0f} jam lalu")


def run_all_checks(env: Mapping[str, str] | None = None, check_ports: bool = True,
                   strict: bool = False) -> list[CheckResult]:
    env = env if env is not None else os.environ
    results = [check_python(), check_dependencies()]
    if results[1].status == "ok":
        results += [check_env_file(), check_secret_key(env, strict=strict), check_bot_token(env),
                    check_database()]
        if check_ports:
            results += [check_port(WEB_PORT, "web"), check_port(DEFAULT_LOCK_PORT, "kunci bot")]
        results += [check_models_dir(), check_backup()]
    return results


def exit_code(results: Sequence[CheckResult]) -> int:
    return 1 if any(r.status == "fail" for r in results) else 0


_ICON = {"ok": "[ OK ]", "warn": "[PERHATIAN]", "fail": "[GAGAL]"}


def format_report(results: Sequence[CheckResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"{_ICON[r.status]} {r.name}: {r.message}")
        if r.fix and r.status != "ok":
            lines.append(f"        -> {r.fix}")
    fails = sum(r.status == "fail" for r in results)
    warns = sum(r.status == "warn" for r in results)
    lines.append("")
    lines.append("Semua siap dijalankan." if not fails and not warns else
                 f"Ringkasan: {fails} gagal, {warns} perhatian.")
    return "\n".join(lines)
