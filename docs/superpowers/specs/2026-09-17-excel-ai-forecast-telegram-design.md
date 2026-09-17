# Design: Excel Export + AI Forecast Training + Bot Telegram

Tanggal: 2026-09-17

## Tujuan

Perluas Netmonitor (FastAPI + SQLite) yang sudah ada supaya:
1. Data histori check (`CheckResult`) bisa diexport ke file Excel.
2. Excel itu dipakai sebagai dataset untuk melatih model AI forecasting
   traffic/response-time secara offline (batch, terjadwal).
3. Hasil forecast bisa diakses lewat bot Telegram, baik push alert
   otomatis maupun query on-demand oleh user.

## Konteks & Kondisi Awal

Modul existing yang relevan:
- `app/models.py` — `User`, `MonitorTarget`, `CheckResult`, `AlertRule`.
- `app/prediction.py` — regresi linear sederhana (numpy polyfit) untuk
  deteksi tren jangka pendek. Tetap dipertahankan sebagai fallback.
- `app/anomaly.py` — deteksi anomali via z-score.
- `app/reporting.py` — agregasi per jam + export CSV (pola yang akan
  ditiru untuk export Excel).
- `app/alerting.py` — kirim email saat down/recovery, dengan cooldown
  per `AlertRule`.
- `app/chatbot.py` — chatbot rule-based, jawab pertanyaan dari data
  tersimpan.

Belum ada: export Excel, pipeline training model ML, model serving
selain regresi linear sederhana, integrasi Telegram.

## Arsitektur

```
CheckResult (DB)
     |
     | export (manual/on-demand via endpoint)
     v
laporan.xlsx  ------------------->  training/train_forecast.py (offline)
                                            |
                                            v
                                   training/models/{target_id}.pkl
                                            |
                                            v
                              app/ml_forecast.py (load & serve)
                                            |
                                            v
                                   GET /api/targets/{id}/forecast
                                            |
                        +-------------------+-------------------+
                        |                                       |
              dashboard web (existing)              app/telegram_bot.py
                                                     - /status, /forecast
                                                     - push alert
```

Training terpisah dari runtime app (proses/script sendiri), dijalankan
manual atau terjadwal (mis. cron mingguan). Ini approach A dari 3
opsi yang dibahas — dipilih karena training time-series butuh histori
cukup panjang, tidak perlu retrain tiap ada 1 baris data baru.

## Komponen Baru

### 1. Excel Export — `app/excel_export.py`

- `export_checks_to_excel(target_id, target_name, db, days=30) -> bytes`:
  sama isi kolomnya dengan `export_checks_to_csv` di `reporting.py`,
  tapi output `.xlsx` (pakai `openpyxl` langsung — tidak nambah
  dependency `pandas` kalau belum ada di requirements).
  Kolom (sama seperti CSV existing): `target_name`, `checked_at`,
  `status_code`, `response_time_ms`, `is_up`, `error_message`,
  `is_anomaly`, `anomaly_z_score`.
- Tambahan sheet kedua: agregat per jam (reuse `get_hourly_aggregate`)
  supaya dataset training punya fitur siap pakai (bukan cuma data
  mentah). Kolom: `hour`, `avg_response_time_ms`, `total_checks`,
  `uptime_percent`.
- Endpoint baru: `GET /api/targets/{id}/export-excel` (auth required,
  filter per-user seperti endpoint lain, dipanggil dari
  `app/main.py` sebagaimana endpoint CSV existing dipanggil).
- Endpoint export-all (opsional, untuk training multi-target):
  `GET /api/export-excel-all` — multi-sheet, 1 sheet per target.
- Data kosong (belum ada check) → response 200 dengan file berisi
  header saja + pesan di sheet (bukan error, bukan file corrupt).

### 2. Training Pipeline — `training/` (folder baru, di luar `app/`)

- `training/train_forecast.py`: script CLI, dijalankan manual:
  ```bash
  python training/train_forecast.py --excel path/to/laporan.xlsx --target-id 1
  ```
  - Baca sheet data mentah, resample ke interval waktu tetap (mis.
    per-jam, isi gap dengan interpolasi/forward-fill wajar untuk
    time-series).
  - Fit model forecasting: **Holt-Winters (statsmodels
    `ExponentialSmoothing`)** sebagai default — ringan, tidak perlu
    tuning berat, cocok untuk data univariate dengan kemungkinan
    pola musiman harian. Alternatif dicatat di komentar kode: SARIMAX
    kalau butuh lebih presisi nanti.
  - Simpan model ke `training/models/{target_id}.pkl` via `joblib`.
  - Minimum data check: kalau data < N titik (mis. 48 jam), skip
    training dan print pesan jelas — tidak memaksa fit model dengan
    data terlalu sedikit (hasil akan tidak reliable).
- Tidak berjalan di dalam proses web server. Dijadwalkan lewat cron OS
  atau Task Scheduler Windows (didokumentasikan di README, bukan
  built-in scheduler baru — sudah ada APScheduler untuk checking,
  tidak dicampur dengan training job yang durasinya bisa lama).

### 3. Model Serving — `app/ml_forecast.py`

- `load_model(target_id) -> model | None`: load dari
  `training/models/{target_id}.pkl` kalau ada, cache in-memory
  (dict) supaya tidak reload file tiap request.
- `get_forecast(target_id, horizon_hours=24) -> dict`:
  - Kalau model ada: forecast N jam ke depan + confidence interval
    dari `statsmodels`.
  - Kalau model tidak ada: fallback ke `prediction.analyze_trend`
    (existing, `app/prediction.py`) dengan flag
    `"source": "fallback_linear"` di response supaya caller
    (web/bot) tahu itu bukan hasil model terlatih.
- Endpoint baru: `GET /api/targets/{id}/forecast?horizon=24`,
  ditambahkan di `app/main.py`.

### 4. Telegram Bot — `app/telegram_bot.py` + `run_telegram_bot.py`

- Library: `python-telegram-bot` (async, polling — tidak perlu
  webhook/domain publik).
- Migration baru (folder `migrations/versions/`, mengikuti pola
  revision existing): tambah kolom `telegram_chat_id: str | None` ke
  `User` (`app/models.py`), nullable, default None — tidak breaking
  data existing.
- Command:
  - `/link <token>` — hubungkan akun Telegram ke akun web. Alur:
    user generate token sekali-pakai dari dashboard web (endpoint baru
    `POST /api/telegram/link-token` di `app/main.py`), lalu kirim
    `/link <token>` ke bot → bot simpan `chat_id` ke
    `User.telegram_chat_id`. Ini menghindari perlu user ketik
    email/password ke bot (aman).
  - `/status` — ringkasan semua target milik user (uptime, status
    terakhir) — reuse logic yang sudah dipakai dashboard
    (`app/main.py` handler target list).
  - `/forecast <target_name>` — panggil `ml_forecast.get_forecast`.
- Push alert: `app/alerting.py` ditambah pemanggilan
  `telegram_bot.send_alert(chat_id, message)` di titik yang sama
  dengan pengiriman email (down/recovery), kalau
  `User.telegram_chat_id` terisi. Cooldown pakai `AlertRule` yang
  sudah ada (tidak dobel logic).
- Bot dijalankan sebagai **proses terpisah** (`run_telegram_bot.py`),
  bukan digabung ke proses `uvicorn`. Alasan: polling loop Telegram
  beda siklus hidup dari web server; kalau bot reconnect/crash tidak
  boleh ganggu availability web dashboard.
- Komunikasi bot -> DB: akses `AsyncSession` yang sama (SQLAlchemy,
  DB file yang sama, `app/database.py`) — tidak perlu API call
  internal.

## Data Flow Ringkas

1. User klik "Export Excel" di dashboard atau panggil endpoint →
   dapat file `.xlsx`.
2. User (manual) jalankan `training/train_forecast.py` dengan file
   itu → model `.pkl` tersimpan.
3. App otomatis pakai model itu untuk endpoint forecast berikutnya
   (tidak perlu restart server — lazy load).
4. User Telegram `/forecast <target>` → bot query `ml_forecast` →
   balas hasil.
5. Scheduler existing (`app/scheduler.py`) tetap jalan check rutin →
   `alerting.py` trigger email + Telegram kalau down/anomaly/forecast
   warning.

## Error Handling

- Model file tidak ada/corrupt saat load → catch exception, log
  warning, fallback ke `prediction.py` — endpoint forecast tidak
  pernah 500 karena model.
- Excel export: target tanpa data → file valid dengan pesan "belum
  ada data", bukan crash.
- Training script: data terlalu sedikit → skip dengan pesan jelas,
  tidak menyimpan model buruk yang menimpa model bagus sebelumnya.
- Telegram bot: token API invalid / network putus → proses bot retry
  dengan backoff bawaan library; karena proses terpisah, tidak
  mempengaruhi uptime web app.
- `/link` token expired/salah → bot balas pesan jelas, tidak expose
  detail internal.

## Testing

- `tests/test_excel_export.py`: cek kolom, jumlah baris sesuai jumlah
  `CheckResult`, kasus data kosong.
- `tests/test_ml_forecast.py`: load model dummy (mock/dibuat di test
  fixture), assert bentuk output forecast; assert fallback ke
  `prediction.py` kalau model tidak ada.
- `tests/test_telegram_bot.py`: mock Telegram `Update`/`Bot` object,
  test handler `/status`, `/forecast`, `/link` tanpa hit API asli.
- Training script: manual/README-documented (bukan unit test otomatis,
  karena butuh dataset nyata untuk validasi kualitas fit) — cukup
  smoke test bahwa script tidak crash dengan data dummy kecil.

## Dependency Baru

- `openpyxl` — baca/tulis Excel.
- `statsmodels` — Holt-Winters / SARIMAX forecasting.
- `joblib` — simpan/load model.
- `python-telegram-bot` — bot Telegram.

Semua ditambah ke `requirements.txt`.

## Migration Baru

- Alembic revision: tambah kolom `telegram_chat_id` (nullable) ke
  tabel `users`.

## Di Luar Scope

- WhatsApp integration (dibahas, ditolak — kompleksitas approval
  bisnis/domain publik jauh lebih tinggi dari Telegram).
- Online/incremental learning (approach B) — tidak dipilih, training
  tetap batch/terjadwal.
- Real-time packet-level netflow capture (item roadmap Tahap 4
  terpisah, tidak bagian dari spec ini).
