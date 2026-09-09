# Panduan Testing — Fase 4 (AI/ML Ringan)

Checklist untuk menguji 3 fitur AI/ML: **Anomaly Detection**, **Prediksi
Tren**, dan **Chatbot**. Beda dari testing Fase 1-3, fitur-fitur ini
butuh **data historis** dulu supaya bisa diuji dengan bermakna — jadi ada
sedikit persiapan sebelum mulai.

---

## Persiapan: Kumpulkan Data Dulu

Semua fitur AI/ML di sini butuh minimal **8-10 hasil check** per target
sebelum mulai bekerja (di bawah itu, sistem sengaja belum menilai apa-apa
karena data belum representatif). Ada 2 cara dapat data ini:

### Cara A: Tunggu Natural (Paling Simpel, Paling Lama)
Biarkan target jalan dengan interval pendek (5-10 detik) selama beberapa
menit. Setelah ~10 kali check terkumpul, fitur AI/ML mulai aktif.

### Cara B: Percepat Pakai Swagger UI (Untuk Testing Cepat)
Buka `http://127.0.0.1:8000/docs` di browser — ini panel interaktif
bawaan FastAPI, semua endpoint bisa dicoba dari sini tanpa command line.
Cocok dipakai untuk cek response API mentah (termasuk field `trend`,
`is_anomaly` yang tidak selalu kelihatan jelas di tampilan dashboard).

---

## 1. Anomaly Detection

**Konsep**: sistem belajar pola "normal" response time tiap target dari
riwayatnya sendiri (rata-rata & variasi), lalu menandai kalau ada hasil
yang jauh lebih lambat dari kebiasaan itu (bukan angka tetap seperti
"lebih dari 1000ms = anomali" — tiap target punya standar sendiri).

| # | Langkah | Cara Melakukan | Hasil yang Diharapkan |
|---|---|---|---|
| 1.1 | Tambah target baru, biarkan jalan normal sampai ±10 kali check | Tunggu saja, tidak perlu aksi apa-apa | Belum ada badge "⚠ ANOMALI" muncul (response time-nya konsisten, wajar) |
| 1.2 | Simulasikan lonjakan: matikan sebentar internet/WiFi kamu 5-10 detik lalu nyalakan lagi, tepat sebelum 1 siklus check jalan | Perhatikan timing di kartu (interval berapa detik), matikan WiFi pas mendekati waktu itu | Response time pada check itu akan jauh lebih tinggi dari biasanya |
| 1.3 | Cek dashboard setelah lonjakan itu | Refresh halaman | Badge "⚠ ANOMALI" (kuning) muncul di sebelah nama target |
| 1.4 | Hover ke badge anomali | Arahkan mouse ke badge kuning | Muncul tooltip: "Response time jauh lebih lambat dari kebiasaan target ini" |
| 1.5 | Cek lewat API mentah | Buka `/docs`, coba endpoint `GET /api/targets/{id}/checks`, expand hasil check yang barusan anomali | Field `is_anomaly: true` dan `anomaly_z_score` berisi angka positif tinggi (biasanya di atas 3) |
| 1.6 | Cek check-check normal lainnya | Lihat check lain di response yang sama | `is_anomaly: false`, tapi `anomaly_z_score` tetap terisi angka kecil (bukan `null`) — ini pembuktian z-score dihitung terus, bukan cuma pas anomali |

**Catatan penting**: kalau kamu baru mengganti/menambah target, anomaly
detection **belum aktif** sampai ada cukup riwayat (minimal 10 data poin
sukses). Ini normal, bukan bug.

---

## 2. Prediksi Tren

**Konsep**: sistem menarik garis tren dari beberapa check terakhir (regresi
linear) untuk lihat arah performa — makin lambat, stabil, atau membaik.
Beda dari anomaly detection yang fokus ke 1 titik ekstrem, ini fokus ke
**arah pergerakan** sepanjang waktu.

| # | Langkah | Cara Melakukan | Hasil yang Diharapkan |
|---|---|---|---|
| 2.1 | Tambah target ke server yang performanya stabil (mis. github.com) | Tunggu ±10-15 kali check | — |
| 2.2 | Cek field `trend` lewat API | Buka `/docs` → `GET /api/summary` → Execute | `trend.direction` kemungkinan besar `"stabil_atau_membaik"`, `warning: false` |
| 2.3 | Simulasikan tren memburuk (skenario nyata seperti "Unhan RI" di screenshot kamu kemarin) | Tambah target ke server yang memang lambat/tidak stabil, biarkan beberapa menit | Kalau response time-nya konsisten naik dari check ke check |
| 2.4 | Cek field `trend` lagi | `GET /api/summary` lewat `/docs` | `trend.direction: "memburuk"`, `warning: true`, dan `slope_ms_per_check` bernilai positif |
| 2.5 | Bandingkan dengan target yang stabil | Lihat target lain di response yang sama | `trend.direction` targetnya beda-beda sesuai kondisi masing-masing (tidak semua ikut-ikutan warning) |

**Cara baca `slope_ms_per_check`**: ini artinya "rata-rata kenaikan
response time per 1x check". Kalau nilainya `26.5`, artinya setiap check
berikutnya diperkirakan ~26.5ms lebih lambat dari sebelumnya — kalau
dibiarkan 15 check lagi, itu bisa jadi ~400ms lebih lambat dari sekarang.

**Catatan**: field `trend` ini **belum ditampilkan di dashboard visual**
(baru ada di response API) — kalau kamu mau, saya bisa tambahkan
indikator tren (mis. panah ↗️ naik / ↘️ turun) di kartu dashboard juga.

---

## 3. Chatbot

**Konsep**: rule-based, bukan LLM — chatbot mencocokkan kata kunci di
pertanyaan kamu ke salah satu pola yang dikenali, lalu jawabannya diambil
langsung dari query database (bukan karangan/tebakan).

| # | Pertanyaan yang Dicoba | Cara Melakukan | Hasil yang Diharapkan |
|---|---|---|---|
| 3.1 | "server mana yang paling sering down?" | Ketik di kotak chat, klik Tanya | Nama target dengan jumlah gagal terbanyak dalam 7 hari, atau "tidak ada yang down" kalau semua sehat |
| 3.2 | "berapa rata-rata uptime?" | — | Persentase uptime gabungan semua target, 24 jam terakhir |
| 3.3 | "ada berapa target?" | — | Jumlah total + berapa yang aktif |
| 3.4 | "server mana yang paling lambat?" | — | Nama target dengan rata-rata response time tertinggi |
| 3.5 | "server mana yang paling cepat?" | — | Kebalikan dari 3.4 |
| 3.6 | "target mana yang sedang down?" | — | List nama target yang check terakhirnya gagal, atau "semua ONLINE" |
| 3.7 | "ada anomali tidak?" | — | List target yang kena flag anomali 24 jam terakhir |
| 3.8 | Ketik pertanyaan ngasal, mis. "siapa presiden indonesia" | — | Chatbot **tidak** mengarang jawaban — dia jujur bilang tidak paham, dan kasih daftar contoh pertanyaan yang bisa dijawab |
| 3.9 | Uji isolasi: buat akun baru (belum ada target), tanya "ada berapa target?" | Logout, daftar akun baru, tanya hal sama | Jawaban: "Kamu belum menambahkan target apapun" — **bukan** ikut menghitung target akun lain |

### Kenapa Chatbot Kadang Jawab "Tidak paham"?
Chatbot ini **rule-based**, bukan AI generatif — dia cuma mengenali
pola kalimat tertentu (lihat daftar di 3.1-3.7). Kalau pertanyaan kamu
di luar pola itu (misal ditanya pakai kalimat yang beda struktur, atau
soal topik di luar monitoring), dia akan jujur bilang tidak paham
daripada mengarang jawaban asal. Ini keputusan desain yang disengaja —
supaya jawabannya selalu bisa dipercaya berasal dari data asli.

---

## Ringkasan Cara Uji Cepat (Kalau Buru-Buru)

1. **Anomaly**: tunggu data terkumpul → matikan WiFi sebentar → refresh → cek badge kuning muncul
2. **Prediksi**: buka `/docs` → `GET /api/summary` → lihat field `trend` tiap target
3. **Chatbot**: ketik satu-satu pertanyaan dari tabel di atas ke kotak chat, bandingkan jawabannya dengan kondisi target yang kamu lihat di kartu dashboard — harus konsisten (misal kalau chatbot bilang "X paling lambat", cek juga di kartu apakah X memang latency-nya paling tinggi)

## Kalau Ada yang Tidak Sesuai

Catat: pertanyaan/langkah yang gagal, jawaban yang muncul vs yang
diharapkan, dan (kalau bisa) screenshot response dari `/docs`. Kirim ke
saya untuk didiagnosa.
