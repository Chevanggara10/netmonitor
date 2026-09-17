"""
Entrypoint untuk menjalankan bot Telegram sebagai proses TERPISAH dari
web server. Jalankan di terminal/proses sendiri:

    python run_telegram_bot.py

Butuh TELEGRAM_BOT_TOKEN di environment atau file .env (dapatkan dari
@BotFather di Telegram).
"""
import logging
from dotenv import load_dotenv
load_dotenv()

from app.telegram_bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

if __name__ == "__main__":
    app = build_application()
    print("Bot Telegram berjalan (polling mode). Ctrl+C untuk berhenti.")
    app.run_polling()
