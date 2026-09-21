# Deploy ke VPS Linux (domain Namecheap)

Vercel tidak cocok untuk backend ini (butuh proses berjalan terus: scheduler, bot polling, SQLite). Pakai VPS Linux mana pun.

1. **DNS Namecheap:** Domain List > Manage > Advanced DNS > tambah *A Record*: Host `monitor` (atau `@`), Value = IP publik VPS, TTL Automatic. Tunggu propagasi (menit-jam).
2. **Server:** `sudo useradd -m netmonitor`, salin project ke `/opt/netmonitor`, lalu
   `python3 -m venv venv && venv/bin/pip install -r requirements.txt`.
3. **`.env`** (chmod 600): `NETMONITOR_SECRET_KEY` (acak >=32 karakter), `TELEGRAM_BOT_TOKEN`, `RESEND_API_KEY`, opsional `REPORT_TIMEZONE=Asia/Jakarta`.
4. **Layanan:** `sudo cp deploy/*.service /etc/systemd/system/ && sudo systemctl enable --now netmonitor-web netmonitor-bot`.
5. **HTTPS:** pasang Caddy, salin `deploy/Caddyfile` ke `/etc/caddy/Caddyfile` (ganti domain), `sudo systemctl reload caddy`. Buka port 80/443 di firewall; port 8000 tetap lokal.
6. **Cek:** `venv/bin/python doctor.py`, buka https://domain-anda, hubungkan Telegram dari dashboard.
7. **Backup:** folder `backups/` ada di disk yang sama; salin berkala ke tempat lain (mis. `rsync`/`scp` via cron).

Alternatif Docker: `docker compose -f deploy/docker-compose.yml up -d`.
Data monitoring instansi sensitif: jangan aktifkan `REPORT_AI_ENABLED` (LLM eksternal) tanpa persetujuan.
