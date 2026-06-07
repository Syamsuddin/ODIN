Saat user menjalankan /odin:check-update, lakukan langkah berikut:

## Langkah 1 — Ambil Versi Lokal

Baca file `server/deploy_agent.py` di direktori instalasi ODIN (cek `~/.odin/server/deploy_agent.py` atau path project saat ini). Cari baris `__version__ = "..."` dan catat versinya.

Jika file tidak ditemukan, tampilkan:

> ✗ File `deploy_agent.py` tidak ditemukan. Pastikan ODIN sudah terinstall.

Dan berhenti.

## Langkah 2 — Cek GitHub

Jalankan:

```bash
python3 ~/.odin/client/update_checker.py 2>/dev/null || python3 client/update_checker.py 2>/dev/null
```

Jika update_checker.py tidak ditemukan, fallback ke cek manual:

```bash
git -C ~/.odin fetch origin main --quiet 2>/dev/null && git -C ~/.odin log HEAD..origin/main --oneline
```

## Langkah 3 — Tampilkan Hasil

Jika sudah terbaru:

> ✓ ODIN v[versi] — sudah versi terbaru.

Jika ada update:

> ⚠ Update tersedia! Versi lokal: v[lokal], terbaru: v[remote].
> Jalankan `odin-update` di terminal untuk memperbarui.

Jika gagal cek (tidak ada internet, dll):

> ✗ Gagal memeriksa update. Periksa koneksi internet.

Jangan tambahkan penjelasan lain.
