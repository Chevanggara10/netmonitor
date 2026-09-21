@echo off
rem Menjalankan Netmonitor otomatis setiap kali Anda login Windows.
rem Klik ganda untuk memasang. Untuk mencabut:  schtasks /Delete /TN Netmonitor /F
cd /d "%~dp0"
schtasks /Create /TN "Netmonitor" /SC ONLOGON /RL LIMITED /F /TR "\"%~dp0venv\Scripts\python.exe\" \"%~dp0run_all.py\" --no-browser"
if errorlevel 1 (echo [GAGAL] Tidak bisa membuat tugas terjadwal.) else (echo Terpasang. Netmonitor jalan otomatis saat login.)
pause
