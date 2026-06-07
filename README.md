<p align="center">
  <img src="assets/odin_header.png" alt="ODIN — MCP Agent AI untuk Server Linux" width="100%"/>
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue?style=for-the-badge&logo=gnu&logoColor=white" alt="AGPL-3.0"/></a>
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/MCP_Tools-15-00bcd4?style=flat-square&logo=lightning&logoColor=white" alt="15 MCP Tools"/></a>
  <a href="#"><img src="https://img.shields.io/badge/Security-4_Layers-e53935?style=flat-square&logo=shield&logoColor=white" alt="4 Security Layers"/></a>
  <a href="#"><img src="https://img.shields.io/badge/Risk_Tiers-5-ff9800?style=flat-square&logo=alert&logoColor=white" alt="5 Risk Tiers"/></a>
</p>

<p align="center">
  <b>MCP Agent AI untuk server Linux</b> — jembatan dari Claude Code (otak di laptop) ke server live (tangan di VPS).
</p>

<p align="center">
  <i>Perintah natural-language dari manusia &#8594; Claude Code memahami intent &#8594; ODIN mengeksekusi di server &#8594; analisis &#8594; ulangi sampai selesai.</i>
</p>


---

## Mengapa ODIN

| Tanpa ODIN | Dengan ODIN |
|-------------|-------------|
| SSH manual, ketik command satu per satu | Instruksi natural language, eksekusi otomatis |
| Lupa urutan deploy, skip langkah | Workflow konsisten via runbook engine |
| Error tak terdeteksi sampai user komplain | 22 pola error dianalisis real-time + saran perbaikan |
| Rollback panik: "commit sebelumnya apa?" | State dicatat otomatis sebelum operasi destruktif |
| Setiap sesi mulai dari nol | Memory persisten: ODIN ingat server, preferensi, instruksi |
| Risk fatigue: approve semua tanpa baca | Kartu risiko 5 tier: baca sekilas, putuskan cepat |
| Server state tak diketahui | Auto-inspect: type, stack, mode terdeteksi otomatis |

---

## Arsitektur

```
LAPTOP (Claude Code CLI)                    SERVER (Ubuntu VPS, user: odin)
┌─────────────────────────┐  SSH stdio MCP  ┌────────────────────────────────┐
│                         │ ──────────────▶ │                                │
│  deploy_agent_guard.py  │  spawn fresh    │  deploy_agent.py (ODIN server) │
│  ├─ READ/WRITE classify │  per sesi       │  ├─ 15 MCP tools               │
│  ├─ Risk engine (5 tier)│                 │  ├─ Output intelligence (22)   │
│  ├─ Mode-aware tier     │                 │  ├─ Rollback tracking          │
│  └─ Kartu risiko UI     │                 │  ├─ Runbook engine (maks 20)   │
│                         │                 │  ├─ Server profiler + modes    │
│  585 baris Python       │                 │  ├─ Memory persisten (JSONL)   │
│                         │                 │  ├─ Audit log (append-only)    │
│                         │                 │  └─ Pre-flight deploy checks   │
│                         │                 │  1578 baris Python             │
└─────────────────────────┘                 └────────────────────────────────┘
```

**Koneksi**: SSH stdio — tidak perlu port tambahan, tidak perlu daemon, tidak perlu API key di server. MCP server di-spawn fresh tiap sesi Claude Code, lalu mati otomatis saat sesi berakhir.

---

## Struktur Proyek

```
ODIN/
├── server/
│   ├── deploy_agent.py    # MCP server ODIN (1578 baris) — jalan di VPS
│   └── run.sh             # Launcher: set env + exec venv python
├── client/
│   ├── deploy_agent_guard.py  # Risk engine + gerbang read/write (585 baris) — jalan di laptop
│   └── update_checker.py      # Cek versi terbaru dari GitHub (155 baris)
├── tests/
│   ├── test_fase3.py          # Test runbook & rollback (36 tests)
│   └── test_profile_mode.py   # Test server profiler & mode (44 tests)
├── docs/
│   └── MEMORY_NOTES.md        # Dokumentasi sistem memory
├── install.sh                 # Installer + server setup wizard (macOS & Linux, 857 baris)
├── install.ps1                # Installer Windows (PowerShell)
├── uninstall.sh               # Uninstaller + auto-clean config (macOS & Linux)
├── uninstall.ps1              # Uninstaller Windows (PowerShell)
├── requirements.txt           # Dependensi: mcp[cli]
├── CLAUDE.md                  # Instruksi untuk Claude Code
├── CHANGELOG.md               # Riwayat perubahan
├── EXECUTIVE_SUMMARY.md       # Rangkuman eksekutif
└── REVIEW_EMPAT_FAKTOR.md     # Review teknis 4 dimensi
```

**Total**: 2318 baris source + 758 baris test = **3076 baris**

---

## 15 Tools MCP

### Eksekusi & Inspeksi

| Tool | Fungsi | Approval |
|------|--------|----------|
| `run_command` | Jalankan command shell apa pun di server | READ: otomatis, WRITE: kartu risiko |
| `tail_log` | Baca N baris terakhir file log (Laravel, Nginx, system) | Otomatis |
| `service_action` | Kelola systemd: status, restart, reload, start, stop | READ: otomatis, WRITE: kartu risiko |
| `server_info` | Ringkasan server: OS, disk, memory, PHP, uptime | Otomatis |
| `inspect_server` | Full inspection: type detection, stack scan, mode derivation | Otomatis |

### Deploy & Testing

| Tool | Fungsi | Approval |
|------|--------|----------|
| `laravel_deploy` | Deploy Laravel satu tombol: git reset, composer, migrate, cache, FPM reload | Kartu risiko TINGGI |
| `run_tests` | Jalankan PHPUnit/Pest test suite | Otomatis |
| `http_health_check` | Verifikasi HTTP status (response code + body) | Otomatis |

### Workflow Intelligence

| Tool | Fungsi | Approval |
|------|--------|----------|
| `runbook` | Eksekusi workflow multi-langkah (maks 20 step) dengan error analysis & rollback per step | Kartu risiko (tier tertinggi) |
| `rollback_plan` | Tampilkan saran undo untuk operasi destruktif terakhir | Otomatis |
| `session_history` | Riwayat semua operasi di sesi ini (in-memory, hilang saat respawn) | Otomatis |

### Memory Persisten

| Tool | Fungsi | Approval |
|------|--------|----------|
| `memory_write` | Simpan fakta/instruksi yang bertahan lintas sesi | Kartu risiko RENDAH |
| `memory_recall` | Cari memory berdasarkan namespace/tag/keyword | Otomatis |
| `memory_forget` | Hapus memory entry (tombstone) | Kartu risiko RENDAH |
| `memory_digest` | Tampilkan seluruh memory aktif (sama dengan yang di-inject saat startup) | Otomatis |

### MCP Resource

| Resource | Fungsi |
|----------|--------|
| `memory://{ns}` | Baca memory per namespace (read-only, tanpa tool call) |

---

## Model Keamanan — 4 Lapis Defense-in-Depth

```
Lapis 1: READ/WRITE Classifier (client — deploy_agent_guard.py)
         23 sub-command classifier (git, docker, mysql, npm, curl, ufw, nginx, ...)
         READ → auto-approve    WRITE → lanjut ke lapis 2
              ↓
Lapis 2: Risk Engine + Kartu Risiko (client)
         5 tier: AMAN → RENDAH → SEDANG → TINGGI → KRITIS
         26 aturan shell + DB risk assessor
         User membaca kartu, memutuskan approve/reject
              ↓
Lapis 3: Hard-block Katastrofik (server — _DANGER_RE)
         rm -rf /, mkfs, dd of=/dev, fork bomb, shutdown, DROP DATABASE
         Ditolak kecuali allow_dangerous=True (double brake)
              ↓
Lapis 4: OS-level (server)
         User odin dengan sudoers terbatas
         Batas keamanan sesungguhnya
```

### Contoh Kartu Risiko

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟠 RISIKO: TINGGI
Cmd   : git reset --hard origin/main
Dir   : /var/www/simuru
Aksi  : Buang semua perubahan lokal
Efek  : Perubahan belum commit hilang permanen
Saran : Stash dulu jika ada kerja penting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Perlindungan Tambahan

- **Command substitution** (`$()` dan backtick) di-detect dan di-force ke "ask" — mencegah bypass via subshell
- **Secret detection** di memory: password, token, private key, JWT, AWS key ditolak masuk JSONL
- **Memory di luar webroot**: tidak bisa diakses via web, tidak ikut `git reset --hard` saat deploy
- **Audit trail**: setiap eksekusi tercatat append-only — untuk forensik pasca-insiden
- **Production mode**: tier risiko naik 1 level + warning `MODE PRODUCTION` di kartu risiko

---

## Kecerdasan Bawaan

### Output Intelligence (22 Pola Error)

Setiap output command dianalisis terhadap 22 pola error — dari spesifik ke generik:

| Kategori | Pola yang Dideteksi |
|----------|---------------------|
| **Database** | SQLSTATE (auth, connection, constraint, general), deadlock, max connections |
| **PHP/Laravel** | Fatal error, OOM, timeout, class not found, composer lock |
| **System** | Disk full, OOM kill, permission denied, command not found |
| **Tools** | Nginx config error, SSL expired, npm build failure |

Hasil analisis dilampirkan otomatis:
```json
{"error_type": "db_conn", "hints": ["Cek service MySQL, pastikan berjalan..."]}
```

### Rollback Tracking

Sebelum command destruktif (git reset, artisan migrate, service restart):
1. `_capture_pre_state()` — tangkap git HEAD, migration status, service status
2. Eksekusi command
3. `_suggest_rollback()` — sarankan undo command spesifik berdasarkan state yang ditangkap

### Runbook Engine

Claude menyusun langkah berdasarkan konteks — ODIN mengeksekusi berurutan (maks 20 step):
- Error analysis per langkah
- Rollback tracking per langkah
- Berhenti otomatis pada kegagalan (kecuali `continue_on_fail=True`)
- Laporan terstruktur: executed/total/skipped/failed

### Pre-flight Checks

Sebelum `laravel_deploy`, otomatis cek: disk (blokir jika >= 95%), git dirty files, commit saat ini, versi PHP. Blocker = deploy dibatalkan dengan laporan.

### Server Profiler & Mode Operasi

Pada startup, ODIN menjalankan inspeksi penuh:
1. **Base inspection** — OS, kernel, uptime, disk, memory, firewall, fail2ban, SSH, cron, users
2. **Type detection** — klasifikasi: `web-app`, `database`, `container`, `general`
3. **Stack inspection** — per-type: web (nginx/PHP/FPM/composer/DB/Redis/SSL), database (MySQL/PG/Mongo), container (Docker/compose)
4. **App inspection** — .env, vendor, framework detection, git state
5. **Mode derivation** — otomatis: `setup` / `deploy` / `production`

**Mode enforcement** (dual layer):
- **Server** (`_mode_gate`): production mode memblokir `laravel_deploy` dan package-install commands
- **Guard** (`_shift_tier`): production mode menaikkan tier risiko +1 level

### Memory Persisten

3 namespace: `server` (fakta infrastruktur), `instruction` (arahan user), `profile` (identitas).
- Append-only JSONL + fold (last-write-wins, tombstone, TTL)
- Auto-inject ke konteks saat spawn — setiap sesi baru langsung "ingat"
- Compaction otomatis saat melebihi `MEMORY_MAX_ENTRIES`
- Storage di luar webroot dan `PROJECT_ROOT` — aman dari deploy & run_command

### Audit Log

Setiap eksekusi tool dicatat ke `audit.jsonl`: timestamp, tool, summary, success, exit_code, durasi, mode. Append-only, tidak pernah dihapus.

---

## Instalasi

### Installer Otomatis (Rekomendasi)

```bash
curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.sh | bash
```

Installer terintegrasi menangani **laptop + server** dalam satu langkah:

**Tahap 1 — Laptop (otomatis)**
- Clone repo, buat venv, install `mcp[cli]`, setup guard hook, buat perintah `odin-update`
- Wizard interaktif: 3 pertanyaan (SSH host, path `run.sh`, scope guard) → config ditulis otomatis ke `~/.claude.json` dan `settings.json`
- Tes koneksi SSH ke server

**Tahap 2 — Server via SSH (opsional, ditawarkan setelah laptop selesai)**
- Cek Python 3 dan `python3-venv` di server
- Buat user `odin` jika belum ada
- Buat venv + install `mcp[cli]`
- Upload `deploy_agent.py` dan generate `run.sh` dengan config yang benar
- Set password user `odin`
- Verifikasi semua file terpasang

SSH ControlMaster digunakan agar hanya satu kali autentikasi selama setup.

**Windows**: `irm https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.ps1 | iex` (laptop only, server setup manual)

### Manual

Jika lebih memilih setup manual (atau installer gagal):

**1. Di Laptop (client)**

```bash
git clone https://github.com/Syamsuddin/ODIN.git ~/.odin
cd ~/.odin
python3 -m venv .venv && .venv/bin/pip install "mcp[cli]"
```

**2. Di Server (VPS)**

```bash
# Buat user odin (sebagai root)
useradd -m -s /bin/bash odin
passwd odin

# Setup venv + install dependensi
python3 -m venv /home/odin/.venv
/home/odin/.venv/bin/pip install "mcp[cli]"

# Salin file dari laptop
scp ~/.odin/server/deploy_agent.py  <host>:/home/odin/
scp ~/.odin/server/run.sh           <host>:/home/odin/

# Set permissions
chmod 600 /home/odin/deploy_agent.py
chmod 755 /home/odin/run.sh
chown -R odin:odin /home/odin/
```

**3. Konfigurasi Claude Code**

MCP server (`~/.claude.json`):

```json
{
  "mcpServers": {
    "odin": {
      "type": "stdio",
      "command": "ssh",
      "args": ["your-server-alias", "/home/odin/run.sh"]
    }
  }
}
```

Guard hook (`.claude/settings.json`):

```json
{
  "permissions": {
    "allow": [
      "mcp__odin__server_info",
      "mcp__odin__tail_log",
      "mcp__odin__http_health_check",
      "mcp__odin__memory_recall",
      "mcp__odin__memory_digest",
      "mcp__odin__session_history",
      "mcp__odin__rollback_plan",
      "mcp__odin__inspect_server"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__odin__(run_command|service_action|laravel_deploy|run_tests|runbook|inspect_server|memory_write|memory_forget)",
        "hooks": [
          {
            "type": "command",
            "command": "python3 '<path-to>/deploy_agent_guard.py'",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

### Update

```bash
odin-update
```

### Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/uninstall.sh | bash
```

Uninstaller menghapus direktori ODIN, membersihkan config `mcpServers.odin` dan hook `mcp__odin__` dari `~/.claude.json` dan semua `settings.json` secara otomatis, serta menawarkan cleanup server via SSH.

Windows: `irm https://raw.githubusercontent.com/Syamsuddin/ODIN/main/uninstall.ps1 | iex`

---

## Cara Kerja End-to-End

```
User: "Cek kenapa website error 500, perbaiki kalau bisa"

Claude Code (otak):
  1. server_info          → "Ubuntu 24.04, disk 42%, PHP 8.3"
  2. http_health_check    → "HTTP 500"
  3. tail_log laravel.log → "SQLSTATE[HY000] [2002] Connection refused"
  4. ODIN analisis        → {error_type: "db_conn", hints: ["Cek service MySQL..."]}
  5. service_action mysql  → "inactive (dead)" ← DITEMUKAN
  6. service_action restart mysql → [KARTU RISIKO SEDANG → user approve] → "active"
  7. http_health_check    → "HTTP 200" ← SELESAI

User membaca laporan: "MySQL mati, sudah di-restart, website kembali normal."
Total waktu: < 2 menit. Intervensi user: 1x approve restart MySQL.
```

---

## Environment Variables

| Variable | Default | Keterangan |
|----------|---------|------------|
| `DEPLOY_MODE` | `local` | `local` atau `ssh` |
| `SSH_TARGET` | — | `user@host` (wajib jika mode ssh) |
| `SSH_PORT` | `22` | Port SSH |
| `SSH_KEY` | — | Path private key (opsional) |
| `PROJECT_ROOT` | cwd | Root aplikasi di server |
| `LOCK_CWD_TO_PROJECT` | `0` | `1` = kunci cwd ke PROJECT_ROOT |
| `ALLOWED_LOG_DIRS` | `/var/log,/var/www,...` | Folder yang boleh dibaca tail_log |
| `DEFAULT_TIMEOUT` | `180` | Timeout default (detik) |
| `MAX_TIMEOUT` | `900` | Timeout maksimum (detik) |
| `OUTPUT_LIMIT` | `20000` | Potong output panjang (karakter) |
| `AGENT_LOG_LEVEL` | `INFO` | Level log |
| `MEMORY_DIR` | `/home/odin/memory` | Folder simpanan memory |
| `MEMORY_MAX_TEXT` | `4000` | Panjang maks teks satu entry |
| `MEMORY_MAX_ENTRIES` | `2000` | Ambang compaction |
| `AUDIT_ENABLED` | `1` | `0` = matikan audit log |
| `ODIN_SKIP_INSPECT` | `0` | `1` = skip startup inspection (untuk testing) |

---

## Testing

```bash
python3 -m pytest tests/ -v          # full test suite (80 tests)
python3 -m py_compile server/deploy_agent.py
python3 -m py_compile client/deploy_agent_guard.py
```

---

## Stack yang Didukung

Dirancang untuk dan diuji dengan:
- **OS**: Ubuntu Linux (VPS)
- **Web**: Laravel / PHP 8.x
- **Database**: MySQL / MariaDB
- **Web server**: Nginx
- **Process**: systemd, PHP-FPM
- **Build**: Composer, NPM
- **VCS**: Git

Auto-detect juga mendukung: PostgreSQL, MongoDB, Docker, Apache, Redis, Supervisor, Let's Encrypt SSL.

`run_command` adalah primitif serbaguna — ODIN bisa mengeksekusi command apa pun yang tersedia di server, tidak terbatas stack di atas.

---

## Angka-Angka Kunci

| Metrik | Nilai |
|--------|-------|
| Total kode | 2318 baris Python (server 1578 + guard 585 + updater 155) |
| Total test | 758 baris (80 automated tests) |
| Dependensi runtime | 1 (`mcp[cli]`) |
| MCP tools | 15 |
| MCP resources | 1 (`memory://{ns}`) |
| Error patterns | 22 |
| Risk rules | 26 shell + DB assessor |
| READ sub-classifiers | 23 (git, docker, mysql, npm, curl, ufw, nginx, ...) |
| Risk tiers | 5 (AMAN / RENDAH / SEDANG / TINGGI / KRITIS) |
| Operation modes | 3 (setup / deploy / production) |
| Security layers | 4 (classifier → risk engine → hard-block → OS) |
| Memory namespaces | 3 (server / instruction / profile) |
| Framework | Tidak ada — pure Python + FastMCP |
| Overhead server | 0 (spawn-per-session, tidak ada daemon) |

---

## Keamanan

Batas sebenarnya = hak OS user `odin` + sudoers. Hook + `_DANGER_RE` = jaring pengaman, bukan sandbox. Keputusan akhir selalu di operator (konfirmasi WRITE).

Filosofi: READ auto-approve, WRITE wajib konfirmasi, katastrofik double-brake. Guard lebih ketat dari server (by design).

---

## Lisensi & Versi

Versi aktif: **1.1.0** — tersimpan di `__version__` pada kedua file Python.
Lihat [CHANGELOG.md](CHANGELOG.md) untuk riwayat perubahan lengkap.

*ODIN v1.1 — ringan, cerdas, aman. Satu file di server, satu file di laptop, otak Claude.*
