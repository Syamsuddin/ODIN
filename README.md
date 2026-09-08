<p align="center">
  <img src="assets/odin_header.png" alt="ODIN — MCP Agent AI untuk Server Linux" width="100%"/>
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="MIT"/></a>
</p>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/MCP_Tools-20-00bcd4?style=flat-square&logo=lightning&logoColor=white" alt="20 MCP Tools"/></a>
  <a href="#"><img src="https://img.shields.io/badge/CLI_Commands-19-4caf50?style=flat-square&logo=terminal&logoColor=white" alt="19 CLI Commands"/></a>
  <a href="#"><img src="https://img.shields.io/badge/Security-5_Layers-e53935?style=flat-square&logo=shield&logoColor=white" alt="5 Security Layers"/></a>
  <a href="#"><img src="https://img.shields.io/badge/Risk_Tiers-5-ff9800?style=flat-square&logo=alert&logoColor=white" alt="5 Risk Tiers"/></a>
  <a href="#"><img src="https://img.shields.io/badge/Tests-771-9c27b0?style=flat-square&logo=pytest&logoColor=white" alt="771 Tests"/></a>
</p>

<p align="center">
  <b>MCP Agent AI untuk server Linux</b> — multi-server, multi-project, workdir-based. Claude Code (otak di laptop) ke server live (tangan di VPS).
</p>

<p align="center">
  <i>Perintah natural-language dari manusia &#8594; Claude Code memahami intent &#8594; ODIN mengeksekusi di server &#8594; analisis &#8594; ulangi sampai selesai.</i>
</p>


---

## Yang Baru di v2.3

Rilis ini menutup **seluruh temuan Kritis & Tinggi** dari review keamanan eksternal
atas v2.2, lalu mengganti sistem instalasinya.

**Keamanan** — tiga kebocoran yang bisa direproduksi, kini tertutup:

| | Sebelum | Sekarang |
|---|---|---|
| Sudoers `tail -n * /var/log/*` | `sudo tail -n 1 /var/log/../../etc/shadow` membaca shadow sebagai root (fnmatch tanpa `FNM_PATHNAME`) | Aturan dihapus; `journalctl` wajib `--no-pager`; `certbot renew` tanpa argumen bebas; sudoers divalidasi `visudo -cf` + rollback |
| Kunci SSH `odin` | shell interaktif penuh — pager root jadi jalan eskalasi | `restrict,command="odin-dispatch.sh"` — kunci **hanya** bisa meluncurkan `run.sh [--project <nama>]` |
| Guard: `env rm -rf /var/www/app` | lolos sebagai READ, **dieksekusi tanpa konfirmasi** | pembungkus di-unwrap → dinilai sebagai `rm`; `<(`/`>(` dan `sed --in-place` ikut tertutup |
| Hook `.claude/settings.json` | `project add` **menimpa** hook `Bash` milik user | di-merge per-matcher |

**Robustness** — lima bug yang menggigit pemakaian harian:

- Timeout kini membunuh **seluruh process group** — `composer install`/`npm ci` tak lagi jadi orphan; output non-UTF-8 tak lagi dilaporkan `ERROR`
- Inspeksi startup pindah ke **thread latar** — handshake MCP tak lagi kehabisan waktu (batas 30 detik Claude Code)
- Mode `production` hanya dari **sinyal eksplisit** (`APP_ENV`/`ODIN_ENV`) — VPS staging berumur 8 hari tak lagi kehilangan `laravel_deploy`
- Singleton memverifikasi PID sebagai proses ODIN sebelum mengirim sinyal
- Cache READ diinvalidasi oleh perintah WRITE; `tail_log` tak lagi sukses palsu

**Instalasi** — dua perintah, tanpa `sudo`, kode & state dipisah. Lihat [Instalasi](#instalasi).

> **Sudah memakai ODIN ≤ v2.2?** Perbaikan sudoers **tidak** sampai otomatis ke server
> yang sudah terpasang. Jalankan `odin update <alias>` lalu **`odin server harden <alias>`**.

---

## Mengapa ODIN

| Tanpa ODIN | Dengan ODIN |
|-------------|-------------|
| SSH manual, ketik command satu per satu | Instruksi natural language, eksekusi otomatis |
| Lupa urutan deploy, skip langkah | Workflow konsisten via runbook engine |
| Error tak terdeteksi sampai user komplain | 23 pola error dianalisis real-time + saran perbaikan |
| **Error berulang, debugging ulang dari nol** | **Auto-save lesson ke memory — belajar dari pengalaman** |
| Rollback panik: "commit sebelumnya apa?" | State dicatat otomatis sebelum operasi destruktif |
| Setiap sesi mulai dari nol | Memory persisten: ODIN ingat server, preferensi, instruksi |
| Risk fatigue: approve semua tanpa baca | Kartu risiko 5 tier: baca sekilas, putuskan cepat |
| Server state tak diketahui | Auto-inspect: type, stack, mode terdeteksi otomatis |
| Satu server, satu project per session | Multi-server, multi-project — switch = pindah folder |
| Tidak tahu sedang kerja di project mana | Project identity tampil di SETIAP risk card + `/odin:status` |
| **Context manual: harus ingat recall memory** | **Orchestrator auto-recall memory relevan setiap tool call** |

---

## Arsitektur

```
LAPTOP (Claude Code CLI)                    SERVER(S) (VPS, user: odin)
┌─────────────────────────┐                 ┌────────────────────────────────┐
│  odin_cli.py            │                 │  odin-dispatch.sh (forced-cmd) │
│  ├─ setup (wizard)      │  SSH stdio MCP  │    └─ run.sh --project <name>  │
│  ├─ server add/…/harden │ ──────────────▶ │  odin_agent.py (shared)        │
│  ├─ project add/…/sync  │  per project    │  projects/<name>.conf          │
│  ├─ global enable       │                 │  memory/<name>/ (isolated)     │
│  └─ update / self-update│                 │                                │
│     doctor / uninstall  │                 │  20 MCP tools                  │
│                         │                 │  Output intelligence (23)      │
│  odin_guard.py          │                 │  Rollback tracking             │
│  ├─ READ/WRITE classify │                 │  Runbook engine + templates    │
│  ├─ Risk engine (5 tier)│                 │  Server profiler + modes       │
│  ├─ Per-project mode    │                 │  Audit log + watchdog          │
│  ├─ Project identity UI │                 │                                │
│  └─ Kartu risiko + warn │                 │  3799 baris Python             │
│                         │                 └────────────────────────────────┘
│  ~/.odin/               │
│  ├─ app/   kode + venv  │   ← odin self-update
│  ├─ bin/odin  (wrapper) │   → ~/.local/bin/odin
│  ├─ keys/ servers/      │   ┐
│  └─ projects/ modes/    │   ┘ state — tak disentuh installer
│                         │
│  ~3583 baris Python     │
└─────────────────────────┘
```

**Koneksi**: SSH stdio — tidak perlu port tambahan, tidak perlu daemon, tidak perlu API key di server. MCP server di-spawn fresh tiap sesi Claude Code per project, lalu mati otomatis saat sesi berakhir.

---

## Struktur Proyek

```
ODIN/
├── server/
│   ├── odin_agent.py        # MCP server (3794 baris) — jalan di VPS
│   │                        # + Orchestrator + Continuous Learning + Cortex
│   ├── run.sh               # Multi-project launcher: --project <name>
│   └── odin-dispatch.sh     # Forced-command SSH: kunci ODIN tak memberi shell
├── client/
│   ├── odin_cli.py          # CLI multi-server & multi-project (2381 baris)
│   ├── odin_guard.py        # Risk engine + guard (895 baris) — project-aware
│   ├── odin_mcp_launch.py   # Launcher MCP global: cwd → project (151 baris)
│   └── update_checker.py    # Cek versi terbaru (155 baris)
├── tests/                   # 771 tests across 17 files
│   ├── test_core.py         # (59), test_guard.py (160), test_memory.py (58)
│   ├── test_memory_improvements.py (42)  # Semantic search, staleness, similarity
│   ├── test_cortex.py       # (40) — Global consciousness + event journal
│   ├── test_orchestrator.py # (22) — Sistem saraf otonom
│   ├── test_learning.py     # (25) — Continuous learning (3 loop)
│   ├── test_output_intelligence.py (48), test_fase2_intelligence.py (25)
│   ├── test_fase3.py (36), test_fase3_ux.py (32), test_fase4_proactive.py (38)
│   ├── test_profile_mode.py (57)
│   ├── test_guard_multiproject.py (24)  # Project awareness tests
│   ├── test_review_fixes.py (45)        # Regresi temuan review v2.2
│   ├── test_installer.py (17)           # install.sh nyata + setup/self-update/uninstall
│   └── test_cli.py          # (43) — CLI + run.sh + project tests
├── install.sh               # Installer (macOS & Linux) — tanpa sudo, venv, tag rilis
├── install.ps1              # Installer (Windows PowerShell) — cermin install.sh
├── uninstall.sh / .ps1      # Pembungkus `odin uninstall` (+ tata letak lama)
├── requirements.txt         # Server: mcp[cli]
├── requirements-cli.txt     # Laptop CLI: paramiko, pyyaml
└── CHANGELOG.md
```

Catatan: `CLAUDE.md`, `docs/`, dan dokumen desain internal sengaja di-gitignore —
clone segar tidak memilikinya, jadi jangan menganggapnya bagian dari struktur repo.

**Total**: ~7382 baris source + ~7031 baris test

---

## 20 Tools MCP + 2 Resources

### Eksekusi & Inspeksi

| Tool | Fungsi | Approval |
|------|--------|----------|
| `run_command` | Jalankan command shell apa pun di server | READ: otomatis, WRITE: kartu risiko |
| `tail_log` | Baca N baris terakhir file log (Laravel, Nginx, system) | Otomatis |
| `service_action` | Kelola systemd: status, restart, reload, start, stop | READ: otomatis, WRITE: kartu risiko |
| `server_info` | Ringkasan server: OS, disk, memory, PHP, uptime, **project_name** | Otomatis |
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
| `runbook_templates` | List/ambil template runbook builtin & custom | Otomatis |
| `rollback_plan` | Tampilkan saran undo untuk operasi destruktif terakhir | Otomatis |
| `session_history` | Riwayat semua operasi di sesi ini (in-memory, hilang saat respawn) | Otomatis |
| `audit_tail` | Baca audit log dengan filter (last, tool, success, since) | Otomatis |

### Memory Persisten & Cortex

| Tool | Fungsi | Approval |
|------|--------|----------|
| `memory_write` | Simpan fakta/instruksi yang bertahan lintas sesi (routing otomatis: cortex vs project) | Kartu risiko RENDAH |
| `memory_recall` | Cari memory berdasarkan namespace/tag/keyword dengan **semantic search TF-IDF** | Otomatis |
| `memory_forget` | Hapus memory entry (tombstone) | Kartu risiko RENDAH |
| `memory_digest` | Tampilkan cortex (global) + project memory + cross-project events (24j) | Otomatis |
| `memory_health` | Diagnostik: stale entries, duplicates, namespace counts, file size, event count | Otomatis |
| `cortex_log` | Log event lintas-project ke cortex event journal (auto-called saat deploy/service restart) | Kartu risiko RENDAH |
| `cortex_events` | Baca event journal lintas-project (filter: hours, project, limit) | Otomatis |

### MCP Resources

| Resource | Fungsi |
| --- | --- |
| `memory://{ns}` | Baca memory per namespace (read-only, tanpa tool call) |
| `health://live` | Watchdog: cek disk, memory, load, service status — untuk polling via `/loop` |

---

## Multi-Project — Workdir-Based Switching

### Konsep Inti

**1 project = 1 workdir lokal + 1 server remote + memory terisolasi + mode sendiri.**

Switch project = pindah folder, buka Claude Code baru. Tidak ada perintah switch manual di dalam sesi Claude Code (1 sesi = 1 MCP connection = 1 project).

### Project Awareness (v2.0)

ODIN sekarang **tahu dan tampilkan** project aktif di mana-mana:

1. **Risk cards** — setiap kartu risiko menampilkan identitas project sebagai **baris PERTAMA**:
   ```
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Prj   : simuru → vps-app
   🟡 RISIKO: SEDANG
   Cmd   : systemctl restart nginx
   ...
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ```

2. **`/odin:status`** — skill menampilkan project sebagai baris pertama:
   ```
   Project   : simuru → vps-app (production)
   Server    : Ubuntu 22.04, uptime 45d, disk 62%
   Memory    : 12 entries (3 instruksi)
   ```

3. **Guard warnings** — jika project tidak terdaftar di `~/.odin/projects/`, kartu risiko menampilkan warning:
   ```
   ⚠ Project 'simuru' tidak terdaftar di ~/.odin/projects/
   ```

4. **CLI validation** — `odin project status` memvalidasi config lokal + SSH ping server

5. **CLI switch helper** — `odin project switch <name>` membuka tab Terminal baru di workdir project (macOS: via `osascript`, fallback: print `cd` command)

### CLI Commands

| Command | Fungsi |
|---------|--------|
| `odin server add` | Setup server baru (interaktif: hostname, port, user, password) |
| `odin server list` | Daftar server terdaftar |
| `odin server remove <alias> [--purge]` | Hapus server (`--purge`: cabut juga kunci ODIN dari `authorized_keys` server) |
| `odin server test <alias>` | Test koneksi + ODIN health + handshake MCP |
| `odin server harden <alias>` | **Baru** — pasang ulang sudoers aman + kunci forced-command pada server yang dipasang ODIN ≤ v2.2 (butuh kredensial admin) |
| `odin project add` | Link workdir lokal ↔ server:project (interaktif **atau** via flags) |
| `odin project list` | Daftar project terdaftar (dengan marker `→` untuk project aktif) |
| `odin project status [name]` | Validasi project: config lokal + SSH ping server |
| `odin project switch <name>` | Buka tab Terminal baru di workdir project |
| `odin project sync [name] [--all]` | **Baru** — Regenerasi config lokal dari manifest (pemulihan / migrasi) |
| `odin project remove <name>` | Hapus project config (bersihkan `.claude/settings.json` + `.mcp.json`) |
| `odin global enable [--migrate]` | Pasang MCP odin di scope-user (server → `~/.claude.json`, hook & allow → `~/.claude/settings.json`) → tersedia otomatis di semua project; `--migrate` cabut entry per-workdir lama |
| `odin global disable` | Cabut entry MCP odin global |
| `odin setup` | **Baru** — satu wizard: server → project → Claude Code → verifikasi (idempoten) |
| `odin update <alias>` | Update agent + run.sh + dispatcher di **server** (backup → compile-check → ganti atomik → handshake MCP) |
| `odin self-update [ref]` | **Baru** — update ODIN di **laptop** ke tag rilis terbaru (atau ref tertentu; rollback pun bisa) |
| `odin doctor [alias]` | Tanpa alias: diagnostik **laptop**. Dengan alias: server + handshake MCP nyata per project |
| `odin uninstall [--purge]` | **Baru** — hapus ODIN dari laptop; state (kunci, registry) dipertahankan kecuali `--purge` |
| `odin version` | **Baru** — versi, lokasi kode & state |

**Registrasi non-interaktif (batch/scripting):**

```sh
odin project add --name ekampus --server gibtha_srv \
    --remote-root /var/www/ekampus --workdir ~/PROJECTS/eKAMPUS --yes
```

> **Dua mode config.** `odin global enable` (dipilih `odin setup` secara default) memasang
> SATU entry MCP di `~/.claude.json`; project diresolusi dari cwd saat spawn oleh
> `odin_mcp_launch.py`, sehingga project baru tak perlu config per-repo. Alternatifnya
> config per-workdir di `.claude/settings.json`. Keduanya di-hook oleh guard dengan matcher
> `mcp__odin__.*` — hook milik user di-merge, tidak ditimpa. Entry `odin` dari `.mcp.json`
> format lama otomatis dimigrasikan agar tidak ada definisi MCP ganda.

### Alur Kerja

1. `odin setup` — wizard: server (1x per server) → project (1x per project) → Claude Code → verifikasi
2. `cd ~/project && claude` — ODIN otomatis aktif ke server & project yang benar
3. `/odin:status` — cek project identity + server state
4. Risk card setiap WRITE operation menampilkan `Prj : <name> → <server>`
5. Project berikutnya: `cd ~/project-lain && odin setup` (server yang ada dipakai ulang)

### Isolasi Per Project

- **Memory**: `memory/<project>/` di server (terpisah)
- **Audit log**: per project
- **Mode operasi**: `~/.odin/modes/<project>` di laptop
- **MCP config**: `~/.claude.json` (global, rekomendasi — project diresolusi dari cwd) atau `<workdir>/.claude/settings.json` (per-workdir)
- **Project identity**: di-export via `PROJECT_NAME` env var dari `.conf` ke agent

---

## Model Keamanan — 5 Lapis Defense-in-Depth

```
Lapis 1: READ/WRITE Classifier (client — odin_guard.py)
         23 sub-command classifier (git, docker, mysql, npm, curl, ufw, nginx, ...)
         READ → auto-approve    WRITE → lanjut ke lapis 2
              ↓
Lapis 2: Risk Engine + Kartu Risiko (client)
         5 tier: AMAN → RENDAH → SEDANG → TINGGI → KRITIS
         26 aturan shell + DB risk assessor
         Project identity tampil di SETIAP kartu (Prj : <name> → <server>)
         User membaca kartu, memutuskan approve/reject
              ↓
Lapis 3: Hard-block Katastrofik (server — _DANGER_RE)
         rm -rf /, mkfs, dd of=/dev, fork bomb, shutdown, DROP DATABASE
         Ditolak kecuali allow_dangerous=True (double brake)
              ↓
Lapis 4: Kunci SSH ber-forced-command (server — odin-dispatch.sh)
         authorized_keys: restrict,command="/home/odin/odin-dispatch.sh"
         Kunci ODIN HANYA bisa meluncurkan run.sh [--project <nama>] —
         tak ada shell interaktif, tak ada TTY, tak ada pager sbg root
              ↓
Lapis 5: OS-level (server)
         User odin dengan sudoers terbatas (tanpa wildcard argumen path)
         Batas keamanan sesungguhnya
```

### Sudoers: aturan yang SENGAJA tidak ada

sudo mencocokkan argumen dengan `fnmatch(3)` **tanpa** `FNM_PATHNAME` — `*` ikut
cocok dengan `/` dan `..`. Karena itu aturan berwildcard-path bukan pembatas:

| Aturan | Kenapa dihapus |
|---|---|
| `tail -n * /var/log/*` | `tail -n 1 /var/log/../../etc/shadow` membaca shadow sbg root |
| `certbot renew *` | `--deploy-hook='...'` dieksekusi sbg root |
| `/usr/local/bin/*-deploy` | wildcard nama = skrip apa pun yang bisa ditulis user lain |
| `journalctl *` | tanpa `--no-pager`, pager root + `!/bin/sh` = shell root |

`odin server add` memvalidasi sudoers dengan `visudo -cf` sebelum memasangnya, dan
membatalkan (tanpa mengganti file) bila tidak valid — sudoers rusak = sudo server rusak.

> **Server yang sudah terpasang tidak ikut terperbaiki otomatis.** `server add`
> melewati sudoers bila `/etc/sudoers.d/odin` sudah ada, dan `odin update` berjalan
> sebagai user `odin` (tanpa hak menulis `/etc/sudoers.d`). Untuk server dari ODIN
> ≤ v2.2 jalankan **`odin server harden <alias>`** (butuh kredensial admin). Ia
> mem-backup sudoers lama, memasang yang baru dengan validasi `visudo`, memasang
> dispatcher, lalu membatasi kunci ke forced-command — dan **memverifikasi lewat
> koneksi SSH baru**, mengembalikan `authorized_keys` bila verifikasi gagal
> (tidak ada risiko terkunci). `odin update` & `odin doctor` melaporkan bila server
> masih rentan.

### Contoh Kartu Risiko (v2.0)

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prj   : simuru → vps-app
🟠 RISIKO: TINGGI
Cmd   : git reset --hard origin/main
Dir   : /var/www/simuru
Aksi  : Buang semua perubahan lokal
Efek  : Perubahan belum commit hilang permanen
Saran : Stash dulu jika ada kerja penting
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Dengan warning jika project tidak terdaftar:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Prj   : simuru → vps-app
🟡 RISIKO: SEDANG
Cmd   : rm -rf /tmp/cache
...
⚠ Project 'simuru' tidak terdaftar di ~/.odin/projects/
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Perlindungan Tambahan

- **Perintah pembungkus di-unwrap** (v2.3): `env`, `nice`, `nohup`, `timeout`, `stdbuf`, `setsid`, `xargs`, `command` dilewati beserta flag & `VAR=val`-nya — `env rm -rf /x` dinilai sebagai `rm`, bukan sebagai `env`
- **Command & process substitution** (`$()`, backtick, `<(`, `>(`) di-force ke "ask" — mencegah bypass via subshell
- **Normalisasi flag** (v2.3): `rm -fr`, `rm -f -r`, `rm --recursive --force` semuanya dikenali sebagai `rm -rf` sebelum pencocokan pola katastrofik — fungsi ini ada di **kedua sisi** dan harus tetap sama
- **Secret detection** di memory: password, token, private key, JWT, AWS key ditolak masuk JSONL
- **Memory di luar webroot**: tidak bisa diakses via web, tidak ikut `git reset --hard` saat deploy
- **Audit trail**: setiap eksekusi tercatat append-only dengan `project` field, **dicerminkan ke journald** (v2.3) — `audit.jsonl` milik user `odin` dan bisa di-truncate lewat `run_command` yang sama, jadi forensik butuh sumber kedua
- **Production mode**: tier risiko naik 1 level + warning `MODE PRODUCTION` di kartu risiko
- **Project identity everywhere**: setiap risk card, service card, dan `/odin:status` menampilkan project name — pada konfigurasi MCP global, project diresolusi dari `~/.odin/projects` (v2.3)

---

## Kecerdasan Bawaan

### Output Intelligence (23 Pola Error)

Setiap output command dianalisis terhadap 23 pola error — dari spesifik ke generik:

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

Sebelum `laravel_deploy`, otomatis cek: disk (blokir jika >= 95%), git dirty files, commit saat ini, versi PHP, **drift detection** (bandingkan state vs deploy fingerprint terakhir). Blocker = deploy dibatalkan dengan laporan.

### Server Profiler & Mode Operasi

Pipeline inspeksi:
1. **Base inspection** — OS, kernel, uptime, disk, memory, firewall, fail2ban, SSH, cron, users
2. **Type detection** — klasifikasi: `web-app`, `database`, `container`, `general`
3. **Stack inspection** — per-type: web (nginx/PHP/FPM/composer/DB/Redis/SSL), database (MySQL/PG/Mongo), container (Docker/compose)
4. **App inspection** — .env (termasuk `APP_ENV`), vendor, framework detection, git state
5. **Mode derivation** — `setup` / `deploy` / `production`

**Startup tidak pernah memblokir handshake MCP** (v2.3). Profil cache < 1 jam dipakai
langsung; bila tidak ada, inspeksi dijadwalkan di **thread latar** dan sesi langsung hidup
dengan mode `deploy`. Sebelumnya inspeksi berjalan saat import — empat perintah serial
dengan timeout 30+15+30+15 detik, jauh di atas batas koneksi MCP Claude Code (30 detik),
sehingga server bisa gagal connect tanpa pesan jelas. `inspect_server` tetap sinkron
saat dipanggil manual.

**Mode `production` hanya dari sinyal EKSPLISIT** (v2.3): `APP_ENV=production|prod|live`
di `.env` aplikasi, atau `ODIN_ENV=production`, atau override memory `server:mode-override`.
Uptime dan disk **bukan** sinyal lingkungan — aturan lama (`uptime > 7 hari && disk < 80%`)
membuat VPS staging yang hidup 8 hari otomatis jadi `production`, kehilangan
`laravel_deploy` dan `apt/npm/pip install`, dan keputusan itu di-cache 1 jam.

**Mode enforcement** (dual layer):
- **Server** (`_mode_gate`): production memblokir `laravel_deploy` dan package-install — pola menoleransi flag sebelum sub-perintah, jadi `apt -y install` ikut terblokir
- **Guard** (`_shift_tier`): production menaikkan tier risiko +1 level

**Mode per project**: disimpan di `~/.odin/modes/<project>` dan auto-sync via PostToolUse hook saat `inspect_server`.

### Memory Persisten & Cortex (Global Consciousness)

**Arsitektur 2-layer**:
- **Cortex** (global): `memory/_cortex/` — namespace `profile` (operator) + `cross` (lintas-project)
- **Project** (isolated): `memory/<project>/` — namespace `server` (fakta infra) + `instruction` (arahan)

**Fitur intelligence**:
- **Semantic search TF-IDF** di `memory_recall` — ranking relevansi (stdlib-only, no deps)
- **Staleness detection**: entry > 30 hari dapat marker `[STALE?]` di digest
- **Similarity detection**: `memory_write` warning jika ada entry mirip (Jaccard >= 0.45)
- **Digest budget**: max 3000 chars, prioritas profile > instruction > server (newest first)
- **Event journal**: cross-project events auto-logged saat deploy/service restart → 24h digest
- **Auto-inject** ke konteks: cortex + project + cross-project events → Claude langsung aware

**Continuous learning** (otomatis):
- Error berulang (≥3x) → auto-save lesson ke `server:error-lesson-*`
- Deploy/runbook sukses → auto-save pattern ke `server:last-successful-deploy`
- Error frequency bertahan lintas sesi via `server:error-freq`

**Storage**: append-only JSONL + fold (last-write-wins, tombstone, TTL), di luar webroot.

Compaction (v2.3) dipicu oleh **rasio record mati** (> 50%) atau **ukuran berkas** (> 8 MB),
bukan lagi jumlah entry hidup — pemicu lama tak pernah tercapai lewat upsert (id-nya tetap,
hanya versi lamanya menumpuk), sehingga histori mati tumbuh tanpa batas. Penulisan ulang
memakai `mkstemp` di direktori yang sama di bawah `flock`. `audit.jsonl` & `events.jsonl`
dirotasi pada 5 MB. Fold cache diinvalidasi lintas-proses lewat `(mtime, size)` — sesi A
kini melihat instruksi baru yang ditulis sesi B.

### Orchestrator — Sistem Saraf Otonom

**Berjalan otomatis** di setiap tool call utama (6 tools: run_command, tail_log, service_action, laravel_deploy, run_tests, http_health_check). Memperkaya result dengan:

1. **`_memory_context`** (Thalamus — auto-recall):
   - Recall memory relevan berdasarkan tool + args (TF-IDF token overlap)
   - Maks 3 entry dari namespace `instruction` + `cross`
   - Claude dapat context tanpa harus ingat memanggil `memory_recall`

2. **`_suggested_next`** (Cerebellum — pattern-based suggestion):
   - Deploy sukses → "http_health_check"
   - Deploy gagal → "rollback_plan" + "tail_log"
   - Service restart → "http_health_check"
   - Error DB → "service_action untuk cek MySQL"
   - Error permission → "cek ownership/permission file"

3. **`_attention`** (RAS — alert system):
   - Error berulang → "BERULANG — pertimbangkan investigasi root cause"
   - Cross-project events (6j terakhir, severity warn/error) → info dari project lain

4. **`_learned`** (Auto-learning):
   - Error berulang → "Auto-learned: db_conn → lesson tersimpan"
   - Deploy sukses → "Recorded: deploy sukses (branch=main, backup=ya)"

**Transparan**: Claude melihat semua enrichment di result dict. ODIN tidak mengubah state — hanya memberikan context yang lebih kaya.

### Continuous Learning — Belajar dari Pengalaman

**3 learning loop otomatis**:

#### Loop 1: Error → Lesson

Saat error terjadi ≥3x dalam sesi:
- Auto-save ke memory: `server:error-lesson-{type}`
- Isi: penyebab, solusi, command pemicu, frekuensi (termasuk cross-session)
- Satu lesson per error type per sesi (tidak spam)
- Orchestrator auto-recall lesson ini di tool call berikutnya yang relevan

Contoh:
```
Error 'db_conn' terdeteksi 5x (termasuk 2x dari sesi sebelumnya).
Penyebab: MySQL connection refused. 
Solusi: systemctl status mysql; systemctl restart mysql.
Terakhir dipicu: php artisan migrate
```

#### Loop 2: Cross-Session Error Tracking

Error frequency **bertahan lintas sesi**:
- `_error_counts` disimpan ke `server:error-freq` saat shutdown (atexit hook)
- Load saat startup → error 2x sesi kemarin + 1x sesi ini = recurring terdeteksi
- `_analyze_output` menandai `cross_session: true` di analysis
- Recurring hint: "Error 'db_conn' sudah terjadi 4x (termasuk 3x dari sesi sebelumnya)"

#### Loop 3: Success → Pattern

Milestone sukses auto-save pola:
- **laravel_deploy** sukses → `server:last-successful-deploy`
  - Mencatat: branch, apakah backup dilakukan, health check passed, urutan tool
  - Contoh: "Deploy branch 'hotfix' berhasil. Backup dilakukan sebelum deploy (BAIK). Health check passed. Urutan: run_command → laravel_deploy → http_health_check"
- **runbook** sukses (executed = total) → `server:success-runbook-{name}`
- TTL 90 hari (auto-expire)

**Siklus belajar**:
```
Sesi 1: Error db_conn 3x → ODIN auto-save lesson
Sesi 2: Error db_conn lagi → Orchestrator auto-recall lesson → 
        Claude: "Saya ingat masalah ini. Cek MySQL dulu." → langsung fix
```

### Ketahanan Eksekusi (v2.3)

| Masalah | Perbaikan |
|---|---|
| Timeout hanya membunuh wrapper `bash` — `composer install`/`npm ci` terus jalan sebagai orphan | `Popen(start_new_session=True)` + `killpg` **seluruh process group** saat timeout |
| Output non-UTF-8 (mysqldump, git log latin1) melempar `UnicodeDecodeError` → dilaporkan `ERROR` dengan `stdout=""` padahal perintah sukses | dekode dengan `errors="replace"` |
| `capture_output` menampung output tak terbatas di RAM sebelum dipotong | dialirkan ke buffer **head/tail terbatas** |
| `cat f` → `sed -i` → `cat f` mengembalikan isi lama selama 60 detik | cache READ diinvalidasi oleh setiap perintah WRITE |
| `tail <file-hilang> \| grep x \|\| true` exit 0 → sukses palsu | `PIPESTATUS` memisahkan kegagalan `tail` dari grep-tanpa-hasil |
| `tail` yang sukses selalu exit 0 → deteksi SQLSTATE tak pernah terpicu | analisis pola error dijalankan pada **isi log**, bukan hanya saat exit ≠ 0 |
| Crash di tengah tulis menyisakan baris tanpa `\n` → record berikutnya ikut gugur | append memeriksa byte terakhir dan menyisipkan `\n` bila perlu |
| Singleton mengirim SIGKILL 1,5 detik setelah SIGTERM ke PID dari file yang selamat dari reboot | PID diverifikasi sebagai proses `odin_agent.py`; SIGTERM ditunggu sampai ~15 detik |

### Audit Log

Setiap eksekusi tool dicatat ke `audit.jsonl`: timestamp, tool, summary, success, exit_code, durasi, mode, **project**. Append-only, tidak pernah dihapus. Isolated per project.

---

## Instalasi

Dua perintah dari nol sampai bekerja. Tanpa `sudo`, tanpa mengutak-atik PATH atau
config Claude Code secara manual.

**macOS & Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.sh | bash
odin setup
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.ps1 | iex
odin setup
```

### Apa yang dilakukan installer

| Langkah | Detail |
|---|---|
| Prasyarat | git, Python ≥ 3.10, ssh. Claude Code CLI dicek (peringatan bila belum ada) |
| Versi | **Tag rilis terbaru** dari GitHub (bukan `main` HEAD). Pin manual: `ODIN_VERSION=v2.3.0` |
| Kode | `~/.odin/app/` — checkout git yang bisa diganti versi kapan saja |
| Dependensi | venv terisolasi di `~/.odin/app/.venv/` — kebal PEP 668 (Debian 12, Ubuntu 23.04+, Homebrew) |
| Perintah | `~/.odin/bin/odin` + symlink `~/.local/bin/odin`; PATH ditambahkan ke shell rc (dengan izin) |
| Claude Code | slash command `/odin:*` ke `~/.claude/commands/odin/` |
| Verifikasi | `odin --version` dan import `paramiko`/`pyyaml` dijalankan sungguhan |

Idempoten: menjalankan installer lagi = update. **Tidak pernah memakai `sudo`.**

Opsi: `--yes` (non-interaktif), `--no-setup`, `--version <ref>`, `--home <dir>`.
Lewat pipe: `curl … | bash -s -- --yes`. Env setara: `ODIN_VERSION`, `ODIN_HOME`.

### Tata letak di laptop

Kode dan **state dipisah** — installer/self-update tak pernah menyentuh state.

```
~/.odin/
├── app/                    kode ODIN (git)  ← odin self-update
│   └── .venv/              dependensi CLI
├── bin/odin                wrapper CLI
├── keys/                   kunci SSH per server        ┐
├── servers/  projects/     registry                    │ state milik Anda
├── modes/    ssh_config    mode per project, SSH entry ┘
└── client/ server/ → app/  symlink kompatibilitas (hook & MCP dari versi lama tetap valid)
~/.local/bin/odin → ~/.odin/bin/odin
```

Pengguna ODIN ≤ v2.2 (kode langsung di `~/.odin`): installer memigrasikan otomatis —
hanya file yang dilacak git yang dipindah, `keys/ servers/ projects/ modes/` utuh.

### `odin setup` — satu wizard

```
[1/4] Server       → odin server add (user odin, sudoers tervalidasi, venv, agent,
                     kunci forced-command, handshake MCP)   — dilewati bila sudah ada
[2/4] Project      → odin project add (workdir = folder saat ini) — dilewati bila terdaftar
[3/4] Claude Code  → MCP odin global (rekomendasi) + hook guard + allow-list
[4/4] Verifikasi   → handshake MCP nyata ke project ini
      → cd <workdir> && claude
```

Aman dijalankan berulang: tahap yang sudah beres dilewati.

### Perintah pemeliharaan

```bash
odin doctor               # laptop: python, deps, PATH, MCP global, hook, server & project
odin doctor <alias>       # server: file, venv, sudoers, kunci, handshake MCP per project
odin self-update          # perbarui ODIN di LAPTOP (tag rilis terbaru; atau: odin self-update v2.4.0)
odin update <alias>       # perbarui agent di SERVER (backup → compile-check → atomik → handshake)
odin server harden <alias>   # server lama: sudoers aman + kunci forced-command
odin version              # versi + lokasi kode & state
```

### Uninstall

```bash
odin uninstall            # hapus kode, wrapper, entry Claude Code — state DIPERTAHANKAN
odin uninstall --purge    # hapus juga kunci SSH & registry
```

Sebelum `--purge`, cabut kunci dari server dulu: `odin server remove <alias> --purge`.
Bila CLI sudah rusak: `curl -fsSL …/uninstall.sh | bash` (Windows: `…/uninstall.ps1 | iex`).

---

## Cara Kerja End-to-End

```
User: "Cek kenapa website error 500, perbaiki kalau bisa"

Claude Code (otak):
  1. server_info          → "Project: simuru, Ubuntu 24.04, disk 42%, PHP 8.3"
  2. http_health_check    → "HTTP 500"
  3. tail_log laravel.log → "SQLSTATE[HY000] [2002] Connection refused"
  4. ODIN analisis        → {error_type: "db_conn", hints: ["Cek service MySQL..."]}
  5. service_action mysql  → "inactive (dead)" ← DITEMUKAN
  6. service_action restart mysql → [KARTU RISIKO SEDANG → user approve] → "active"
     Kartu risiko menampilkan: "Prj : simuru → vps-app"
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
| `PROJECT_NAME` | — | **Baru** — Nama project (di-set via .conf) |
| `LOCK_CWD_TO_PROJECT` | `0` | `1` = kunci cwd ke PROJECT_ROOT |
| `ALLOWED_LOG_DIRS` | `/var/log,/var/www,...` | Folder yang boleh dibaca tail_log |
| `DEFAULT_TIMEOUT` | `180` | Timeout default (detik) |
| `MAX_TIMEOUT` | `900` | Timeout maksimum (detik) |
| `OUTPUT_LIMIT` | `20000` | Potong output panjang (karakter) |
| `AGENT_LOG_LEVEL` | `INFO` | Level log |
| `ODIN_ENV` | — | **Baru** — `production`/`prod`/`live` → mode production (sinyal eksplisit) |
| `CACHE_TTL_READ` | `60` | TTL cache hasil perintah READ (detik); `0` = matikan |
| `CONTEXT_BUDGET` | `5000` | Ambang pemotongan output ke head/tail (karakter) |
| `MEMORY_DIR` | `$ODIN_HOME/memory/<project>` (di-set `run.sh`) | Folder simpanan memory |
| `MEMORY_MAX_TEXT` | `4000` | Panjang maks teks satu entry |
| `MEMORY_MAX_ENTRIES` | `2000` | Ambang compaction (jumlah entry hidup) |
| `MEMORY_MAX_BYTES` | `8388608` | **Baru** — ukuran berkas pemicu compaction |
| `MEMORY_DEAD_RATIO` | `0.5` | **Baru** — rasio record mati pemicu compaction |
| `STALE_DAYS` | `30` | Umur entry sebelum ditandai `[STALE?]` |
| `DIGEST_BUDGET` | `3000` | Batas karakter memory digest saat startup |
| `EVENTS_DIGEST_HOURS` | `24` | Jendela cross-project event di digest |
| `LOG_ROTATE_BYTES` | `5242880` | **Baru** — rotasi `audit.jsonl` & `events.jsonl` |
| `AUDIT_ENABLED` | `1` | `0` = matikan audit log |
| `AUDIT_SYSLOG` | `1` | **Baru** — `0` = jangan cermin audit ke journald |
| `SERVER_ID` | — | machine-id server (di-seed `run.sh`) — anti mis-route |
| `ODIN_SKIP_INSPECT` | `0` | `1` = skip startup inspection (untuk testing) |

Sisi laptop (dipakai installer & CLI): `ODIN_HOME` (default `~/.odin`),
`ODIN_VERSION` (tag/branch yang dipasang), `ODIN_INSTALL_DIR` (kode, default `~/.odin/app`).

---

## Testing

```bash
python3 -m pytest tests/ -v          # full test suite (771 tests, ~18 detik)
python3 -m py_compile server/odin_agent.py client/odin_guard.py client/odin_cli.py
bash -n install.sh uninstall.sh server/run.sh server/odin-dispatch.sh
```

| File | Tests | Cakupan |
|---|---:|---|
| `test_guard.py` | 160 | READ/WRITE classifier, 23 sub-classifier, risk engine, kartu risiko |
| `test_core.py` | 59 | `_run` (process group, timeout, non-UTF-8, buffer terbatas), `_DANGER_RE`, cache |
| `test_memory.py` | 58 | Append/fold/tombstone/TTL, compaction, secret guard |
| `test_profile_mode.py` | 57 | Inspeksi, type detection, derivasi mode, enforcement |
| `test_output_intelligence.py` | 48 | 23 pola error, suggested_commands, recurring |
| `test_review_fixes.py` | 45 | **Regresi tiap temuan review v2.2** (K1–K5, T1–T7) |
| `test_cli.py` | 43 | Server/project CRUD, sync, global MCP, run.sh |
| `test_memory_improvements.py` | 42 | Semantic search TF-IDF, staleness, similarity |
| `test_cortex.py` | 40 | Global consciousness, event journal, routing |
| `test_fase4_proactive.py` | 38 | Watchdog, drift detection, deploy fingerprint |
| `test_fase3.py` | 36 | Runbook engine, rollback tracking |
| `test_fase3_ux.py` | 32 | Kartu risiko, undo hints, template |
| `test_fase2_intelligence.py` | 25 | Session history, pre-flight, audit |
| `test_learning.py` | 25 | Error→lesson, cross-session decay, success pattern |
| `test_guard_multiproject.py` | 24 | Project context, identitas di kartu, warning |
| `test_orchestrator.py` | 22 | enrich_context, suggest_next, check_attention |
| `test_installer.py` | 17 | **`install.sh` dijalankan sungguhan** (repo lokal + HOME sementara), migrasi tata letak lama, setup/self-update/uninstall/doctor |

`test_installer.py` bukan sekadar `bash -n`: installer benar-benar dieksekusi terhadap
clone `file://` repo ini dengan `HOME` sementara, lalu tata letak, wrapper, idempotensi,
dan **keutuhan private key saat migrasi** diverifikasi.

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
| Total kode | **~7382 baris** (server 3799 + cli 2381 + guard 895 + launcher 151 + updater 156) |
| Total test | **771 automated tests, 17 files** |
| CLI commands | 19 (server ×5, project ×6, global ×2, setup, update, self-update, doctor, uninstall, version) |
| Dependensi server | 1 (`mcp[cli]`) |
| Dependensi laptop CLI | 2 (`paramiko`, `pyyaml`) |
| MCP tools | **20** (termasuk cortex_log, cortex_events, memory_health) |
| MCP resources | 2 (`memory://{ns}`, `health://live`) |
| Error patterns | 23 (18 with suggested_commands) |
| Risk rules | 26 shell + DB assessor |
| Undo hint patterns | 12 |
| Runbook templates | 4 builtin + custom |
| READ sub-classifiers | 23 (git, docker, mysql, npm, curl, ufw, nginx, ...) |
| Risk tiers | 5 (AMAN / RENDAH / SEDANG / TINGGI / KRITIS) |
| Operation modes | 3 (setup / deploy / production) |
| Security layers | 5 (classifier → risk engine → hard-block → forced-command key → OS) |
| Memory namespaces | **4** (cortex: profile, cross; project: server, instruction) |
| **Learning loops** | **3** (error→lesson, cross-session tracking, success pattern) |
| **Orchestrator functions** | **4** (enrich_context, suggest_next, check_attention, auto_learn) |
| Framework | Tidak ada — pure Python + FastMCP |
| Overhead server | 0 (spawn-per-session, tidak ada daemon) |

---

## Keamanan

Batas sebenarnya = hak OS user `odin` + sudoers + forced-command pada kunci SSH.
Hook + `_DANGER_RE` = jaring pengaman, bukan sandbox. Keputusan akhir selalu di
operator (konfirmasi WRITE).

Filosofi: READ auto-approve, WRITE wajib konfirmasi, katastrofik double-brake. Guard lebih ketat dari server (by design).

**Project awareness** mengurangi risiko "salah project" — setiap risk card menampilkan `Prj : <name> → <server>` sebagai baris pertama. Guard warnings jika project tidak terdaftar.

---

## Lisensi & Versi

Versi aktif: **2.3.0** — tersimpan di `__version__` pada `server/odin_agent.py`,
`client/odin_guard.py`, dan `client/odin_cli.py` (ketiganya harus sinkron).
Lihat [CHANGELOG.md](CHANGELOG.md) untuk riwayat perubahan lengkap.

*ODIN v2.3 — multi-server, multi-project, workdir-based. Continuous learning. Orchestrator. Cortex consciousness. Project identity everywhere. Ringan, cerdas, belajar dari pengalaman.*