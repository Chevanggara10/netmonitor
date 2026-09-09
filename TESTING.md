# Panduan Testing — Fase 1, 2, 3

Checklist ini untuk memverifikasi semua fitur yang sudah dibangun sampai
Fase 3 benar-benar bekerja di komputer kamu sendiri. Kerjakan berurutan
dari atas ke bawah — beberapa test di bagian bawah butuh data dari test
sebelumnya (misal butuh sudah login dulu).

Siapkan dulu: server jalan (`python -m uvicorn app.main:app --reload`),
browser terbuka di `http://127.0.0.1:8000`, dan **buka F12 → Console**
supaya kelihatan kalau ada error JavaScript yang tidak terduga.

---

## 1. Autentikasi (Fase 2)

| # | Langkah | Hasil yang Diharapkan |
|---|---|---|
| 1.1 | Buka dashboard pertama kali | Muncul layar login, bukan dashboard |
| 1.2 | Klik "daftar di sini", isi email + password < 8 karakter | Browser menolak submit (validasi `minlength`) |
| 1.3 | Daftar dengan email baru + password valid | Langsung masuk ke dashboard, email muncul di kanan atas |
| 1.4 | Klik "Keluar", coba refresh halaman | Kembali ke layar login (sesi benar-benar berakhir) |
| 1.5 | Login lagi dengan email sama tapi password salah | Muncul pesan error, tetap di layar login |
| 1.6 | Login dengan password benar | Berhasil masuk dashboard |
| 1.7 | Coba daftar lagi pakai email yang **sama** dengan langkah 1.3 | Ditolak dengan pesan "Email sudah terdaftar" |

## 2. Isolasi Antar Akun (Fase 2)

| # | Langkah | Hasil yang Diharapkan |
|---|---|---|
| 2.1 | Login sebagai akun A, tambah 1 target | Target muncul di dashboard akun A |
| 2.2 | Keluar, daftar akun B (email baru) | Dashboard akun B kosong — **tidak** melihat target akun A |
| 2.3 | Tambah target berbeda di akun B | Hanya target akun B yang terlihat di akun B |
| 2.4 | Login ulang ke akun A | Hanya target akun A yang terlihat (bukan gabungan) |

## 3. Monitoring Dasar (Fase 1)

| # | Langkah | Hasil yang Diharapkan |
|---|---|---|
| 3.1 | Tambah target dengan URL valid (mis. `https://github.com`), interval 5-10 detik | Kartu muncul, status awal "PENDING" |
| 3.2 | Tunggu 1x interval | Badge berubah jadi "ONLINE" (hijau), latency & status code terisi |
| 3.3 | Tambah target dengan domain ngasal (mis. `https://tidak-ada-xyz123.com`) | Setelah beberapa detik (ada retry), badge jadi "OFFLINE" (merah) |
| 3.4 | Hover ke badge "OFFLINE" | Muncul tooltip pesan error |
| 3.5 | Klik "Jeda" pada target manapun | Badge tetap seperti terakhir, tapi tidak update lagi (scheduler berhenti) |
| 3.6 | Klik "Lanjut" | Monitoring aktif lagi |
| 3.7 | Klik "Hapus" | Kartu hilang, riwayat datanya ikut terhapus |
| 3.8 | Tunggu beberapa menit dengan target ONLINE terus | Grafik latency mulai terisi garis, bukan kosong |
| 3.9 | Buka 2 tab browser dengan dashboard yang sama (login akun sama) | Update status di 1 tab **otomatis** muncul juga di tab lain (WebSocket broadcast) |

## 4. Retry Logic & Alert Email (Fase 2)

| # | Langkah | Hasil yang Diharapkan |
|---|---|---|
| 4.1 | Tambah target ke domain yang pasti gagal | Butuh **beberapa detik lebih lama** dari biasanya sebelum status final muncul (karena retry 2x dengan jeda) |
| 4.2 | Cek tooltip error setelah gagal total | Pesan menyebutkan "gagal setelah 3x percobaan" |
| 4.3 | Set alert rule lewat curl (lihat README bagian "Cara Mengatur Alert"), `failure_threshold: 2` | Response sukses, dapat `id` alert rule |
| 4.4 | Biarkan target itu down selama 2x check berturut-turut | Cek `GET /api/targets/{id}/alert-rule` — `consecutive_failures` naik, `last_alert_sent_at` terisi setelah mencapai threshold |
| 4.5 | Set `SENDGRID_API_KEY` asli, ulangi 4.4 | Email sungguhan masuk ke inbox |
| 4.6 | Perbaiki target itu jadi URL valid | Email "sudah ONLINE lagi" terkirim, `consecutive_failures` reset ke 0 |

## 5. Keamanan Input — SSRF (Fase 3)

| # | Langkah | Hasil yang Diharapkan |
|---|---|---|
| 5.1 | Coba tambah target dengan URL `http://localhost:8000` | Ditolak, pesan "mengarah ke server itu sendiri" |
| 5.2 | Coba tambah target dengan URL `http://192.168.1.1` | Ditolak, pesan "alamat jaringan internal/privat" |
| 5.3 | Coba tambah target dengan URL `http://127.0.0.1` | Ditolak dengan alasan serupa |
| 5.4 | Tambah target dengan domain publik biasa | Berhasil normal (bukan false-positive) |

## 6. Rate Limiting (Fase 3)

| # | Langkah | Hasil yang Diharapkan |
|---|---|---|
| 6.1 | Coba login dengan password salah berkali-kali (>10x dalam 1 menit) | Setelah 10x, muncul error rate limit (429), bukan lagi "password salah" |
| 6.2 | Tambah target berkali-kali cepat (>20x dalam 1 menit) | Setelah 20x, permintaan ke-21 ditolak dengan 429 |
| 6.3 | Tambah target sampai 20 buah (dengan jeda supaya tidak kena rate limit di 6.2) | Target ke-21 ditolak dengan pesan "Batas maksimum 20 target per akun" (pesan beda dari rate limit) |

## 7. Multi Check-Type (Fase 3)

| # | Langkah | Hasil yang Diharapkan |
|---|---|---|
| 7.1 | Tambah target, pilih jenis "HTTP" (default) | Berfungsi seperti biasa, badge check-type "HTTP" di kartu |
| 7.2 | Tambah target, pilih "Content", isi teks yang **pasti ada** di halaman (mis. nama perusahaan di homepage-nya) | ONLINE |
| 7.3 | Tambah target Content yang sama, tapi teks yang **pasti tidak ada** | OFFLINE dengan pesan "Konten tidak ditemukan" |
| 7.4 | Tambah target, pilih "TCP Port", isi port yang pasti terbuka (mis. `443` untuk situs HTTPS) | ONLINE, latency muncul tapi status code kosong (memang TCP tidak punya status code) |
| 7.5 | Tambah target TCP dengan port yang pasti tertutup (mis. `9999`) | OFFLINE, pesan timeout/connection refused |
| 7.6 | Tambah target, pilih "Ping" | Sesuai kondisi jaringan kamu — kalau ICMP diizinkan, ONLINE dengan latency; kalau diblokir jaringan/firewall, OFFLINE dengan pesan jelas (bukan error asal) |
| 7.7 | Coba submit form dengan check-type "TCP" tapi kosongkan field port | Form tertahan (field port jadi wajib diisi saat TCP dipilih) |

## 8. Regresi (Pastikan Fitur Lama Tidak Rusak)

Setelah semua di atas lolos, ulangi cepat:
- [ ] Tambah target HTTP biasa masih berfungsi seperti Fase 1
- [ ] Grafik masih muncul normal
- [ ] WebSocket masih live-update
- [ ] Login/logout masih normal

---

## Cara Cepat Cek Lewat API (Tanpa Browser)

Kalau mau lebih cepat tanpa klak-klik UI, endpoint interaktif FastAPI bisa dipakai:
```
http://127.0.0.1:8000/docs
```
Ini halaman Swagger UI otomatis — semua endpoint bisa dicoba langsung dari
browser tanpa perlu `curl` manual, termasuk lihat skema request/response-nya.

## Kalau Ada yang Gagal

Catat: nomor test yang gagal, apa yang terjadi vs yang diharapkan, dan
screenshot kalau ada. Kirim ke saya biar langsung didiagnosa dan diperbaiki
sebelum lanjut ke Fase 4.
