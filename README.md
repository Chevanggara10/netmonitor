# Network Flow Monitor — Tahap 1 (MVP)

Aplikasi web untuk memantau server/website: kamu input URL, sistem otomatis
mengecek secara berkala dan menampilkan status (UP/DOWN), response time,
status code, dan grafik realtime — semua lewat dashboard di browser.

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

## Struktur Proyek
```
netmonitor/
├── app/
│   ├── main.py         # Entry point FastAPI: semua route REST + WebSocket
│   ├── database.py     # Koneksi & setup database (SQLite)
│   ├── models.py       # Skema tabel: User, MonitorTarget, CheckResult, AlertRule
│   ├── schemas.py       # Validasi data request/response (Pydantic)
│   ├── checker.py       # Logika inti: melakukan 1x pengecekan HTTP
│   ├── scheduler.py     # Menjadwalkan pengecekan berkala per-target
│   └── ws_manager.py    # Kelola koneksi WebSocket untuk broadcast realtime
├── migrations/          # Migrasi database (Alembic)
│   ├── env.py
│   └── versions/        # Riwayat perubahan skema, urut kronologis
├── static/
│   └── index.html       # Dashboard (frontend) — HTML+JS, tanpa build step
├── alembic.ini
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

## Rencana Tahap Berikutnya
Tahap ini sengaja difokuskan ke fondasi yang solid: input → cek → simpan →
tampilkan realtime. Setelah kamu coba dan konfirmasi ini jalan baik di
sisi kamu, kita lanjutkan ke tahap berikutnya, misalnya:

- **Tahap 2**: Alert/notifikasi (email/webhook) saat target down
- **Tahap 3**: Analisis lebih dalam — histori tren, laporan mingguan/bulanan
- **Tahap 4**: Packet-level monitoring (Scapy) untuk traffic di level jaringan
  jika servernya kamu kontrol penuh (butuh privilege root)
- **Tahap 5**: Autentikasi multi-user, supaya tiap tim bisa punya daftar
  target sendiri

Silakan dicoba dulu — beri tahu saya kalau ada yang error atau ingin
disesuaikan sebelum kita lanjut ke tahap berikutnya.
