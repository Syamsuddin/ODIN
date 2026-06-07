Saat user menjalankan /odin:about, tampilkan teks berikut PERSIS (jangan ubah format atau isi):

## ODIN — MCP Agent AI untuk Server Linux

ODIN adalah jembatan dari Claude Code (otak di laptop) ke server Linux (tangan di VPS). Cukup bicara bahasa manusia ke Claude — ODIN yang mengeksekusi perintah di server, menganalisis output, dan mengulangi sampai selesai. Dua file Python, satu dependensi (`mcp[cli]`), nol daemon.

### 17 Tools MCP + 2 Resources

**Eksekusi & Inspeksi**
- `run_command` — jalankan shell command di server (dengan klasifikasi READ/WRITE)
- `service_action` — kelola systemd service (status/restart/reload/start/stop)
- `laravel_deploy` — deploy Laravel one-button dengan pre-flight + drift detection
- `run_tests` — jalankan PHPUnit/Pest test suite
- `tail_log` — baca log file (nginx, Laravel, syslog, dll)
- `http_health_check` — cek HTTP status endpoint
- `server_info` — ringkasan server (OS, disk, memory, PHP, dll)
- `inspect_server` — inspeksi mendalam: type, stack, mode operasi, trend

**Workflow**
- `runbook` — eksekusi multi-step workflow (maks 20 langkah)
- `runbook_templates` — list/ambil template runbook builtin & custom
- `rollback_plan` — saran rollback dari histori sesi
- `session_history` — log semua tool yang dieksekusi sesi ini
- `audit_tail` — baca audit log dengan filter (last, tool, success, since)

**Memory**
- `memory_write` — simpan informasi persisten (instruksi, profil server/user)
- `memory_recall` — cari memory berdasarkan namespace/query/tag
- `memory_forget` — hapus memory entry
- `memory_digest` — ringkasan seluruh memory aktif

**Resources**: `memory://{ns}` (baca memory per namespace), `health://live` (watchdog health check)

### Keamanan 4 Lapis

1. **READ/WRITE Classifier** — 23 sub-classifier otomatis. READ auto-approve, WRITE lanjut ke risk engine
2. **Risk Engine** — 26 aturan shell + DB assessor. Kartu risiko 5 tier: AMAN → RENDAH → SEDANG → TINGGI → KRITIS
3. **Hard-block** — perintah katastrofik (rm -rf /, DROP DATABASE, dll) ditolak otomatis di server
4. **OS-level** — user `odin` dengan sudoers terbatas. Batas keamanan sesungguhnya

### Cara Pakai

Tidak perlu hafal nama tool — cukup bicara ke Claude:
- *"Cek kenapa website error 500"* → Claude panggil `tail_log`, `run_command`, analisis
- *"Deploy versi terbaru"* → Claude panggil `laravel_deploy` dengan pre-flight
- *"Restart nginx dan php-fpm"* → Claude panggil `service_action`, tampilkan risk card
- *"Ingat bahwa backup harus di /var/backups"* → Claude panggil `memory_write`

Setiap operasi WRITE menampilkan kartu risiko untuk Anda review sebelum dieksekusi.

> Ketik **/odin:help** untuk daftar perintah, atau **/odin:status** untuk cek server.

Jangan tambahkan penjelasan lain.
