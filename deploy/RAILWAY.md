# Deploy ke Railway (dari repo GitHub)

Satu service menjalankan web + bot Telegram sekaligus lewat `run_all.py` (auto-restart), dan
satu Volume menyimpan SQLite serta backup. Alasan satu service: Volume Railway tidak bisa
dipakai bersama oleh dua service, sedangkan web dan bot harus berbagi database yang sama.

## Langkah
1. railway.com > **New Project > Deploy from GitHub repo** > pilih `Chevanggara10/netmonitor` (branch `master`).
   `railway.json` sudah mengatur perintah start dan health check.
2. Service > **Settings > Volumes > Add Volume**, mount path `/data`.
3. Service > **Variables**, isi:

   | Variabel | Nilai |
   |---|---|
   | `NETMONITOR_SECRET_KEY` | string acak >= 32 karakter (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
   | `TELEGRAM_BOT_TOKEN` | token dari @BotFather |
   | `RESEND_API_KEY` | key email Resend (bila dipakai) |
   | `DATABASE_URL` | `sqlite+aiosqlite:////data/netmonitor.db` (4 garis miring) |
   | `BACKUP_DIR` | `/data/backups` |
   | `NETMONITOR_HOST` | `0.0.0.0` |
   | `REPORT_TIMEZONE` | `Asia/Jakarta` |
   | `RAILWAY_DEPLOYMENT_OVERLAP_SECONDS` | `0` (cegah dua bot berjalan bersamaan saat deploy ulang) |

   **Jangan** menambahkan plugin PostgreSQL Railway: ia menimpa `DATABASE_URL`, dan sebagian query laporan masih khusus SQLite.
4. **Settings > Networking > Generate Domain** (atau *Custom Domain* dan arahkan CNAME dari Namecheap sesuai instruksi Railway).
5. Buka domain, daftar akun, klik *Hubungkan Telegram*, kirim `/link KODE` ke bot.

## Catatan
- Data lama: Volume awalnya kosong. Bila ingin membawa riwayat, unggah salinan `netmonitor.db` ke `/data` (mis. lewat `railway run` / shell service) sebelum dipakai.
- Deploy ulang menghentikan service beberapa detik (Volume hanya satu replika); data tetap aman.
- Model prediksi (`training/models/*.pkl`) tidak ikut repo; sistem memakai tren linear sampai model dilatih.
- Biaya: Railway berbayar pakai kredit (paket Hobby); cek harga terkini di railway.com/pricing.
- Data monitoring instansi sensitif: jangan aktifkan `REPORT_AI_ENABLED` tanpa persetujuan.
