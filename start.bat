@echo off
rem Klik ganda untuk menjalankan Netmonitor (web + bot Telegram).
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
    echo Pertama kali: menyiapkan lingkungan Python, tunggu sebentar...
    python -m venv venv || (echo [GAGAL] Python belum terpasang. Pasang dari python.org lalu ulangi. & pause & exit /b 1)
)
venv\Scripts\python.exe -c "import fastapi, telegram, statsmodels" 2>nul || (
    echo Memasang paket yang dibutuhkan...
    venv\Scripts\python.exe -m pip install -r requirements.txt || (echo [GAGAL] Pemasangan paket gagal. Cek internet. & pause & exit /b 1)
)
venv\Scripts\python.exe run_all.py
echo.
pause
