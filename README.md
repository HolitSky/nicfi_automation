<div align="center">
  <img src="asset/logo/logo-kemenhut-new.png" alt="Logo Kementerian Kehutanan" height="90" style="margin-bottom:12px"/>
  <br/>
  <sub><b>Kementerian Kehutanan — Direktorat IPSDH</b></sub>
  <br/><br/>

  <h1>🛰️ Planet Global Quarterly</h1>
  <h3>Interactive Quad Selector &amp; Downloader</h3>

  <p>
    <img src="https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
    <img src="https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white"/>
    <img src="https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white"/>
    <img src="https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white"/>
    <img src="https://img.shields.io/badge/Planet%20API-NICFI-4CAF50?style=for-the-badge"/>
  </p>

  <p><i>Aplikasi lokal berbasis Python untuk memilih area pada peta, mencari Quad Planet Global Quarterly yang beririsan, mengunduh TIFF, serta membuat workbook Excel berisi peta posisi dan rekap status.</i></p>
</div>

---

## 📌 Mode Area

| Mode | Keterangan |
|:---:|---|
| 🔲 **Area Tertentu** | Gambar satu rectangle pada peta — Quad penuh yang beririsan dipilih |
| 🗺️ **Seluruh Indonesia** | Proses seluruh Quad pada `data/quads.shp` |

> **Catatan:** Rectangle memilih Quad penuh yang beririsan. File TIFF **tidak** dipotong mengikuti bentuk rectangle.

---

## ✨ Fitur Utama

<table>
<tr>
<td width="50%">

**🗺️ Peta & Seleksi**
- Nama mosaic dinamis sesuai Planet Insights/API
- Satu rectangle aktif — rectangle baru menggantikan area lama
- Layer Grid Quad Indonesia opsional (default OFF)
- Tooltip Quad ID pada grid master dan hasil preview

**⬇️ Download**
- 8 worker download paralel secara default
- HTTP Session persisten pada setiap worker
- Batch untuk semua mode area, termasuk 1.000+ Quad
- Batch tidak membuat folder baru

</td>
<td width="50%">

**🔄 Fault Tolerance**
- SQLite checkpoint untuk setiap Quad
- Pause & resume pada folder dan workbook yang sama
- Auto-pause ketika koneksi gagal berulang kali
- Resume file `.part` melalui HTTP Range

**📊 Monitoring**
- Daftar ID Quad sedang diproses secara real-time
- Informasi Quad terakhir selesai atau gagal
- Retry hanya untuk Quad gagal
- Log proses aktif dan ekspor `failed_quads.json/.csv`

</td>
</tr>
</table>

---

## 🔧 Persyaratan

| # | Kebutuhan |
|:---:|---|
| 1 | Windows 10 atau Windows 11 |
| 2 | Python 3.10 – 3.14 |
| 3 | Akun Planet dengan akses Global Quarterly / NICFI Basemaps |
| 4 | API Key Planet |
| 5 | Koneksi internet |
| 6 | Ruang penyimpanan yang cukup |
| 7 | Shapefile master Quad Indonesia |

> SQLite **tidak** memerlukan instalasi package tambahan — tersedia di Python Standard Library.

Shapefile harus lengkap:

```
data/
├── quads.shp
├── quads.dbf
├── quads.shx
├── quads.prj
└── quads.cpg
```

---

## 📁 Struktur Project

```
nicfi_automation/
├── 📄 area_selector_app.py        ← UI utama Streamlit
├── 📄 download_quads.py           ← Engine download (batch, worker, retry)
├── 📄 download_state.py           ← SQLite checkpoint per Quad
├── 📄 run_identity.py             ← Manajemen run ID & state
├── 📄 planet_report.py            ← Generator workbook Excel
├── 📄 requirements.txt
├── 📄 .env / .env.example
├── 📄 README.md / CHANGELOG.md
├── 📄 setup_area_selector.bat
├── 📄 launch_area_selector.bat
│
├── 📂 .streamlit/
│   └── config.toml
├── 📂 asset/
│   └── logo/
│       └── logo-kemenhut-new.png
├── 📂 data/
│   └── quads.*
├── 📂 config/
│   ├── selected_quads.json
│   ├── active_run.json
│   ├── last_run.json
│   ├── download.pause
│   └── download.cancel
└── 📂 downloads/
    ├── Excel Files/
    └── <folder-sesi>/
```

---

## 🚀 Instalasi

### Menggunakan File BAT (Disarankan)

```
1. Ekstrak project ke folder pilihan
2. Buka folder nicfi_automation
3. Jalankan  →  setup_area_selector.bat
4. Tunggu dependency selesai dipasang
5. Isi PL_API_KEY pada .env
6. Letakkan shapefile quads.* pada folder data/
7. Jalankan  →  launch_area_selector.bat
8. Buka http://localhost:8501 jika browser tidak terbuka otomatis
```

### Instalasi Manual (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
python -m streamlit run area_selector_app.py
```

> Hentikan aplikasi dengan `Ctrl + C`

---

## ⚙️ Konfigurasi `.env`

Salin `.env.example` menjadi `.env`, lalu isi API key:

```env
PL_API_KEY=API_KEY_PLANET_ANDA
```

### Konfigurasi Output

```env
OUTPUT_DIR=downloads
EXCEL_OUTPUT_DIR="downloads/Excel Files"
```

### Konfigurasi Performa

```env
MAX_WORKERS=8
DOWNLOAD_BATCH_SIZE=250
DOWNLOAD_CHUNK_MB=8
USE_PERSISTENT_HTTP_SESSIONS=true
```

### Konfigurasi Retry & Resume

```env
DOWNLOAD_MAX_RETRIES=5
RETRY_BACKOFF_SECONDS=3
NETWORK_FAILURE_PAUSE_THRESHOLD=10
RESUME_PARTIAL_DOWNLOADS=true
ENABLE_DOWNLOAD_RESUME=true
```

> `AREA_MODE`, `BBOX`, dan `SELECTED_QUADS_FILE` diperbarui otomatis oleh UI setelah konfigurasi disimpan.

---

## 📖 Panduan Penggunaan

### 🔲 Area Tertentu

> Input **Mosaic** memakai container biasa dengan CSS global (`render_global_widget_css()`) untuk menyembunyikan instruksi keyboard bawaan Streamlit (`Press Enter to apply`). Validasi Planet API hanya berjalan setelah tombol **Terapkan pilihan** ditekan.

```
Step 1 → Isi nama mosaic  (contoh: global_quarterly_2026q2_mosaic)
Step 2 → Pilih "Area tertentu"
Step 3 → Klik [Terapkan pilihan]
Step 4 → Gambar rectangle pada peta
Step 5 → Klik [Preview Area]
Step 6 → Periksa jumlah Quad yang beririsan
Step 7 → Klik [Simpan Konfigurasi]
Step 8 → Klik [Download Semua Quad]
Step 9 → Pantau progress: ID aktif, terakhir selesai/gagal, ETA
Step 10→ Klik [Buka Folder Sesi] atau [Buka Folder Hasil]
```

> Rectangle baru otomatis menggantikan rectangle sebelumnya dan membatalkan preview lama.

---

### 🗺️ Seluruh Indonesia

```
Step 1 → Isi nama mosaic
Step 2 → Pilih "Seluruh Indonesia"
Step 3 → Klik [Terapkan pilihan]
Step 4 → Klik [Gunakan Seluruh Indonesia]
Step 5 → Klik [Simpan Konfigurasi]
Step 6 → Klik [Download Semua Quad]
```

> ⚠️ Seluruh Indonesia dapat berisi **ribuan TIFF**. Gunakan tombol **Pause** daripada menutup paksa proses.

---

## 📦 Sistem Batch

Batch berlaku untuk **semua mode area**, bukan hanya Seluruh Indonesia. Batch tidak membuat folder baru dan tidak mengulang file yang sudah selesai.

**Contoh — Area Tertentu (1.075 Quad, batch size 250):**

```
Batch 1/5  →  250 Quad
Batch 2/5  →  250 Quad
Batch 3/5  →  250 Quad
Batch 4/5  →  250 Quad
Batch 5/5  →   75 Quad
```

**Contoh — Seluruh Indonesia (6.978 Quad):**

```
Satu folder sesi dibuat
→ Batch 1/28   (status tiap Quad disimpan ke SQLite)
→ Batch 2/28   (folder & workbook yang sama)
→ ...
→ Batch 28/28  (selesai atau dijeda)
```

Pilihan ID Quad di-snapshot ke `selection_snapshot.json` saat run dimulai — perubahan rectangle atau `selected_quads.json` setelah run dimulai **tidak** mengubah isi sesi lama.

---

## 📡 Monitoring Real-time

Panel UI menampilkan:

```
┌─────────────────────────────────────────────┐
│  Quad sedang diproses (8)                   │
│  ─────────────────────────────────────────  │
│  1706-1018  1706-1019  1706-1020  1706-1021 │
│  1706-1022  1706-1023  1706-1024  1706-1025 │
└─────────────────────────────────────────────┘
```

**Format log:**

```log
START    1706-1018 | batch=1/28 | active=8/8
DONE     1706-1017 | status=downloaded | size=81.52 MB | attempts=1 | time=4.21s
PROGRESS 14/6978   | downloaded=13 reused=1 failed=0 active=8 pending=6956
FAILED   1706-1020 | attempts=5 | type=ReadTimeout | error=Connection timed out
```

---

## ⏸️ Pause & Resume

| Aksi | Tombol |
|---|---|
| Jeda proses | `⏸ Pause Download` |
| Lanjutkan proses | `▶ Lanjutkan Download` |

Resume menggunakan folder sesi, SQLite, dan workbook yang **sama**. Quad sudah selesai dilewati, Quad pending dilanjutkan.

Jika komputer atau aplikasi tertutup — buka kembali aplikasi. Sesi aktif dibaca dari `config/active_run.json`.

---

## 🌐 Saat Koneksi Terputus

Setiap Quad mencoba ulang sesuai `DOWNLOAD_MAX_RETRIES`. Jika kegagalan berturut-turut mencapai:

```env
NETWORK_FAILURE_PAUSE_THRESHOLD=10
```

Status berubah menjadi **"Download dijeda karena koneksi terputus"**. Setelah koneksi kembali, klik **Lanjutkan Download**.

File belum lengkap menggunakan ekstensi `.tif.part`. Downloader mencoba melanjutkan byte terakhir via HTTP Range jika server mendukung.

---

## 🔁 Quad Gagal & Retry

Quad gagal tersimpan di folder sesi:

```
failed_quads.json   ←  detail error per Quad
failed_quads.csv    ←  format spreadsheet
```

**Informasi yang disimpan:** Quad ID · jumlah percobaan · jenis error · pesan error · HTTP status

Setelah proses berakhir, klik **`🔁 Retry Quad Gagal`** — hanya mengulang Quad gagal pada sesi dan folder yang sama.

---

## 🗃️ SQLite Checkpoint

File `download_state.sqlite` tersimpan di setiap folder sesi.

| Status | Keterangan |
|:---:|---|
| `pending` | Belum diproses atau menunggu resume |
| `downloading` | Sedang diproses oleh worker |
| `downloaded` | Baru selesai diunduh |
| `reused` | Menggunakan TIFF run sebelumnya (mosaic sama) |
| `existing` | File sudah tersedia dalam folder sesi |
| `failed` | Gagal setelah batas retry |
| `cancelled` | Sesi dibatalkan |

> ⚠️ Jangan mengedit database ini secara manual ketika downloader sedang berjalan.

---

## 📂 Output Sesi

```
downloads/
├── 21_14_05_08_2026_global_quarterly_2026q2_mosaic/
│   ├── *.tif                    ← File TIFF hasil download
│   ├── *.tif.part               ← File belum lengkap (resume)
│   ├── download_state.sqlite    ← Checkpoint status per Quad
│   ├── selection_snapshot.json  ← Snapshot ID Quad saat run dimulai
│   ├── run_manifest.json        ← Metadata sesi
│   ├── run.log                  ← Log lengkap
│   ├── failed_quads.json
│   └── failed_quads.csv
└── Excel Files/
    └── 21_14_05_08_2026_global_quarterly_2026q2_mosaic.xlsx
```

> Folder & workbook baru hanya dibuat saat **memulai sesi baru**. Pause, resume, dan retry tidak membuat folder baru.

---

## 🔀 Pemisahan Run Aktif & Riwayat

| File | Isi |
|---|---|
| `config/active_run.json` | Sesi **masih hidup**: `initializing` · `running` · `pausing` · `paused` · `paused_network` |
| `config/last_run.json` | Hasil **terminal**: `completed` · `completed_with_failures` · `failed` · `cancelled` |

Saat run selesai, informasi dipindahkan ke `last_run.json` dan `active_run.json` dihapus otomatis. Hasil lama tetap bisa dibuka tanpa mengunci workflow area baru.

Panel **Hasil Run Terakhir** menampilkan:
- Tombol **Buka Folder Run Terakhir** dan **Buka File Log**
- Expander **Log Run Terakhir** (terbuka secara default)
- Maksimal baris log sesuai `UI_LOG_MAX_LINES`

Tombol **Reset** hanya membersihkan pilihan area aktif — file TIFF, workbook, SQLite, log, dan riwayat run **tidak** dihapus.

---

## 📊 Workbook Excel

Workbook berisi tiga sheet:

| Sheet | Keterangan |
|---|---|
| `MASTER_MAP_QUADS` | Peta posisi seluruh Quad Indonesia |
| `MAP_<periode>` | Peta posisi Quad yang diunduh pada periode ini |
| `DETAIL_<periode>` | Rekap status setiap Quad (selesai, gagal, dll.) |

Workbook dibuat saat proses selesai. Jika `EXCEL_UPDATE_ON_PAUSE=true`, workbook juga diperbarui setelah pause. Simpan menggunakan file sementara → replace atomik untuk mencegah file rusak.

> ⚠️ Tutup file `.xlsx` di Microsoft Excel sebelum proses mencoba memperbaruinya.

---

## ⚡ Pengaturan Performa

| Parameter | Default | Rekomendasi |
|---|:---:|---|
| `MAX_WORKERS` | `8` | Turunkan ke `4` jika banyak timeout |
| `DOWNLOAD_BATCH_SIZE` | `250` | Sesuaikan dengan RAM dan storage |
| `DOWNLOAD_CHUNK_MB` | `8` | Tingkatkan untuk koneksi stabil |

> Jangan langsung gunakan nilai `MAX_WORKERS` sangat tinggi — retry dan rate-limit server dapat membuat proses justru lebih lambat.

---

## 🛠️ Troubleshooting

<details>
<summary><b>❌ Mosaic tidak ditemukan</b></summary>

Pastikan nama sama persis dengan Planet Insights dan akun mempunyai akses mosaic.
</details>

<details>
<summary><b>🔒 HTTP 401 atau 403</b></summary>

Periksa `PL_API_KEY` dan akses mosaic. Proses dihentikan karena retry tidak akan memperbaiki masalah otorisasi.
</details>

<details>
<summary><b>⏳ Download terlihat berhenti</b></summary>

Periksa status UI, `run.log`, koneksi internet, dan `failed_quads.csv`. Jika status `paused`, klik **Lanjutkan Download**.
</details>

<details>
<summary><b>📄 Excel tidak dapat ditimpa</b></summary>

Tutup file `.xlsx` yang sedang terbuka di Microsoft Excel, lalu resume atau retry proses.
</details>

<details>
<summary><b>🖥️ Aplikasi tidak terbuka</b></summary>

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip check
python -m streamlit run area_selector_app.py
```
</details>

<details>
<summary><b>🔄 Tampilan lama masih muncul</b></summary>

Hentikan Streamlit, jalankan ulang, lalu tekan `Ctrl + F5` pada browser.
</details>

---

## 🔄 Cara Memperbarui Project

```
1. Hentikan Streamlit  →  Ctrl + C
2. Simpan  →  .env  |  folder data/  |  folder downloads/
3. Ekstrak paket baru ke folder sementara
4. Replace source code dan file dokumentasi
5. JANGAN timpa .env aktif (kecuali ingin pakai template baru)
6. Jalankan  →  python -m pip install -r requirements.txt
7. Jalankan kembali aplikasi
```

---

## 🔐 Keamanan & GitHub

> ⚠️ **JANGAN pernah commit data berikut ke repository:**

- `.env` dan API key
- File TIFF, Excel, database runtime, dan log
- Folder `downloads/` dan `config/` (runtime)

`.gitignore` pada project sudah mengecualikan file runtime utama. Pastikan penggunaan data mengikuti hak akses Planet dan kebijakan organisasi.

---

## 💬 Melanjutkan Project di Chat Baru

Gunakan file [`PROMPT_LANJUT_NEW_CHAT.md`](PROMPT_LANJUT_NEW_CHAT.md). Upload ZIP project terbaru ke chat baru, lalu kirim isi prompt tersebut agar pengembangan dilanjutkan dari source terkini dan tidak dibangun ulang dari nol.

---

<div align="center">
  <sub>
    Dikembangkan untuk <b>Kementerian Kehutanan — Direktorat Inventarisasi dan Pemantauan Sumber Daya Hutan (IPSDH)</b>
    <br/>
    Data satelit: <b>Planet NICFI Basemaps</b> · Norway's International Climate and Forests Initiative
    <br/><br/>
    <a href="https://github.com/HolitSky">GitHub: HolitSky</a>
  </sub>
</div>
