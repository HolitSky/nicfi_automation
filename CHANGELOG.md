# Changelog

## Run-State Isolation and Universal Batching

- Memisahkan `active_run.json` untuk proses aktif/paused dan `last_run.json` untuk hasil terminal.
- Memigrasikan otomatis active run lama yang sudah completed/failed/cancelled.
- Mencegah status run lama seperti 8/8 selesai mengunci workflow area baru.
- Menambahkan `run_id` unik dan `selection_signature` deterministik.
- Menambahkan `selection_snapshot.json` sebagai sumber pilihan yang stabil untuk resume.
- Resume memakai mosaic, BBOX, dan daftar Quad milik sesi lama walaupun `.env` berubah.
- Batch berlaku konsisten untuk Area tertentu dan Seluruh Indonesia.
- Melanjutkan nomor batch berdasarkan checkpoint yang sudah selesai.
- Menambahkan panel Hasil Run Terakhir yang terpisah dari panel proses aktif.
- Menambahkan retry Quad gagal dan resume sesi historis tanpa membuat folder baru.
- Memperbarui README serta template `.env` tanpa dependency Python baru.


## Real-Time Quad ID Progress

- Menambahkan log `START <Quad ID>` ketika worker mulai memproses Quad.
- Menambahkan log `DONE`, `FAILED`, `PAUSED`, dan `CANCELLED` per Quad.
- Menambahkan `last=<Quad ID>` dan `status=<status>` pada setiap baris progres.
- Menyimpan durasi proses setiap Quad pada SQLite.
- Menambahkan migrasi otomatis kolom `duration_seconds` untuk database lama.
- Menambahkan method `active_items()` dan `recent_items()` pada DownloadState.
- Menambahkan `active_quad_ids`, `active_quad_items`, dan `last_result`
  pada payload run.
- Menampilkan daftar Quad yang sedang diproses pada UI.
- Menampilkan Quad terakhir selesai atau gagal beserta ukuran, durasi,
  dan jumlah percobaan.
- Memperbarui README tanpa menambah dependency baru.


## Resumable Batch Downloader

- Mengubah default worker menjadi 8.
- Menambahkan batch 250 Quad tanpa membuat folder baru.
- Menambahkan persistent HTTP Session per worker.
- Menambahkan SQLite checkpoint `download_state.sqlite`.
- Menambahkan pause graceful dan resume pada sesi yang sama.
- Menambahkan auto-pause ketika kegagalan jaringan beruntun mencapai batas.
- Menambahkan resume file `.part` menggunakan HTTP Range bila didukung.
- Menambahkan daftar Quad gagal dalam JSON, CSV, dan UI.
- Menambahkan tombol Retry Quad Gagal.
- Menjalankan downloader sebagai proses background agar UI tetap responsif.
- Menambahkan progress batch, jumlah status, ukuran data, kecepatan, dan ETA.
- Menambahkan atomic save workbook Excel.
- Menambahkan `download_state.py` dan `planet_report.py`.
- Memperbarui `.env.example`, requirements, setup BAT, README, dan `.gitignore`.
