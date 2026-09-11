# Timeline Pengembangan — Network Flow Monitor (dengan AI/ML)

Rencana kerja harian dari **28 Agustus 2026** sampai **30 September 2026**.
Bulan **Oktober 2026 dikosongkan khusus untuk maintenance** (perbaikan bug,
monitoring stabilitas sistem yang sudah live, tidak ada fitur baru).

Versi ini menambahkan **Fase 4: AI/ML Ringan** (anomaly detection, prediksi
downtime, chatbot berbasis data) yang disisipkan sebelum deployment.
Fase History/Report dan Deployment sedikit dipadatkan supaya total tetap
muat sampai 30 September tanpa mengorbankan waktu maintenance di Oktober.

Format: setiap hari kerja punya target jelas. Akhir pekan (Sabtu-Minggu)
dikosongkan sebagai buffer — boleh dipakai mengejar kalau ada yang molor.

---

## FASE 1 — Fondasi ✅ (Selesai, 27 Agustus 2026)

- [x] Struktur proyek FastAPI + database + scheduler
- [x] Fitur input URL, pengecekan berkala, dashboard realtime via WebSocket
- [x] Sudah diuji jalan end-to-end (GitHub UP, domain palsu DOWN terdeteksi benar)
- [x] Dokumentasi awal (README.md, TOOLS.md)

---

## FASE 2 — Alert, Autentikasi & Keandalan
*(28 Agustus – 7 September 2026, 7 hari kerja)*

| Tanggal | Hari | Tugas |
|---|---|---|
| 28 Agu | Jumat | Setup akun tools: Heroku, Namecheap, SendGrid (aktivasi GitHub Student Pack) |
| 29–30 Agu | Sabtu–Minggu | *(buffer/istirahat)* |
| 31 Agu | Senin | Desain skema tabel `User` & `AlertRule`; buat model & migrasi database |
| 1 Sep | Selasa | Implementasi autentikasi dasar (register/login, hashing password, JWT) |
| 2 Sep | Rabu | Proteksi semua endpoint API dengan autentikasi; uji akses tanpa login harus ditolak |
| 3 Sep | Kamis | Integrasi SendGrid: kirim email test dari aplikasi berhasil terkirim |
| 4 Sep | Jumat | Logika alert (down > 2x berturut baru kirim email) + retry logic sebelum status DOWN |
| 5–6 Sep | Sabtu–Minggu | *(buffer/istirahat)* |
| 7 Sep | Senin | Testing menyeluruh Fase 2 (simulasi down, cek email masuk, cek login/logout) + update dokumentasi |

**Checkpoint**: Sistem punya login dan mengirim email otomatis saat target down.

---

## FASE 3 — Keamanan Input & Multi Check-Type
*(8 – 15 September 2026, 6 hari kerja)*

| Tanggal | Hari | Tugas |
|---|---|---|
| 8 Sep | Selasa | Validasi input URL: blokir target ke `localhost`/IP privat (cegah SSRF) |
| 9 Sep | Rabu | Rate limiting: batasi jumlah target per user & jumlah request API per menit |
| 10 Sep | Kamis | Tambah jenis pengecekan: Ping (ICMP) sebagai pelengkap HTTP check |
| 11 Sep | Jumat | Tambah jenis pengecekan: cek port TCP spesifik (mis. database di port 5432) |
| 12–13 Sep | Sabtu–Minggu | *(buffer/istirahat)* |
| 14 Sep | Senin | Tambah opsi "cek konten response" + update UI pilihan jenis check |
| 15 Sep | Selasa | Testing seluruh jenis check baru + perbaikan bug + update dokumentasi |

**Checkpoint**: Sistem aman dari input berbahaya dan mendukung 4 jenis pengecekan.

---

## FASE 4 — AI/ML Ringan 🤖 (BARU)
*(16 – 23 September 2026, 6 hari kerja)*

Semua fitur di fase ini dirancang **ringan**: memakai statistik dasar dari
data yang sudah dikumpulkan sistem (`CheckResult`), bukan model machine
learning yang butuh training berat atau GPU. Cocok untuk mulai belajar
konsep AI/ML di proyek nyata tanpa over-engineering.

| Tanggal | Hari | Tugas |
|---|---|---|
| 16 Sep | Rabu | Riset & desain: tentukan metode (z-score untuk anomaly, moving average untuk tren), rancang tabel tambahan `AnomalyLog` bila perlu |
| 17 Sep | Kamis | **Anomaly Detection**: hitung baseline (rata-rata & standar deviasi response time per target dari histori), tandai check sebagai "anomali" jika z-score melewati ambang batas (mis. > 3) |
| 18 Sep | Jumat | Integrasikan anomaly detection ke pipeline scheduler (tiap hasil check baru otomatis dibandingkan ke baseline); tampilkan badge "⚠️ Anomali" di dashboard |
| 19–20 Sep | Sabtu–Minggu | *(buffer/istirahat)* |
| 21 Sep | Senin | **Prediksi Downtime ringan**: hitung tren response time pakai regresi linear sederhana / slope moving average; munculkan peringatan "tren naik, berpotensi down" di dashboard |
| 22 Sep | Selasa | **Chatbot berbasis data**: buat endpoint `/api/ask` yang menjawab pertanyaan sederhana dari data tersimpan (rule-based + template, contoh: "server mana yang paling sering down minggu ini?"). Opsional: tambahkan lapisan LLM (Claude/OpenAI API) di atas data terstruktur ini supaya jawabannya berbahasa natural |
| 23 Sep | Rabu | Testing AI/ML: simulasikan lonjakan response time buatan untuk cek akurasi anomaly detection, uji chatbot dengan beberapa pertanyaan, perbaikan bug, dokumentasi fitur AI |

**Checkpoint**: Dashboard bisa menandai anomali otomatis, memberi peringatan dini tren memburuk, dan menjawab pertanyaan dasar soal status sistem.

---

## FASE 5 — History & Laporan (dipadatkan)
*(24 – 25 September 2026, 2 hari kerja)*

| Tanggal | Hari | Tugas |
|---|---|---|
| 24 Sep | Kamis | Agregasi data historis (rata-rata per jam) + grafik riwayat panjang (harian/mingguan) di dashboard |
| 25 Sep | Jumat | Fitur export riwayat ke CSV + testing performa dengan banyak target sekaligus |

**Checkpoint**: Sistem punya laporan historis yang tetap cepat walau data sudah banyak.

---

## FASE 6 — Deployment ke Production
*(26 – 30 September 2026, 3 hari kerja efektif)*

| Tanggal | Hari | Tugas |
|---|---|---|
| 26–27 Sep | Sabtu–Minggu | *(buffer/istirahat)* |
| 28 Sep | Senin | Siapkan konfigurasi deploy untuk Heroku (`Procfile` sudah dibuat); migrasi database dari SQLite ke PostgreSQL (tambah addon Heroku Postgres, `DATABASE_URL` sudah didukung otomatis di `app/database.py`) |
| 29 Sep | Selasa | Deploy ke Heroku (connect repo GitHub, auto-deploy dari branch `main`); hubungkan domain dari Namecheap; HTTPS otomatis dari Heroku |
| 30 Sep | Rabu | Setup Sentry (error tracking) & GitHub Actions (auto-deploy); testing akhir di production; finalisasi dokumentasi |

**Checkpoint (akhir September)**: Aplikasi live di domain sendiri, aman (HTTPS), sudah punya fitur AI/ML ringan, dan bisa dipantau errornya otomatis.

---

## OKTOBER 2026 — Bulan Maintenance 🛠️
*(Sengaja dikosongkan dari fitur baru)*

Fokus murni menjaga stabilitas sistem yang sudah live:

- Memantau Sentry untuk error yang muncul di production
- Memperbaiki bug yang ditemukan dari pemakaian nyata
- **Evaluasi akurasi fitur AI/ML**: cek apakah threshold anomaly detection
  (z-score) terlalu sensitif/kurang sensitif berdasarkan data nyata,
  sesuaikan ambang batas bila perlu (ini termasuk maintenance, bukan fitur baru)
- Memantau biaya/credit Heroku & Azure agar tidak kehabisan tanpa sadar
- Review keamanan berkala (update dependency Python yang punya celah keamanan)
- Backup database secara berkala
- Mengumpulkan feedback pengguna untuk bahan perencanaan fitur November

---

## Ringkasan Pendekatan AI/ML (Kenapa "Ringan" Sudah Cukup)

| Fitur | Metode yang Dipakai | Kenapa Tidak Perlu yang Lebih Berat |
|---|---|---|
| Anomaly Detection | Z-score dari mean & std dev response time | Data monitoring umumnya cukup direpresentasikan sebagai distribusi normal sederhana; model kompleks (Isolation Forest, dll) baru relevan kalau pola datanya multi-dimensi dan rumit |
| Prediksi Downtime | Regresi linear / slope moving average | Tren jangka pendek (naik/turun) sudah cukup ditangkap tanpa perlu model time-series berat (ARIMA, LSTM) yang butuh data historis jauh lebih banyak |
| Chatbot | Rule-based + query ke database, opsional lapisan LLM API | Pertanyaan seputar status server sifatnya terstruktur (butuh angka pasti dari database), bukan open-ended reasoning — LLM mahal justru kurang cocok jika dipakai sendirian tanpa akses data nyata |

Semua pendekatan ini bisa di-upgrade nanti (ke model yang lebih canggih)
begitu data historis sudah cukup banyak terkumpul dari pemakaian nyata —
tapi untuk tahap ini, kompleksitas tambahan belum diperlukan.

---

## Catatan Penggunaan Timeline Ini

- Timeline ini **fleksibel** — kalau satu hari tugasnya belum selesai, geser
  ke akhir pekan (buffer) atau ke hari berikutnya.
- Setiap akhir fase ada **checkpoint** — jangan lanjut ke fase berikutnya
  kalau checkpoint sebelumnya belum benar-benar teruji (ingat pengalaman
  Tahap 1: bug scheduler baru ketahuan setelah dites langsung, bukan cuma
  dibaca kodenya).
- Kalau progres lebih cepat dari rencana, waktu ekstra bisa dipakai untuk
  memperdalam fitur AI (misal ganti z-score dengan Isolation Forest dari
  scikit-learn) atau mulai eksplorasi packet-level monitoring dengan Scapy.
