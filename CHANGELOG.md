# ODIN — Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

---

## [2.0.0] — 2026-06-15

### Added — Multi-Project Support & Project Awareness

**CLI Multi-Server & Multi-Project** (`client/odin_cli.py`, 985 baris):
- `odin server add` — setup server baru secara interaktif (SSH, user odin, venv, mcp[cli], SSH key)
- `odin server list|remove|test` — kelola server terdaftar
- `odin project add` — link workdir lokal ke server:project (auto-generate `.claude/settings.json`)
- `odin project list` — daftar project terdaftar dengan marker `→` untuk project aktif + kolom Mode
- `odin project status [name]` — **BARU**: validasi project (config lokal + SSH ping server)
- `odin project switch <name>` — **BARU**: buka tab Terminal baru di workdir project (macOS: `osascript`, fallback: print `cd` command)
- `odin project remove <name>` — hapus project config
- `odin update <alias>` — update `odin_agent.py` + `run.sh` di server
- `odin doctor <alias>` — diagnostik server lengkap
- Total CLI commands: **11** (naik dari 9)

**Project Identity Everywhere**:
- **Risk cards** menampilkan project identity sebagai **baris PERTAMA**: `Prj   : <name> → <server>`
- **Service cards** menampilkan project identity
- **Guard warnings**: jika project tidak terdaftar di `~/.odin/projects/`, kartu risiko menampilkan `⚠ Project '<name>' tidak terdaftar`
- **Skill `/odin:status`** menampilkan project sebagai baris pertama: `Project   : <name> → <server> (<mode>)`
- **`server_info` tool** mengembalikan `project_name` field
- **Audit log** mencatat `project` field per record

**Multi-Project Infrastructure**:
- **Multi-project `run.sh`**: terima `--project <name>`, source dari `projects/<name>.conf`, export `PROJECT_NAME` env var
- **Per-project memory isolation**: `memory/<project>/` di server — setiap project punya `memory.jsonl` dan `audit.jsonl` sendiri
- **Per-project mode**: `~/.odin/modes/<project>` di laptop — mode operasi terisolasi per project
- **Workdir-based auto-switching**: `.claude/settings.json` per workdir — switch project = pindah folder, buka Claude Code baru
- **SSH key per server**: `~/.odin/keys/<alias>` — auto-generate ed25519 key pair saat setup
- **Config registry**: `~/.odin/servers/`, `~/.odin/projects/` (YAML/JSON)
- **Dependency CLI** (laptop saja): `paramiko>=3.0`, `pyyaml>=6.0` (`requirements-cli.txt`)

**Guard Project Awareness** (`client/odin_guard.py`, 720 baris):
- `_detect_project_context()` → tuple `(project_name, server_alias)` dari `.claude/settings.json`
- `_detect_project()` refactored sebagai thin wrapper ke `_detect_project_context()`
- `_warn_project_mismatch(project)` cek apakah project terdaftar di `~/.odin/projects/`
- `risk_card()` dan `service_card()` menerima `project=""` dan `server=""` params
- Semua inline card builders (laravel_deploy, memory_write, memory_forget, runbook) inject Prj line
- `_get_mode()` dan `_sync_mode_from_result()` per-project (dari `~/.odin/modes/<project>`)

**Server-Side** (`server/odin_agent.py`, 2335 baris):
- `PROJECT_NAME` global var dari `os.environ.get("PROJECT_NAME", "")`
- `server_info()` mengembalikan `project_name` field
- `_audit()` mencatat `project` field (atau `None` jika kosong)
- Startup log mencantumkan `PROJECT_NAME`

**Tests Baru** (536 total, naik dari 513):
- `test_guard_multiproject.py`: 24 tests (naik dari 13) — `TestDetectProjectContext` (3), `TestProjectInRiskCard` (4), `TestWarnProjectMismatch` (4)
- `test_cli.py`: 24 tests (naik dari 15) — `TestDetectCurrentProject` (3), `TestProjectListEnhanced` (1), `TestProjectStatus` (2), `TestProjectSwitch` (2), `TestRunShProjectName` (1)
- `test_core.py`: 48 tests (naik dari 44) — `TestProjectName` (4): PROJECT_NAME default, server_info, audit dengan project, audit tanpa project

### Changed

- `run.sh`: tambah `export PROJECT_NAME="${PROJECT_NAME:-}"` setelah source `.conf`
- `odin_guard.py`: 686 → 720 baris (+34) — project identity di semua risk/service cards
- `odin_cli.py`: 865 → 985 baris (+120) — `_detect_current_project()`, enhanced list, status, switch
- `install.sh`: integrasi `install_cli_deps()` + `install_cli_command()` untuk CLI setup
- `.claude/commands/odin.md`: update banner version dari v1.1 → v2.0
- `.claude/commands/odin/status.md`: rewrite untuk menampilkan project sebagai baris pertama

### Unchanged

- Security model 4-layer: tidak berubah
- Memory format (JSONL): tidak berubah
- Semua 17 MCP tools: tidak berubah
- Core logic `odin_agent.py`: hanya tambah ~5 baris untuk expose project identity
- 512 existing tests: tidak ada regresi (536 total = 512 + 24 baru)

---

## [1.3.0] — 2026-06-10

### Added — Token Optimization (3 inovasi)

- **Inovasi 1 — Result Cache TTL** (`CACHE_TTL_READ`, default 60 s):
  - `_READ_ONLY_RE` mencocokkan ~20 jenis perintah read-only (df, ps, git status, systemctl status, cat, ls, dll.)
  - `_cache_get()`: kembalikan salinan cached result + metadata `_cached` / `_cache_age_sec`
  - `_cache_set()`: simpan hasil sukses post-filter; cache key = `(command, cwd)`
  - `run_command`: cache-check sebelum spawn subprocess; hit → return `_slim(cached)` langsung
  - `CACHE_TTL_READ=0` menonaktifkan cache sepenuhnya

- **Inovasi 2 — Slim Envelope** (strip field konstan untuk READ command):
  - `_WRITE_CMDS_RE`: deteksi 25+ pola perintah WRITE (systemctl restart, git pull, mv, rm, mysql*, composer install, apt install, dll.)
  - `_slim()`: READ sukses → strip `mode` / `agent_mode` / `ssh_target` (konstan di local) + `stderr` kosong + `command`; WRITE/GAGAL → kembalikan envelope penuh untuk audit trail
  - Estimasi hemat ~25–40 token per READ call; berlaku juga untuk cache hit

- **Inovasi 3 — Domain-aware Output Filter** (ganti naive head+tail):
  - `_filter_ps`: `ps aux` → header + baris service dikenal (`_SERVICES_KNOWN`) + user www-data/odin/deploy
  - `_filter_journal`: `journalctl` → hanya baris mengandung ERROR/WARN/FAIL/CRIT/EMERG
  - `_filter_git_log`: `git log` → batasi 40 baris
  - `_filter_find`: `find` → batasi 60 hasil
  - `_filter_env`: `env`/`printenv` → buang baris panjang/binary, batasi 50 baris
  - `_smart_output`: jalankan domain filter dulu; jika masih > `CONTEXT_BUDGET` → head+tail fallback; `_output_meta` kini mencantumkan `filter` yang dipakai

### Fixed

- **Singleton guard + SSH watchdog** (dari v1.2.1, dipisah entri):
  - PID file `MEMORY_DIR/odin_agent.pid`: instance baru SIGTERM→SIGKILL instance lama (new instance always wins)
  - Watchdog thread: probe parent PID (sshd) tiap 10 detik; jika mati → exit + cleanup PID file
  - `atexit` + `SIGTERM` handler untuk cleanup PID file saat exit normal

### Changed

- `odin_agent.py`: 2046 → 2335 baris (+289)
- `odin_guard.py`: versi sinkron 1.2.0 → 1.3.0
- Env var baru: `CACHE_TTL_READ` (default 60)
- Import baru di `odin_agent.py`: `atexit`, `signal`, `threading`

---

## [1.2.1] — 2026-06-10

### Fixed

- **Singleton guard**: PID file di `MEMORY_DIR/odin_agent.pid`; setiap instance baru membunuh instance lama (SIGTERM → 1.5 s → SIGKILL) sebelum start. Mencegah akumulasi orphan process saat koneksi MCP drop + reconnect berulang.
- **SSH watchdog thread**: probe `os.kill(parent_pid, 0)` tiap 10 detik; jika parent sshd mati (SSH drop mendadak tanpa EOF), proses ODIN exit otomatis + cleanup PID file.
- **Logging startup**: `log.info` kini mencantumkan `pid` dan `ppid` untuk debugging koneksi.

### Root cause

Ditemukan 8 orphan `odin_agent.py` berjalan bersamaan (+ 1 PHP runaway 200% CPU). Setiap sesi MCP baru men-spawn proses baru via SSH tanpa mekanisme cleanup proses lama. `mcp.run(transport="stdio")` tidak selalu exit saat SSH drop mendadak (stdin tidak selalu mengirim EOF).

---

## [1.2.0] — 2026-06-08

### Added — Fase 1: Safety Net

- **Fix `_strip_quotes()`**: handle ANSI-C quoting (`$'...'`) sebelum quote stripping biasa
- **Test coverage masif**: dari 80 test → 485 test (10 file), mencakup seluruh subsistem

### Added — Fase 2: Intelligence Core

- **Command suggestions**: 18 dari 23 error pattern kini menyertakan `suggested_commands` (daftar perintah + level risiko)
- **Error frequency tracking**: per-session `_error_counts`, flag `recurring` + `recurring_hint` saat error >= 3x
- **Deploy config persistence**: auto-save konfigurasi deploy terakhir ke memory, auto-load sesi berikutnya
- **Trend detection**: ring-buffer metrik (max 7 snapshot), `_compute_trend` membandingkan current vs oldest

### Added — Fase 3: UX & Tooling

- **`audit_tail` tool**: baca audit log dengan filter (last, tool, success, since)
- **Risk card undo hints**: 12 `_UNDO_PATTERNS` menambahkan baris "Undo: ..." di kartu risiko
- **`runbook_templates` tool**: 4 template builtin (ssl-renew, db-backup, log-cleanup, health-check) + custom dari memory

### Added — Fase 4: Proactive Intelligence

- **Deploy fingerprint & drift detection**: simpan fingerprint setelah deploy (git hash, composer.lock md5, migration count, .env lines), deteksi drift di preflight berikutnya
- **Context window budget**: `_smart_output()` memotong stdout > 5000 char menjadi head+tail + `_output_meta`
- **Watchdog resource `health://live`**: health check ringkas (disk, memory, load, services) untuk polling via `/loop`

### Changed

- `odin_agent.py`: 1632 → 2046 baris
- `odin_guard.py`: 616 → 651 baris
- MCP tools: 15 → 17
- MCP resources: 1 → 2
- Test: 80 → 485 (10 files, 3545 baris)

---

## [1.1.0] — 2026-06-07

### Added

- **Slash sub-commands**: 6 sub-command `/odin:help`, `/odin:about`, `/odin:status`, `/odin:doctor`, `/odin:check-update`, `/odin:setup`
- `/odin:about` — penjelasan fitur ODIN (17 tools, 4 lapis keamanan, cara pakai)
- `/odin:status` — quick status server (disk, memory, uptime, services) + load memory
- `/odin:doctor` — diagnostik 5 langkah (file lokal, config MCP, guard hook, SSH, MCP server)
- `/odin:check-update` — cek versi terbaru via `update_checker.py` atau git fetch
- `/odin:setup` — rekonfigurasi interaktif MCP config & guard hook dari dalam sesi Claude Code

### Changed

- `/odin` disederhanakan: hanya banner + hint help (tanpa MCP call, lebih cepat)
- `__version__` dinaikkan dari `1.0.0` ke `1.1.0` di kedua file Python
- Header SVG dan README badges diupdate ke v1.1

---

## [1.0.0] — 2026-06-07

Rilis stabil pertama. Seluruh fitur inti lengkap, teruji (80 test), dan terdokumentasi menyeluruh. Promosi dari v0.9.0 setelah validasi arsitektur, review teknis 4 dimensi, dan analisis dampak.

### Changed

- **Versi**: `__version__` di kedua file Python dinaikkan dari `0.9.0` ke `1.0.0`
- **Dokumentasi**: Seluruh file dokumentasi ditulis ulang menjadi lengkap dan informatif:
  - `README.md` — panduan lengkap: arsitektur, 15 tools, keamanan 4 lapis, instalasi, env vars, cara kerja end-to-end
  - `CLAUDE.md` — project instructions diperkaya: detail per-tool approval, 23 sub-classifiers, memory system, semua subsistem
  - `EXECUTIVE_SUMMARY.md` — rangkuman eksekutif dengan alur interaksi penuh (User → Claude → Guard → Server → VPS)
  - `REVIEW_EMPAT_FAKTOR.md` — review teknis 4 dimensi (kompleksitas, kemudahan, kepintaran, keamanan)
  - `docs/MEMORY_NOTES.md` — dokumentasi sistem memory dengan detail storage, fold, compaction, safety

### Summary Fitur v1.0

Berikut seluruh kapabilitas yang tersedia di ODIN v1.0:

**15 MCP Tools**: `run_command`, `tail_log`, `service_action`, `laravel_deploy`, `run_tests`, `http_health_check`, `server_info`, `inspect_server`, `session_history`, `runbook`, `rollback_plan`, `memory_write`, `memory_recall`, `memory_forget`, `memory_digest`

**1 MCP Resource**: `memory://{ns}`

**Keamanan 4 Lapis**: READ/WRITE classifier (23 sub-command classifiers) → Risk engine (26 shell rules + DB assessor, 5 tier) → Hard-block katastrofik (`_DANGER_RE`) → OS user permissions

**Kecerdasan**: Output intelligence (22 error patterns), rollback tracking, runbook engine (maks 20 step), pre-flight deploy checks, server profiler + auto-mode (setup/deploy/production)

**Memory**: Append-only JSONL, 3 namespace (server/instruction/profile), fold + TTL + tombstone + compaction, secret guard, auto-inject ke konteks tiap sesi

**Audit**: Append-only `audit.jsonl`, setiap tool execution tercatat

**Installer**: `install.sh` (857 baris, macOS & Linux) — wizard laptop + setup server via SSH (ControlMaster). `install.ps1` (Windows). `uninstall.sh` / `uninstall.ps1` — auto-clean config + server cleanup. `odin-update` command

**Metrik**: 2318 baris source + 758 baris test = 3076 total. 1 dependensi (`mcp[cli]`). 80 automated tests.

- **Rename user/path**: Seluruh referensi user `deploy` + path `/home/deploy/agent/` diganti ke user `odin` + path `/home/odin/` (server, client, installer, docs, CLAUDE.md, README.md)
- **Setup wizard**: `install.sh` menambah wizard interaktif — 3 pertanyaan (SSH host, path run.sh, scope guard), tes koneksi SSH, tulis config otomatis ke `~/.claude.json` dan `settings.json`
- **Server installer via SSH**: `install.sh` menawarkan setup server setelah laptop selesai — buat user odin, venv, upload file, generate run.sh, set password. SSH ControlMaster untuk satu kali auth
- **Installer Windows**: `install.ps1` (PowerShell) dengan panduan konfigurasi lengkap termasuk permissions allow list dan venv setup
- **Uninstaller auto-clean**: `uninstall.sh` dan `uninstall.ps1` membersihkan `mcpServers.odin` dari `~/.claude.json`, hook/permissions `mcp__odin__` dari semua `settings.json`, `~/.odin_mode`, dan menawarkan cleanup server via SSH
- **Slash command `/odin`**: banner ASCII art + auto-load server status dan memory digest
- **Guard docstring**: versi diperbarui dari `v0.9.0` ke `v1.0.0`
- **FULL_REVIEW.md**: analisis teknis 9 dimensi (konsistensi, validitas, optimasi, fitur, kecerdasan, kemudahan, keamanan, kualitas kode, prioritas)

---

## [0.9.0] — 2026-06-06

Rilis pertama dengan nama ODIN. Seluruh fitur inti lengkap dan teruji (80 test).

### Added

- **Server Profile & Auto-Mode** (Fase 4)
  - `inspect_server` tool — full inspection on-demand
  - Startup auto-inspection: OS, kernel, disk, memory, firewall, SSH, cron, users
  - Type detection: `web-app`, `database`, `container`, `general`
  - Stack-specific deep scan: web (nginx/apache/PHP/FPM/composer/node/DB/Redis/SSL), database (MySQL/PG/Mongo/backups), container (Docker/compose)
  - App inspection: .env, vendor, framework detection (Laravel/Django/Node), git state
  - Operation mode derivation: `setup` / `deploy` / `production` — dari data, bukan manual
  - Mode override via memory (`server:mode-override`)
  - Dual enforcement: server `_mode_gate` blocks + guard `_shift_tier` risk escalation
  - Profile summary persisted to memory (`server:stack-profile`, pinned)
  - `ODIN_SKIP_INSPECT=1` env var for testing

- **Runbook Engine** (Fase 3)
  - `runbook` tool — multi-step workflow execution (max 20 steps)
  - Per-step error analysis and rollback tracking
  - `continue_on_fail` per step
  - Guard: risk tier = max write step; all-read runbooks auto-allow

- **Rollback Tracking** (Fase 3)
  - `_capture_pre_state` before destructive commands (git, migrate, service)
  - `_suggest_rollback` generates undo commands from captured state
  - `rollback_plan` tool — actionable rollback suggestions from session history
  - `_rollback_hint` attached to tool results

- **Output Intelligence** (Fase 2)
  - 22 error patterns: DB (SQLSTATE, deadlock, max conn), PHP/Laravel (fatal, OOM, timeout), system (disk full, OOM kill, permission denied), tools (nginx, SSL, npm)
  - `_analyze_output()` attaches `_analysis` to failed commands

- **Session History** (Fase 2)
  - `session_history` tool — in-memory log of all tool executions per session

- **Pre-flight Checks** (Fase 2)
  - `_preflight_deploy` — disk, git dirty, commit, PHP version check before deploy
  - Blockers abort deploy with report

- **Audit Log** (Fase 2)
  - Append-only `audit.jsonl` — every tool execution recorded
  - Disable with `AUDIT_ENABLED=0`

- **Memory System** (Fase 0)
  - 3 namespaces: `server`, `instruction`, `profile`
  - 4 tools: `memory_write`, `memory_recall`, `memory_forget`, `memory_digest`
  - Append-only JSONL + fold + TTL + tombstone + compaction
  - Secret detection (password, token, private key, JWT, AWS key)
  - Auto-inject digest to FastMCP instructions on startup

- **Core Tools** (Fase 0)
  - `run_command` — shell execution with READ/WRITE classification
  - `tail_log` — log file reader with allowed dirs
  - `service_action` — systemd management (status/restart/reload/start/stop)
  - `laravel_deploy` — one-button Laravel deploy with pre-flight
  - `run_tests` — PHPUnit/Pest test runner
  - `http_health_check` — HTTP status verification
  - `server_info` — server summary

- **Security Model** (Fase 0-1)
  - PreToolUse guard with READ/WRITE classifier (`seg_is_read`)
  - 23 sub-command classifiers (git, docker, mysql, npm, curl, ufw, nginx, etc.)
  - Risk engine: 5-tier cards (AMAN/RENDAH/SEDANG/TINGGI/KRITIS) + 26 shell rules
  - `_DANGER_RE` hard-block for catastrophic commands (server-side)
  - Command substitution (`$()`, backticks) detection — forces "ask"
  - DB read/write classification (SELECT/SHOW → allow, DML/DDL → ask)
  - Production mode: tier shift +1, `MODE PRODUCTION` warning on risk cards

- **Installer**
  - `install.sh` — cross-platform installer (macOS & Linux)
  - `uninstall.sh` — clean uninstaller
  - `odin-update` command via symlink

- **Versioning**
  - `__version__` constant in both Python files
  - `CHANGELOG.md` for tracking changes

### Changed

- MCP server name: `deploy-agent` → `odin` (tool prefix: `mcp__odin__`)
- Logger name: `deploy-agent` → `odin`
- Memory digest header: `MEMORY deploy-agent` → `MEMORY ODIN`
- All documentation updated with ODIN branding

---

## [0.0.0] — 2026-05-xx

Initial commit. Bare MCP server skeleton.
