# Tools & Resources — GitHub Student Developer Pack

Catatan tools dari GitHub Student Developer Pack yang relevan untuk
pengembangan **Network Flow Monitor**, beserta fungsinya masing-masing
dan di tahap mana sebaiknya dipakai.

---

## 🖥️ Hosting & Deployment

### ~~Railway~~ (DIBATALKAN — sudah tidak ada di GitHub Student Pack)
- Rencana awal memakai Railway sudah tidak berlaku: per pengecekan
  terbaru ke education.github.com/pack, **Railway tidak lagi terdaftar**
  di GitHub Student Developer Pack (mungkin dulu ada, sudah dihapus).
  Tanpa credit student, Railway berbayar (~$5/bulan minimum) tanpa tier
  gratis permanen. Diganti dengan Heroku di bawah.

### Heroku ✅ (Dipilih — Hosting Utama, pengganti Railway)
- **Fungsi**: Platform-as-a-Service (PaaS) tempat aplikasi ini di-deploy
  agar bisa diakses publik 24/7.
- **Kenapa dipilih**: Masih terdaftar aktif di GitHub Student Developer
  Pack dengan **credit $13 USD/bulan selama 24 bulan** -- cukup untuk
  menutup biaya hosting skala kecil seperti proyek ini sepenuhnya gratis
  selama periode tsb. Deploy langsung dari GitHub (`git push heroku
  main` atau auto-deploy via GitHub integration), tidak perlu konfigurasi
  server manual. Cocok untuk semua fitur di roadmap sampai Fase 6 (Auth,
  Alert, AI/ML ringan, History/Report).
- **Konfigurasi yang sudah disiapkan di repo**: `Procfile` (perintah
  `release` menjalankan migrasi Alembic otomatis tiap deploy, `web`
  menjalankan uvicorn dengan `$PORT` dari Heroku) dan `app/database.py`
  yang otomatis membaca `DATABASE_URL` dari environment (dengan konversi
  skema `postgres://` -> `postgresql+asyncpg://` untuk kompatibilitas
  driver async).
- **Catatan penting**: Heroku dyno standar juga **tidak memberi akses
  root/CAP_NET_RAW**, sama seperti Railway -- fitur **packet-level
  monitoring dengan Scapy** tetap butuh VPS terpisah (lihat Azure/Oracle
  Cloud di bawah). Tidak masalah karena fitur itu belum masuk timeline
  saat ini.
- **Dipakai di**: Fase 6 — Deployment.

### Microsoft Azure (Cadangan — Khusus Scapy)
- **Fungsi**: VPS dengan akses root penuh.
- **Kenapa relevan**: Kalau nanti proyek lanjut ke fitur packet-level
  monitoring (Scapy), Heroku tidak bisa dipakai — perlu VPS terpisah
  khusus untuk fitur itu. Azure sudah tersedia di Student Pack kamu,
  tinggal pakai tanpa perlu daftar akun baru.
- **Credit**: ~$100
- **Dipakai di**: Hanya jika/ketika fitur Scapy mulai dikerjakan (di luar
  timeline saat ini).

### Oracle Cloud Free Tier (Cadangan Jangka Panjang)
- **Fungsi**: VPS dengan akses root, gratis selamanya (bukan cuma credit).
- **Kenapa relevan**: Kalau credit Azure habis nanti, ini opsi supaya
  fitur Scapy tetap bisa jalan tanpa mendadak kena biaya.
- **Dipakai di**: Opsional, sebagai cadangan jangka panjang.

---

## 🌐 Domain

### Namecheap
- **Fungsi**: Registrasi nama domain.
- **Kenapa relevan**: Supaya dashboard bisa diakses lewat domain sendiri
  (misal `netmonitor.me`) alih-alih `IP:port`, lebih profesional untuk
  ditunjukkan di CV/portofolio.
- **Manfaat gratis**: 1 tahun domain `.me` gratis.
- **Dipakai di**: Setelah deployment ke Heroku selesai (hubungkan domain custom di pengaturan Heroku).

---

## 🔔 Notifikasi & Alert (Tahap 2)

### SendGrid
- **Fungsi**: Mengirim email notifikasi otomatis (misal saat target DOWN)
  tanpa perlu setup SMTP server sendiri.
- **Kenapa relevan**: Fitur alert adalah prioritas tinggi yang sudah
  didiskusikan — ini cara paling mudah mengirim email transaksional dari
  aplikasi Python (FastAPI).
- **Manfaat gratis**: ± 15.000 email/bulan (tergantung tier saat pendaftaran).
- **Dipakai di**: Tahap 2 — Alert/Notifikasi.

### Twilio
- **Fungsi**: Mengirim SMS atau WhatsApp saat ada alert kritis (server down).
- **Kenapa relevan**: Email bisa terlambat dibaca; SMS/WhatsApp lebih cepat
  sampai untuk kondisi darurat (misal server production down tengah malam).
- **Dipakai di**: Tahap 2 — Alert/Notifikasi (opsional, pelengkap SendGrid).

---

## 🔐 Keamanan & Autentikasi (Tahap 2)

### 1Password / Bitwarden
- **Fungsi**: Menyimpan API key, secret, dan credential proyek dengan aman.
- **Kenapa relevan**: Supaya API key SendGrid/Twilio/database tidak
  hardcode langsung di kode atau ter-commit ke Git secara tidak sengaja.
- **Dipakai di**: Sepanjang pengembangan, terutama sebelum deployment.

### Auth0
- **Fungsi**: Layanan autentikasi siap pakai (login, register, session).
- **Kenapa relevan**: Alternatif jika tidak ingin membangun sistem login
  manual (hashing password, JWT, dsb) dari nol untuk dashboard.
- **Dipakai di**: Tahap 2 — Autentikasi (opsional, bisa juga bikin manual
  dengan `passlib` + `python-jose` kalau ingin belajar dari dasar).

---

## 📊 Observability (Kualitas Aplikasi)

### Sentry
- **Fungsi**: Error tracking otomatis untuk aplikasi FastAPI ini sendiri
  (bukan target yang dimonitor, tapi aplikasi monitor-nya).
- **Kenapa relevan**: Kalau ada bug di production (misal scheduler crash,
  database error), langsung dapat notifikasi lengkap dengan stack trace,
  tanpa harus menunggu user lapor.
- **Dipakai di**: Setelah aplikasi live di production (post-deployment).

### Datadog
- **Fungsi**: Platform observability/monitoring tingkat enterprise.
- **Kenapa relevan**: Bisa jadi referensi desain dashboard & metrik untuk
  proyek ini, meskipun kemungkinan besar tidak akan dipakai langsung
  (fungsinya mirip dengan yang sedang kamu bangun sendiri).
- **Dipakai di**: Referensi belajar saja, opsional.

---

## 🛠️ Development Tools

### GitHub Copilot
- **Fungsi**: AI code completion di dalam editor.
- **Kenapa relevan**: Mempercepat penulisan kode boilerplate (misal
  endpoint CRUD tambahan, schema Pydantic baru). Tetap perlu ditest manual
  seperti proses yang sudah dilakukan untuk Tahap 1.
- **Dipakai di**: Sepanjang pengembangan.

### JetBrains PyCharm Professional
- **Fungsi**: IDE Python lengkap dengan debugger visual dan database tool
  built-in.
- **Kenapa relevan**: Proyek ini punya banyak file yang saling terhubung
  (`models.py`, `schemas.py`, `scheduler.py`, dst) — debugger visual
  membantu trace alur data. Database tool built-in juga bisa langsung
  lihat isi `netmonitor.db` tanpa command line.
- **Dipakai di**: Sepanjang pengembangan, terutama saat debugging.

---

## 📦 CI/CD

### GitHub Actions
- **Fungsi**: Otomatisasi testing dan deployment setiap kali push kode ke GitHub.
- **Kenapa relevan**: Sudah gratis untuk semua orang (bukan eksklusif student
  pack), tapi penting disebut karena akan dipakai untuk:
  - Menjalankan test otomatis sebelum merge
  - Auto-deploy ke Heroku setiap ada perubahan di branch `main`
- **Dipakai di**: Setelah proyek stabil dan siap untuk alur deployment otomatis.

---

## Rekomendasi Kombinasi untuk Proyek Ini

Urutan penggunaan yang disarankan, mengikuti roadmap tahap pengembangan:

| Tahap | Tools yang Dipakai |
|---|---|
| Tahap 1 (selesai) | — (masih lokal, belum butuh tools eksternal) |
| Deployment awal | Heroku, Namecheap |
| Tahap 2: Alert & Auth | SendGrid, (opsional: Twilio, Auth0), 1Password |
| Post-deployment | Sentry (error tracking) |
| Otomatisasi | GitHub Actions |
| Tahap packet-level (opsional, nanti) | Azure atau Oracle Cloud (perlu akses root untuk Scapy) |
| Sepanjang proses | GitHub Copilot, PyCharm Professional |

---

## Catatan
- Semua akses ke tools ini didapat lewat [education.github.com/pack](https://education.github.com/pack)
  menggunakan email/status pelajar yang terverifikasi.
- Credit cloud (Azure) biasanya punya masa berlaku — cek tanggal
  kedaluwarsa masing-masing sebelum merencanakan deployment jangka panjang.
- Tidak semua tools di atas perlu dipakai sekaligus — pilih sesuai tahap
  yang sedang dikerjakan supaya tidak over-engineering di awal.
