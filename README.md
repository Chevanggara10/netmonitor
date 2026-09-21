# Network Flow Monitor — Tahap 1 (MVP)

Aplikasi web untuk memantau server/website: kamu input URL, sistem otomatis
mengecek secara berkala dan menampilkan status (UP/DOWN), response time,
status code, dan grafik realtime — semua lewat dashboard di browser.

## Mulai Cepat (3 langkah)

1. **Klik ganda `start.bat`.** Pertama kali, ia membuat lingkungan Python dan memasang paket sendiri.
2. **Tunggu browser terbuka** di http://127.0.0.1:8000 lalu daftar/masuk dan tambah target.
3. **Hubungkan Telegram:** di dashboard klik *Hubungkan Telegram*, salin perintah `/link KODE`, kirim ke bot Anda.

`start.bat` menjalankan `run_all.py`: migrasi database, pemeriksaan (`python doctor.py`), lalu web + bot
dengan auto-restart bila salah satu mati. Tekan Ctrl+C untuk berhenti. Agar jalan otomatis saat login
Windows, klik ganda `install_autostart.bat` (cabut: `schtasks /Delete /TN Netmonitor /F`).

### Masalah umum dan solusi

| Gejala | Penyebab | Solusi |
|---|---|---|
| `[GAGAL] Port 8000 ... dipakai` | Netmonitor lain sudah jalan | Tutup jendela lama, atau buka http://127.0.0.1:8000 langsung |
| `[GAGAL] Paket Python` | Paket belum terpasang | Klik `start.bat` (memasang otomatis) |
| `[GAGAL] Bot Telegram` token ditolak | Token salah/dicabut atau tanpa internet | Cek internet; buat token baru di @BotFather, isi `TELEGRAM_BOT_TOKEN` di `.env` |
| Bot tidak dijalankan | `TELEGRAM_BOT_TOKEN` kosong | Isi token di `.env` |
| Bot berhenti dan tidak hidup lagi | Ada instance bot lain (kode keluar 3) | Tutup bot lain, jalankan ulang `start.bat` |
| Kartu Telegram: "Tidak bisa terhubung ke server" | Sistem tidak berjalan | Jalankan `start.bat` |
| Tombol kirim laporan: "Hubungkan Telegram dulu" | Akun belum ditautkan | Klik *Hubungkan Telegram* |
| Prediksi memakai "tren linear" | Data belum cukup untuk model AI (butuh >48 jam kontinu) | Biarkan sistem menyala; latih model: `python -m training.train_forecast` |
| Database rusak | Mati listrik saat menulis | Salin backup terbaru dari `backups/` menjadi `netmonitor.db` |

## Fitur di Tahap 1 ini
- Input URL target langsung dari dashboard (tanpa perlu edit kode/config)
- Pengecekan otomatis berkala (interval bisa diatur per-target)
- Metrik yang diukur: status UP/DOWN, response time (ms), HTTP status code,
  ukuran response, uptime % 24 jam terakhir
- Dashboard realtime via WebSocket (grafik update otomatis tanpa refresh)
- Data tersimpan di database (riwayat time-series), tidak hilang saat restart
- Jeda/lanjutkan atau hapus target kapan saja
- **Autentikasi**: setiap akun hanya melihat & mengelola targetnya sendiri
- **Retry logic**: 1x gagal belum langsung DOWN — dicoba ulang 2x dengan
  jeda 1.5 detik sebelum benar-benar ditandai down, mengurangi false alarm
- **Alert email otomatis**: kirim notifikasi saat target down (setelah N
  kali gagal berturut-turut sesuai `failure_threshold`) dan saat target
  pulih (recovery email), dengan cooldown supaya tidak spam
- **Keamanan input (SSRF)**: URL target divalidasi, tidak bisa mengarah ke
  localhost/IP privat/cloud metadata endpoint
- **Rate limiting**: login dibatasi 10x/menit, pembuatan target 20x/menit,
  maksimum 20 target aktif per akun
- **4 jenis pengecekan**: HTTP (status code), Content (cek teks tertentu
  ada di response), Ping (ICMP lewat command sistem), TCP Port (cek servis
  non-HTTP seperti database)
- **Anomaly detection**: deteksi otomatis kalau response time jauh lebih
  lambat dari kebiasaan target tersebut (statistik z-score, bukan cuma
  threshold tetap) — badge "⚠ ANOMALI" muncul di dashboard
- **Prediksi tren**: regresi linear sederhana dari histori response time,
  memberi peringatan dini kalau performa perlahan memburuk sebelum benar-benar down
- **Chatbot berbasis data**: tanya-jawab seputar status monitoring ("server
  mana yang paling sering down?", "berapa rata-rata uptime?") langsung dari
  data tersimpan — rule-based, jawaban selalu berdasarkan angka nyata
- **Agregasi historis per jam**: riwayat panjang (mingguan) diringkas per
  jam supaya tetap cepat walau data mentah sudah menumpuk ribuan baris
- **Export CSV**: download riwayat check tiap target langsung dari dashboard

## Penjelasan Sistem (Arsitektur)

Sistem ini punya 3 proses yang berjalan terpisah tapi berbagi 1 database:

```
                        ┌──────────────────────┐
   Browser (dashboard) ─┤   Web Server          │
                        │   (uvicorn app.main)  │◄──── REST API + WebSocket
                        └──────────┬────────────┘
                                   │ baca/tulis
                                   ▼
                        ┌──────────────────────┐
                        │   Database            │
                        │   (SQLite/PostgreSQL) │
                        └──────────┬────────────┘
                                   │ baca/tulis
                        ┌──────────┴────────────┐
                        ▼                        ▼
              ┌──────────────────┐     ┌──────────────────────┐
              │  Bot Telegram     │     │  Training Script      │
              │  (run_telegram_   │     │  (train_forecast.py)  │
              │   bot.py, polling)│     │  dijalankan manual/    │
              └──────────────────┘     │  terjadwal (offline)  │
                                        └──────────────────────┘
```

**Alur kerja end-to-end:**
1. **Monitoring**: `scheduler.py` (jalan di dalam proses web server)
   mengecek tiap target sesuai interval, hasilnya (`checker.py`) disimpan
   sebagai baris `CheckResult` di database, lalu di-broadcast realtime ke
   dashboard lewat WebSocket. Kalau down/anomali, `alerting.py` mengirim
   notifikasi (email dan/atau Telegram).
2. **Export ke Excel**: dashboard/API (`excel_export.py`) mengambil baris
   `CheckResult` sebuah target, menulis 2 sheet (data mentah + agregat per
   jam) ke file `.xlsx` yang bisa didownload.
3. **Training model AI**: file Excel itu dipakai sebagai input
   `training/train_forecast.py` (dijalankan manual atau lewat scheduler
   OS, BUKAN bagian dari web server) — meresample data ke per-jam, melatih
   model forecasting (Holt-Winters), lalu menyimpan hasilnya sebagai file
   `.pkl` di `training/models/`.
4. **Serving prediksi**: `ml_forecast.py` (bagian dari web server) memuat
   file `.pkl` itu (kalau ada) untuk menjawab endpoint `/forecast`. Kalau
   belum ada model terlatih untuk suatu target, otomatis fallback ke
   `prediction.py` (regresi linear sederhana) — tidak pernah error 500
   hanya karena training belum dijalankan.
5. **Bot Telegram**: proses terpisah (`run_telegram_bot.py`) yang polling
   ke server Telegram, membaca/menulis ke database yang sama. User
   menghubungkan akun lewat `/link <token>`, lalu bisa `/status` (ringkasan
   semua target) dan `/forecast <nama_target>` (panggil `ml_forecast.py`
   yang sama dengan dashboard). Saat ada alert down/recovery, web server
   memanggil `telegram_bot.send_alert()` untuk push notifikasi.

**Kenapa 3 proses terpisah, bukan 1?**
- Bot Telegram pakai *long-polling* (looping terus-menerus nunggu pesan
  baru) — kalau digabung ke proses web server dan bot itu crash/reconnect
  karena masalah jaringan Telegram, dashboard ikut down. Dipisah supaya
  masing-masing independen.
- Training model bisa makan waktu (tergantung jumlah data), tidak cocok
  dijalankan di dalam siklus request HTTP yang harus cepat merespons.
  Makanya training dijalankan manual/terjadwal secara terpisah, hasilnya
  (file `.pkl`) baru "dibaca" oleh web server saat ada request forecast.

## Struktur Proyek
```
netmonitor/
├── app/
│   ├── main.py           # Entry point FastAPI: semua route REST + WebSocket
│   ├── database.py       # Koneksi & setup database (SQLite/PostgreSQL)
│   ├── models.py         # Skema tabel: User, MonitorTarget, CheckResult, AlertRule, TelegramLinkToken
│   ├── schemas.py        # Validasi data request/response (Pydantic)
│   ├── checker.py        # Logika inti: melakukan 1x pengecekan HTTP/Ping/TCP/Content
│   ├── scheduler.py      # Menjadwalkan pengecekan berkala per-target (APScheduler)
│   ├── ws_manager.py     # Kelola koneksi WebSocket untuk broadcast realtime
│   ├── anomaly.py        # Deteksi anomali response time (z-score)
│   ├── prediction.py     # Prediksi tren sederhana (regresi linear) -- fallback ml_forecast
│   ├── reporting.py      # Agregasi per jam + export CSV
│   ├── alerting.py       # Logika alert (email + Telegram) saat down/recovery
│   ├── chatbot.py        # Chatbot rule-based berbasis data monitoring
│   ├── excel_export.py   # Export riwayat check ke .xlsx (2 sheet: Raw Checks + Hourly Aggregate)
│   ├── ml_forecast.py    # Serve model forecast AI terlatih (fallback ke prediction.py)
│   └── telegram_bot.py   # Bot Telegram: /link, /status, /forecast, push alert
├── training/
│   ├── train_forecast.py # Script offline: baca Excel -> latih model Holt-Winters -> simpan .pkl
│   └── models/            # Model AI terlatih tersimpan di sini (.pkl, tidak di-commit ke git)
├── tests/                  # pytest: excel_export, ml_forecast, telegram_bot, alerting, dst
├── migrations/             # Migrasi database (Alembic)
│   ├── env.py
│   └── versions/           # Riwayat perubahan skema, urut kronologis
├── static/
│   └── index.html          # Dashboard (frontend) — HTML+JS, tanpa build step
├── run_telegram_bot.py     # Entry point proses TERPISAH untuk bot Telegram (polling)
├── alembic.ini
├── pytest.ini
├── requirements.txt
└── README.md
```

## Cara Menjalankan

1. Buat virtual environment (opsional tapi disarankan):
   ```bash
   python3 -m venv venv
   source venv/bin/activate      # Windows: venv\Scripts\activate
   ```

2. Install dependensi:
   ```bash
   pip install -r requirements.txt
   ```

3. Jalankan migrasi database (membuat semua tabel: users, monitor_targets, check_results, alert_rules):
   ```bash
   alembic upgrade head
   ```

4. Jalankan server:
   ```bash
   uvicorn app.main:app --reload
   ```

5. Buka browser ke:
   ```
   http://127.0.0.1:8000
   ```

6. **Daftar akun** (klik "daftar di sini" di layar login) — email dan
   password minimal 8 karakter, langsung masuk otomatis setelah daftar.

7. Di dashboard, masukkan nama, URL (harus lengkap dengan `https://` atau
   `http://`), dan interval pengecekan, lalu klik **+ Monitor**. Sistem
   langsung mulai memantau — hasil pertama muncul setelah 1x interval.
   Setiap akun hanya bisa melihat & mengelola targetnya sendiri.

### Menjalankan Fitur Excel Export + AI Forecast + Bot Telegram

Fitur monitoring dasar (langkah 1-7 di atas) sudah cukup untuk web
berjalan. Langkah berikut ini untuk fitur tambahan (opsional, boleh
di-skip kalau cuma butuh monitoring dasar):

**a) Export data ke Excel** (butuh sudah ada beberapa hari data check):
```bash
curl -X GET "http://127.0.0.1:8000/api/targets/1/export/excel" \
  -H "Authorization: Bearer <token_kamu>" -o laporan.xlsx
```

**b) Latih model AI dari Excel itu** (jalankan terpisah, bukan sambil
server jalan — script ini berhenti sendiri setelah selesai):
```bash
python training/train_forecast.py --excel laporan.xlsx --target-id 1
```
Kalau data belum cukup (minimal 48 titik data per jam, idealnya 2-3 hari
data terus-menerus), script akan bilang `[skip]` dan tidak membuat model
— itu bukan error, cuma belum cukup data. Setelah model berhasil dibuat
(`[ok] ... disimpan ...`), endpoint `/api/targets/{id}/forecast` otomatis
memakainya tanpa perlu restart server.

**c) Jalankan bot Telegram** (proses terpisah, di terminal lain):
```bash
python run_telegram_bot.py
```
Lihat bagian [Bot Telegram](#bot-telegram) di bawah untuk cara setup
token dan menghubungkan akun.

**d) (Opsional) Latih ulang model secara terjadwal.** Response time
berubah seiring waktu, jadi model idealnya dilatih ulang berkala (mis.
mingguan) dengan data terbaru. Di Windows, pakai **Task Scheduler**:
- Buka Task Scheduler → Create Basic Task
- Trigger: Weekly
- Action: Start a program →
  Program: `L:\...\netmonitor\venv\Scripts\python.exe`
  Arguments: `training\train_forecast.py --excel laporan.xlsx --target-id 1`
  Start in: folder project ini
(Ganti path Excel dengan hasil export terbaru, atau otomatiskan langkah
export-nya juga lewat script tambahan kalau mau full otomatis.)

## Catatan Teknis
- Database default: SQLite (`netmonitor.db`, otomatis dibuat). Cukup untuk
  development/skala kecil. Untuk produksi/skala besar, ganti `DATABASE_URL`
  di `app/database.py` ke PostgreSQL (idealnya dengan ekstensi TimescaleDB
  karena data ini bersifat time-series).
- Scheduler (`APScheduler`) berjalan di dalam proses yang sama dengan web
  server. Untuk skala lebih besar (banyak target, interval sangat pendek),
  ini sebaiknya dipisah jadi worker terpisah (mis. Celery + Redis).
- Setiap pengecekan mengukur waktu request dari sisi server aplikasi ini
  ke target — ini disebut *synthetic monitoring* (bukan capture paket
  mentah). Ini normal dan merupakan pendekatan standar untuk uptime/
  performance monitoring.
- **Migrasi database** dikelola dengan Alembic. Kalau nanti menambah/ubah
  kolom di `app/models.py`, jangan edit database manual — buat migrasi baru:
  ```bash
  alembic revision --autogenerate -m "deskripsi singkat perubahan"
  alembic upgrade head
  ```
  Selalu baca dulu file migrasi yang di-generate sebelum menjalankannya —
  autogenerate tidak selalu sempurna mendeteksi semua jenis perubahan.
- Skema `User` & `MonitorTarget.user_id` sudah disiapkan sejak Fase 2 hari
  ke-2. Endpoint register/login sudah aktif — semua endpoint target kini
  memerlukan login dan otomatis terfilter per-user.

## Environment Variables

Buat file `.env` di root proyek (atau set langsung di environment server)
untuk konfigurasi berikut. Semua punya default aman untuk development,
jadi aplikasi tetap bisa jalan tanpa file ini — tapi **wajib diisi
sebelum deploy ke production**:

| Variable | Wajib? | Default | Fungsi |
|---|---|---|---|
| `NETMONITOR_SECRET_KEY` | Wajib di production | `dev-secret-key-...` (tidak aman) | Kunci rahasia untuk sign JWT. Generate dengan: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `RESEND_API_KEY` | Opsional (direkomendasikan) | *(kosong)* | API key dari [resend.com](https://resend.com). Setup jauh lebih simpel dari SendGrid — cukup daftar pakai email, tidak perlu verifikasi nomor telepon. Gratis 3.000 email/bulan. |
| `RESEND_FROM_EMAIL` | Opsional | `onboarding@resend.dev` | Alamat pengirim. Default ini bisa langsung dipakai untuk testing tanpa setup domain sendiri dulu. |
| `SENDGRID_API_KEY` | Opsional (alternatif) | *(kosong)* | API key dari [SendGrid](https://sendgrid.com), kalau lebih memilih provider ini. |
| `SENDGRID_FROM_EMAIL` | Opsional | `alerts@netmonitor.local` | Alamat pengirim SendGrid, harus sudah diverifikasi dulu. |
| `TELEGRAM_BOT_TOKEN` | Wajib untuk fitur Telegram | *(kosong)* | Token bot dari [@BotFather](https://t.me/BotFather). Tanpa ini, `run_telegram_bot.py` tidak bisa start dan push alert Telegram di-skip (dry-run, dicatat ke log, tidak error). |
| `DATABASE_URL` | Opsional (wajib kalau pakai PostgreSQL) | SQLite lokal | Lihat bagian [Hosting/Deploy Production](#hostingdeploy-production) di bawah. |

Kalau tidak ada satupun API key di atas yang di-set, fitur email jalan
dalam **dry-run mode** (tidak error, tapi email tidak benar-benar
terkirim — cuma dicatat ke log). Kalau **keduanya** di-set, Resend yang
dipakai (prioritas lebih tinggi).

### Cara Test Integrasi Email
Setelah salah satu API key di atas di-set dan server dijalankan ulang, login lalu panggil:
```bash
curl -X POST http://127.0.0.1:8000/api/test-email -H "Authorization: Bearer <token_kamu>"
```
Kalau berhasil, responsnya `{"sent": true, "dry_run": false, ...}` dan email percobaan akan masuk ke inbox akun yang sedang login.

### Cara Mengatur Alert per Target
Setiap target bisa punya 1 aturan alert. Kalau belum diatur, target tidak
akan mengirim email apapun (default aman, tidak ada notifikasi kejutan).
```bash
curl -X PUT http://127.0.0.1:8000/api/targets/<target_id>/alert-rule \
  -H "Authorization: Bearer <token_kamu>" \
  -H "Content-Type: application/json" \
  -d '{
    "is_enabled": true,
    "failure_threshold": 2,
    "cooldown_minutes": 30,
    "notify_email": "kamu@contoh.com"
  }'
```
- `failure_threshold`: kirim alert setelah gagal berturut-turut sebanyak ini
- `cooldown_minutes`: jeda minimum antar alert untuk target yang sama
- `notify_email`: kosongkan untuk pakai email akun yang sedang login
- Email recovery ("sudah ONLINE lagi") otomatis terkirim sekali begitu
  target pulih setelah sempat alert down

### Bot Telegram

1. Bikin bot lewat [@BotFather](https://t.me/BotFather) di Telegram
   (`/newbot`), dapat token.
2. Tambah `TELEGRAM_BOT_TOKEN=<token_kamu>` ke file `.env`.
3. Jalankan bot di terminal terpisah (proses sendiri, bukan bagian dari
   `uvicorn`):
   ```bash
   python run_telegram_bot.py
   ```
4. Generate token link dari dashboard/API (berlaku 10 menit):
   ```bash
   curl -X POST http://127.0.0.1:8000/api/telegram/link-token \
     -H "Authorization: Bearer <token_kamu>"
   ```
5. Kirim `/link <token_dari_langkah_4>` ke bot di Telegram (dalam 10
   menit sebelum token expired).
6. Pakai `/status` untuk ringkasan semua target, `/forecast <nama_target>`
   untuk lihat prediksi 24 jam ke depan.
7. Alert down/recovery otomatis ikut terkirim ke Telegram (selain email)
   begitu akun sudah ter-link — tidak perlu setting tambahan lain.

Untuk supaya bot otomatis jalan terus tanpa terminal terbuka manual,
pakai Task Scheduler Windows (trigger "At log on" atau "At startup",
action jalankan `run_telegram_bot.py`) atau tool seperti
[NSSM](https://nssm.cc/) untuk menjadikannya Windows Service.

### Perintah Bot Telegram

| Perintah | Fungsi |
|---|---|
| `/link KODE` | Hubungkan chat ke akun web (kode dibuat lewat `POST /api/telegram/link-token`, berlaku 10 menit; tanda `<` `>` hasil salin ikut dimaafkan) |
| `/status` | Ringkasan semua target (ONLINE/OFFLINE + waktu respons) |
| `/forecast NAMA_TARGET` | Prediksi waktu respons 24 jam ke depan (nama tidak sensitif huruf besar/kecil) |
| `/laporan` | Kirim laporan bulan lalu sekarang (ringkasan evaluasi AI + grafik + Excel); `/laporan berjalan` = bulan ini sampai sekarang |
| `/unlink` | Putuskan chat dari akun |
| `/help` | Daftar perintah |

Dashboard/API pendukung: `GET /api/telegram/status`, `DELETE /api/telegram/link`,
`POST /api/reports/monthly/send?period=previous|current`.

### Laporan Bulanan Otomatis

Setiap jam scheduler memeriksa apakah laporan **bulan lalu** sudah terkirim ke tiap
pengguna yang terhubung ke Telegram. Mulai **tanggal 1 pukul 08.00 WIB** laporan
dikirim: ringkasan evaluasi, grafik PNG (ketersediaan per target, respons harian,
uptime harian, prediksi 7 hari bila model tersedia), dan berkas Excel
(sheet Ringkasan, Harian, Insiden).

- **Idempoten**: dicatat di tabel `monthly_report_log` (unik user + periode), jadi
  restart atau job ganda tidak mengirim dua kali.
- **Catch-up**: bila server mati pada tanggal 1, laporan dikirim begitu server
  hidup lagi. Pengiriman yang gagal dicoba lagi tiap jam.
- **Analis AI dua lapis**: (1) analis lokal berbasis aturan, selalu aktif dan tanpa
  internet; (2) narasi Claude **opsional**, nonaktif secara bawaan, hanya menerima
  angka agregat dengan nama/URL target diganti kode (`Target-A`, ...). Aktifkan hanya
  bila data boleh keluar dari server: set `REPORT_AI_ENABLED=true` dan `ANTHROPIC_API_KEY`.

| Variable | Default | Fungsi |
|---|---|---|
| `REPORT_TIMEZONE` | `Asia/Jakarta` | Zona waktu batas bulan dan jam kirim |
| `REPORT_SEND_HOUR` | `8` | Jam kirim pada tanggal 1 (0-23) |
| `REPORT_AI_ENABLED` | *(mati)* | `true` mengaktifkan narasi Claude |
| `ANTHROPIC_API_KEY` | *(kosong)* | Kunci API untuk narasi Claude (opsional) |
| `REPORT_AI_MODEL` | `claude-sonnet-5` | Model untuk narasi |

### Perawatan Basis Data

Otomatis dari scheduler (job harian 03.00 + susulan bila backup terakhir > 26 jam):

- **Backup harian** ke folder `backups/` memakai API online-backup SQLite (aman saat DB
  dipakai, ditulis ke file sementara lalu di-rename). Disimpan `BACKUP_KEEP` terakhir (default 14).
  Salin folder ini juga ke disk/penyimpanan lain.
- **Retensi**: `check_results` lebih tua dari `RETENTION_DAYS` (default 400) dihapus bertahap.
- **Cek integritas** mingguan (Minggu).
- Indeks komposit `(target_id, checked_at)` pada `check_results` untuk semua kueri riwayat/laporan.

Manual: `python -m app.db_maintenance backup | prune | check`.

Perkiraan pertumbuhan: ~460 byte/baris. 1 target @60 detik = ~525 ribu baris/tahun (~240 MB);
20 target = ~4,8 GB/tahun. Pindah ke PostgreSQL bila target > ~20, interval < 15 detik, atau DB > 1 GB.

Catatan data: model prediksi hanya dilatih dari **segmen data kontigu** (celah > 6 jam memutus
data, minimal 48 jam). Pemantauan yang hanya jalan saat komputer menyala tidak akan menghasilkan
model; jalankan sistem di server yang menyala terus.

## Hosting/Deploy Production

Development lokal (langkah di atas) cukup untuk dicoba sendiri. Untuk
dipakai beneran (bisa diakses dari luar, jalan terus 24/7), berikut
kebutuhan dan langkah tambahannya.

### Kebutuhan Software & Spesifikasi Minimum

| Kebutuhan | Spesifikasi Minimum | Keterangan |
|---|---|---|
| **VPS/Server** | 1 vCPU, 1-2 GB RAM, 20 GB disk | Cukup untuk skala kecil-menengah (puluhan target, interval detik-menitan). Provider apa saja (DigitalOcean, Vultr, Hetzner, AWS Lightsail, dst) selama bisa install Python. |
| **OS** | Linux (Ubuntu 22.04/24.04 LTS direkomendasikan) | Windows Server juga bisa, tapi tooling proses-manager (systemd) lebih umum di Linux. |
| **Python** | 3.11+ | Sesuai yang dipakai development. |
| **Reverse proxy** | Nginx atau Caddy | uvicorn tidak didesain menghadap internet langsung — reverse proxy urus HTTPS (SSL/TLS), lebih efisien untuk file statis (`static/index.html`), dan bisa jadi rate-limit tambahan di layer jaringan. |
| **Sertifikat HTTPS** | Let's Encrypt (gratis, lewat Certbot) | Wajib kalau domain publik — WebSocket dan login token JWT sebaiknya tidak lewat HTTP polos. |
| **Process manager** | `systemd` (Linux) | Supaya `uvicorn` (web) dan `run_telegram_bot.py` (bot) otomatis restart kalau crash, dan otomatis start saat server reboot. Alternatif: `supervisor` atau (kalau tetap di Windows) **NSSM**/Task Scheduler. |
| **Database** | SQLite cukup untuk skala kecil; **PostgreSQL** direkomendasikan untuk banyak user/target bersamaan | Set `DATABASE_URL` (lihat `app/database.py`) — sudah otomatis konversi skema `postgres://` jadi `postgresql+asyncpg://`. Idealnya PostgreSQL + ekstensi **TimescaleDB** karena data ini time-series. |
| **Domain** | Opsional tapi direkomendasikan | Supaya bisa pasang HTTPS dan diakses dengan nama yang mudah diingat, bukan IP. |

### Contoh Setup systemd (Linux)

Buat 2 service file terpisah — satu untuk web, satu untuk bot, supaya
keduanya independen (bot crash tidak menjatuhkan web, dan sebaliknya):

`/etc/systemd/system/netmonitor-web.service`:
```ini
[Unit]
Description=Netmonitor Web Server
After=network.target

[Service]
User=netmonitor
WorkingDirectory=/opt/netmonitor
Environment="PATH=/opt/netmonitor/venv/bin"
ExecStart=/opt/netmonitor/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/netmonitor-bot.service`:
```ini
[Unit]
Description=Netmonitor Telegram Bot
After=network.target

[Service]
User=netmonitor
WorkingDirectory=/opt/netmonitor
Environment="PATH=/opt/netmonitor/venv/bin"
ExecStart=/opt/netmonitor/venv/bin/python run_telegram_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Aktifkan keduanya:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now netmonitor-web netmonitor-bot
```

Nginx tinggal reverse-proxy ke `127.0.0.1:8000` (web server tidak perlu
dengar di port publik langsung), termasuk mem-forward header WebSocket
(`Upgrade`/`Connection`) supaya dashboard realtime tetap jalan di balik
proxy.

### Checklist Sebelum Go-Live

- [ ] `NETMONITOR_SECRET_KEY` di-set ke nilai acak (bukan default dev)
- [ ] `TELEGRAM_BOT_TOKEN` di-set kalau fitur bot dipakai
- [ ] Email provider (`RESEND_API_KEY` atau `SENDGRID_API_KEY`) di-set,
      bukan dry-run
- [ ] `DATABASE_URL` mengarah ke PostgreSQL kalau ekspektasi banyak user
- [ ] HTTPS aktif (Let's Encrypt), bukan HTTP polos
- [ ] `alembic upgrade head` sudah dijalankan di server production
- [ ] Backup database terjadwal (`pg_dump` untuk PostgreSQL, atau copy
      file `.db` untuk SQLite) — data monitoring adalah data historis
      yang tidak bisa dibuat ulang kalau hilang
- [ ] Training model (`train_forecast.py`) dijadwalkan berkala (mis.
      cron mingguan) kalau fitur forecast AI dipakai serius

## Rencana Tahap Berikutnya
Tahap ini sengaja difokuskan ke fondasi yang solid: input → cek → simpan →
tampilkan realtime. Setelah kamu coba dan konfirmasi ini jalan baik di
sisi kamu, kita lanjutkan ke tahap berikutnya, misalnya:

- **Tahap 2**: Alert/notifikasi (email/webhook) saat target down — selesai
- **Tahap 3**: Analisis lebih dalam — histori tren, laporan mingguan/bulanan — selesai
- **Tahap 4**: Packet-level monitoring (Scapy) untuk traffic di level jaringan
  jika servernya kamu kontrol penuh (butuh privilege root) — belum dikerjakan
- **Tahap 5**: Export Excel + training model AI forecasting offline + bot
  Telegram (`/link`, `/status`, `/forecast`, push alert) — selesai, lihat
  `docs/superpowers/specs/2026-09-17-excel-ai-forecast-telegram-design.md`

Silakan dicoba dulu — beri tahu saya kalau ada yang error atau ingin
disesuaikan sebelum kita lanjut ke tahap berikutnya.
