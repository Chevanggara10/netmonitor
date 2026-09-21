"""
Entrypoint untuk menjalankan bot Telegram sebagai proses TERPISAH dari
web server. Jalankan di terminal/proses sendiri:

    python run_telegram_bot.py

Butuh TELEGRAM_BOT_TOKEN di environment atau file .env (dapatkan dari
@BotFather di Telegram).
"""
import logging
import sys
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.log_safety import install_token_redaction
install_token_redaction()

from app.single_instance import acquire_single_instance_lock, AlreadyRunningError
from app.telegram_bot import build_application

if __name__ == "__main__":
    try:
        # Referensi dipegang sampai proses selesai; OS melepas kunci saat exit/crash.
        _instance_lock = acquire_single_instance_lock()
    except AlreadyRunningError as exc:
        print(f"[batal] {exc}")
        sys.exit(1)

    app = build_application()
    print("Bot Telegram berjalan (polling mode). Ctrl+C untuk berhenti.")
    # Update lama yang menumpuk saat bot mati (mis. /link kedaluwarsa) dibuang,
    # bukan dieksekusi ulang tiba-tiba.
    app.run_polling(drop_pending_updates=True)
