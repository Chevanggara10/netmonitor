"""
Jalankan SELURUH sistem dengan satu perintah:  python run_all.py

Urutan: migrasi database -> pemeriksaan (doctor) -> web + bot Telegram
(otomatis dihidupkan ulang bila mati) -> buka browser. Ctrl+C menghentikan semuanya.
Opsi: --no-browser, --strict (kunci rahasia bawaan dianggap GAGAL; dipakai di server)
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
load_dotenv()

from app.doctor import WEB_PORT, exit_code, format_report, run_all_checks  # noqa: E402
from app.supervisor import ManagedProcess, supervise  # noqa: E402

# Host lokal aman (127.0.0.1); di server/Railway set NETMONITOR_HOST=0.0.0.0.
# Port mengikuti $PORT (dibaca doctor.WEB_PORT), default 8000.
HOST = os.environ.get("NETMONITOR_HOST", "127.0.0.1")
BOT_EXIT_NO_RESTART = 3  # sama dengan EXIT_NO_RESTART di run_telegram_bot.py


def wait_until_up(url: str, timeout: float = 40.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    print("== 1/3 Menyiapkan database ==")
    if subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT) != 0:
        print("[GAGAL] Migrasi database gagal. Pulihkan dari folder backups/ lalu coba lagi.")
        return 1

    print("\n== 2/3 Pemeriksaan sistem ==")
    results = run_all_checks(strict="--strict" in sys.argv)
    print(format_report(results))
    if exit_code(results) != 0:
        print("\nPerbaiki bagian [GAGAL] di atas, lalu jalankan lagi.")
        return 1

    print("\n== 3/3 Menjalankan layanan ==")
    procs = [ManagedProcess(
        "web", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(WEB_PORT)],
        cwd=ROOT)]
    if os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        procs.append(ManagedProcess("bot", [sys.executable, "run_telegram_bot.py"], cwd=ROOT,
                                    no_restart_codes=(BOT_EXIT_NO_RESTART,)))
    else:
        print("[info] TELEGRAM_BOT_TOKEN kosong: bot Telegram tidak dijalankan.")

    stop = threading.Event()
    url = f"http://127.0.0.1:{WEB_PORT}"
    if "--no-browser" not in sys.argv:
        def open_when_ready():
            if wait_until_up(url):
                print(f"\nDashboard siap: {url}  (Ctrl+C untuk berhenti)")
                webbrowser.open(url)
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        supervise(procs, stop)
    except KeyboardInterrupt:
        print("\nMenghentikan layanan...")
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
