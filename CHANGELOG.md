# ODIN — Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

## [2.3.0] - 2026-09-08

Rilis perbaikan berdasarkan review keamanan & robustness eksternal atas v2.2.0.
Semua temuan **Kritis (K1–K5)** dan **Tinggi (T1–T7)** ditutup, plus sebagian besar
temuan Sedang dan drift dokumentasi, plus sistem instalasi baru. 771 test (dari 699), semuanya lulus.

### Added — Sistem instalasi baru (dua perintah dari nol sampai bekerja)

```
curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.sh | bash
odin setup
```

- **Kode dan state dipisah.** Kode di `~/.odin/app/` (checkout git), dependensi CLI di
  `~/.odin/app/.venv/`, wrapper di `~/.odin/bin/odin` + `~/.local/bin/odin`. State
  (`keys/ servers/ projects/ modes/ ssh_config`) tetap di `~/.odin/` dan **tidak pernah
  disentuh** installer maupun self-update — menutup akar T6 (dulu `mv ~/.odin` bisa
  menghilangkan private key). Pengguna ≤ v2.2 dimigrasikan otomatis: hanya file yang
  dilacak git yang dipindah; symlink `~/.odin/client` & `server` menjaga hook/MCP lama tetap valid.
- **Tanpa `sudo`.** `~/.local/bin` dipakai alih-alih `/usr/local/bin`; PATH ditambahkan ke
  rc shell (zsh/bash/fish) dengan izin. Symlink lama `/usr/local/bin/odin{,-update}` dibersihkan.
- **Versi ter-pin ke tag rilis** (GitHub Releases API, fallback `main`). `--version <ref>` /
  `ODIN_VERSION` untuk pin manual, `--home`, `--yes`, `--no-setup`. Verifikasi nyata di akhir
  (`odin --version` + import paramiko/pyyaml). Idempoten: jalankan lagi = update.
- **`odin setup`** — satu wizard idempoten: server → project (workdir = cwd) → Claude Code
  (MCP global + hook guard) → verifikasi handshake MCP. Tahap yang sudah beres dilewati.
- **`odin self-update [ref]`** — perbarui laptop ke tag rilis terbaru (atau ref apa pun,
  termasuk rollback); dependensi hanya di-install ulang bila `requirements-cli.txt` berubah;
  slash command ikut diperbarui. Menggantikan `odin-update`.
- **`odin uninstall [--purge]`** — cabut kode, wrapper, entry MCP, hook guard, allow-list,
  slash command; state dipertahankan kecuali `--purge` (konfirmasi ganda).
- **`odin doctor` tanpa alias** — diagnostik laptop: Python, paramiko/pyyaml, git/ssh/claude,
  tata letak, PATH, MCP global, hook, slash command, server & project (workdir hilang terdeteksi).
- **`odin version`** — versi + lokasi kode & state.
- `install.ps1`/`uninstall.ps1` (Windows) ditulis ulang mencerminkan tata letak yang sama
  (venv, `bin\odin.cmd`, PATH user, junction kompat). Belum diuji di mesin Windows —
  verifikasi dengan `odin doctor` setelah install.
- `tests/test_installer.py` (17 test) menjalankan **install.sh sungguhan** terhadap repo lokal
  (`file://`, HOME sementara): tata letak, wrapper, idempotensi, `--home`, ref tak dikenal,
  migrasi tata letak lama dengan state utuh; plus unit test setup/self-update/uninstall/doctor.

### Security — Kritis

- **K1 — Sudoers `tail -n * /var/log/*` dihapus.** sudo mencocokkan argumen dengan
  `fnmatch(3)` TANPA `FNM_PATHNAME`, jadi `*` ikut cocok dengan `/` dan `..`:
  `sudo tail -n 1 /var/log/../../etc/shadow` membaca shadow sebagai root, dan itu
  dapat dijangkau lewat `run_command` biasa. `tail_log` tetap jalan sebagai user `odin`.
- **K1b — `journalctl *` → `journalctl --no-pager *`.** Tanpa `--no-pager`, pager
  berjalan sebagai root dan `!/bin/sh` di dalamnya memberi shell root.
- **K2 — `certbot renew *` diganti aturan tanpa argumen bebas.** `certbot renew
  --deploy-hook='...'` mengeksekusi perintah arbitrer sebagai root.
- **K2b — `/usr/local/bin/*-deploy` dihapus** (wildcard nama = skrip apa pun).
- **Sudoers kini divalidasi `visudo -cf` sebelum dipasang, dengan rollback.**
  Sudoers rusak = seluruh `sudo` di server rusak.
- **K3 — Kunci SSH ODIN memakai forced-command.** `authorized_keys` ditulis sebagai
  `restrict,command="/home/odin/odin-dispatch.sh"`. Skrip baru `server/odin-dispatch.sh`
  hanya menerima `run.sh [--project <nama>]` (nama dibatasi `[A-Za-z0-9._-]`, metakarakter
  ditolak). Pemegang private key tak lagi mendapat shell interaktif ber-TTY — jalur
  eskalasi root lewat pager tertutup. Pemasangan kunci kini idempoten (tak lagi
  menumpuk duplikat tiap `server add`).
- **K4 — Guard: perintah pembungkus di-unwrap.** `env rm -rf /var/www/app` dulu
  diklasifikasi READ dan **dieksekusi tanpa konfirmasi**. `_unwrap()` kini melewati
  `env`/`nice`/`ionice`/`nohup`/`timeout`/`stdbuf`/`setsid`/`command`/`xargs`/`time`
  beserta flag & `VAR=val`-nya, lalu menilai perintah SEBENARNYA. Ikut diperbaiki:
  `sed --in-place` disamakan dengan `-i`, dan substitusi proses `<(`/`>(` memicu "ask".
  Ini juga menutup runbook yang auto-approve karena "semua langkah read-only".
- **K5 — `project add`/`sync` tak lagi menimpa hook user.** Dulu
  `settings["hooks"]["PreToolUse"] = [...]` (assignment) membuang hook `Bash` milik
  user tanpa peringatan. Sekarang memakai `_ensure_guard_hook()` yang merge per-matcher.

### Security — Tinggi

- **T7 — `_DANGER_RE` diperkuat & disinkronkan.** `_normalize_flags()` (ada di server
  DAN guard, harus tetap identik) menormalkan `rm -fr`, `rm -f -r`, dan
  `rm --recursive --force` menjadi `rm -rf` sebelum pencocokan. Pola baru: `poweroff`,
  `DROP SCHEMA`, `find / … -delete`, `rm -rf .`.
- **Matcher hook dilebarkan ke `mcp__odin__.*`.** Dengan matcher sempit, tool baru
  (mis. `cortex_log`, `memory_health`) diam-diam lolos tanpa penilaian guard. Guard
  sendiri yang memutuskan: `READ_ONLY_TOOLS` → allow, sisanya → kartu risiko.
- **Audit dicerminkan ke journald** (`AUDIT_SYSLOG=0` untuk mematikan). `audit.jsonl`
  milik user `odin` dan bisa di-truncate lewat `run_command` yang sama.

### Fixed — Robustness

- **T1 — Singleton tak lagi membunuh proses sembarangan.** PID diverifikasi sebagai
  proses `odin_agent.py` (`/proc/<pid>/cmdline`, fallback `ps`) sebelum sinyal dikirim,
  dan SIGKILL hanya setelah menunggu SIGTERM sampai ~15 detik (dulu 1,5 detik buta —
  sesi lain bisa mati di tengah `laravel_deploy`).
- **T2 — Mode `production` hanya dari sinyal eksplisit** (`APP_ENV=production` di `.env`
  aplikasi, atau `ODIN_ENV=production`). Uptime & disk bukan sinyal lingkungan; aturan
  lama membuat VPS staging berumur 8 hari kehilangan `laravel_deploy` dan
  `apt/npm/pip install`. `_PRODUCTION_BLOCKED_CMDS` juga kini menangkap `apt -y install`
  (flag sebelum sub-perintah).
- **T3 — Inspeksi startup tak lagi memblokir handshake MCP.** `_full_inspect()` (4
  perintah serial, timeout 30+15+30+15 detik) dulu jalan saat import, sebelum
  `FastMCP()` — jauh di atas batas koneksi MCP 30 detik. Kini dijadwalkan di thread
  latar; sesi langsung hidup dengan profil cache/`deploy`.
- **T4 — Timeout tak lagi meninggalkan orphan; output non-UTF-8 tak lagi jadi "ERROR".**
  `_run` memakai `Popen(start_new_session=True)` dan `killpg` seluruh process group saat
  timeout (dulu hanya wrapper `bash` yang mati — `composer install`/`npm ci` terus jalan).
  Dekode memakai `errors="replace"`, dan output dialirkan ke buffer head/tail terbatas
  alih-alih ditampung utuh di RAM.
- **T5 — `curl | bash` bisa lanjut ke `odin server add`.** Wizard dijalankan dengan
  `</dev/tty`; prompt `read` di `uninstall.sh` juga dialihkan ke `/dev/tty`.
- **T6 — Installer tak lagi memindahkan `~/.odin`.** Folder itu juga rumah state CLI
  (`keys/`, `servers/`, `projects/`, `modes/`); `mv` membuat private key hilang dari
  jalur yang masih ditunjuk `~/.ssh/config`. Kini clone dilakukan di tempat, state
  dipertahankan. `.gitignore` juga menolak `keys/ servers/ projects/ modes/ ssh_config`
  supaya `git add -A` di `~/.odin` tak bisa meng-commit private key.
- **PEP 668**: dependensi CLI jatuh ke venv `$INSTALL_DIR/.venv` bila Python sistem
  `externally-managed`, dan `odin` dipasang sebagai wrapper (bukan symlink) yang
  memakai interpreter itu.

### Fixed — Perilaku

- **Cache READ diinvalidasi oleh perintah WRITE.** `cat f` → `sed -i` → `cat f` dulu
  mengembalikan isi lama selama 60 detik.
- **`tail_log` tak lagi sukses palsu.** `set -o pipefail` + rc `grep`-tanpa-hasil
  dinormalkan ke 0; analisis pola error kini berjalan pada ISI log (`force=True`),
  bukan hanya saat exit ≠ 0 — `tail` yang berhasil selalu exit 0.
- **Compaction memory berbasis rasio/ukuran** (`MEMORY_DEAD_RATIO`, `MEMORY_MAX_BYTES`),
  di bawah `LOCK_EX`, memakai `mkstemp` di direktori yang sama. Pemicu lama (entri HIDUP
  > 2000) tak pernah tercapai lewat upsert sehingga histori mati menumpuk selamanya.
- **Baris JSONL terpotong diperbaiki saat append** (crash di tengah tulis dulu menelan
  record berikutnya).
- **Fold cache invalid lintas-proses** lewat `(mtime, size)` — sesi A kini melihat
  instruksi baru dari sesi B.
- **Auto-learning: decay + buang noise.** Hitungan error diparuh tiap sesi;
  `generic_failure`/`file_not_found` dikeluarkan dari pembelajaran (dulu setiap `grep`
  tanpa hasil ikut terhitung, dan `recurring` jadi status permanen). Lesson
  (`tag: error-lesson`) kini ikut di-recall `_enrich_context` — loop belajar tertutup.
- **`memory_health` tak lagi kuadratik** — indeks terbalik token menggantikan
  perbandingan semua-lawan-semua.
- **Rotasi `audit.jsonl` & `events.jsonl`** pada `LOG_ROTATE_BYTES` (default 5 MB).
- **`http_health_check` mengembalikan body** (dipotong 2000 byte), sesuai janji README.
- **Envelope error seragam** — penolakan membawa `error` DAN `stderr` dengan pesan sama.
- **Guard mengenali project pada konfigurasi MCP global.** `_detect_from_registry()`
  meresolusi project dari `cwd` lewat `~/.odin/projects/*` (aturan sama dengan
  `odin_mcp_launch.py`); tanpa ini identitas project hilang dari kartu risiko dan
  mode per-project tak terbaca sejak MCP dipindah ke scope-user.
- **`~/.odin/ssh_config` + `Include`** menggantikan penulisan langsung ke `~/.ssh/config`:
  cek tabrakan alias jadi EKSAK (dulu substring — `vps` dianggap ada karena ada
  `vps-app`), entry membawa `IdentitiesOnly yes` & `BatchMode yes`, dan `Include`
  ditaruh paling atas supaya tak kalah oleh blok `Host *` milik user.
- **`odin server add` memeriksa return code setiap langkah remote.** Dulu semua rc
  diabaikan: dengan admin tanpa NOPASSWD sudo semua langkah "berhasil" padahal gagal,
  lalu YAML & `~/.ssh/config` tetap ditulis. Kini gagal → config lokal tidak ditulis
  dan penyebabnya dilaporkan.
- **`odin update` aman**: backup ke `~/.backup/<ts>`, upload ke `.new`, compile-check
  (`py_compile` + `bash -n`), ganti atomik, lalu handshake MCP; gagal → dibatalkan.
- **`odin doctor` / `server test` melakukan handshake MCP sungguhan** (`initialize` +
  `tools/list` lewat `run.sh`), bukan sekadar `test -f`.
- **`run.sh`**: argumen tak dikenal & nama project tak valid ditolak; ≥2 project tanpa
  `--project` kini FATAL (dulu jatuh senyap ke `PROJECT_ROOT=/var/www/html`).

### Added

- **`odin server harden <alias>` — perbaikan K1–K3 untuk server yang SUDAH terpasang.**
  Tanpa ini rilis 2.3.0 hanya mengamankan server baru: `server add` melewati sudoers
  bila `/etc/sudoers.d/odin` sudah ada, dan `odin update` berjalan sebagai user `odin`
  yang tak berhak menulis `/etc/sudoers.d`. Perintah ini meminta kredensial admin,
  mem-backup sudoers lama, memasang aturan baru (tervalidasi `visudo -cf`), memasang
  dispatcher, lalu membatasi kunci ke forced-command — **diverifikasi lewat koneksi SSH
  BARU** (forced-command hanya berlaku saat autentikasi, jadi sesi berjalan tak
  membuktikan apa pun), dan `authorized_keys` dikembalikan bila verifikasi gagal
  sehingga tak ada risiko terkunci dari server.
- **`odin update` memigrasikan kunci ke forced-command** (user `odin` memiliki
  `authorized_keys`-nya sendiri) dengan verifikasi + rollback yang sama, dan
  **melaporkan** bila sudoers server masih rentan.
- **`odin doctor` mengaudit postur keamanan server**: pola sudoers rentan
  (`tail` berwildcard, `certbot renew *`, `*-deploy`, `journalctl` tanpa `--no-pager`)
  dan kunci tanpa forced-command.
- `server/odin-dispatch.sh` — dispatcher forced-command SSH.
- `odin server remove --purge` — cabut kunci ODIN dari `authorized_keys` server.
- `tests/test_review_fixes.py` (45 test) — regresi untuk setiap temuan di atas.
- Env baru: `ODIN_ENV`, `MEMORY_MAX_BYTES`, `MEMORY_DEAD_RATIO`, `LOG_ROTATE_BYTES`,
  `AUDIT_SYSLOG`.

### Removed

- **`migrate-to-v2.1.sh`** — menulis manifest dengan kunci yang tak dikenali CLI
  (`workdir`/`remote_path` vs `local_workdir`/`remote_root`), menimpa `settings.json`
  dengan format hook yang tak diterima Claude Code, dan hardcoded `simuru`/`vps-app`.
  Gunakan `odin project add --yes` / `odin project sync --all`.
- **`setup_full()` di `install.sh`** (~580 baris) — dead code, tak pernah dipanggil
  `main()`, dan men-generate `run.sh` lama yang mengabaikan `--project`.

### Changed

- Versi disinkronkan ke **2.3.0** di `odin_agent.py`, `odin_guard.py`, dan `odin_cli.py`
  (guard sebelumnya tertinggal di 2.1.0).
- Slash command `/odin:*` disalin ke `~/.claude/commands/odin/` oleh installer — dulu
  hanya bekerja bila Claude Code dijalankan dari dalam repo ODIN.
- `/odin:setup` tak lagi menyuruh mengedit `~/.claude.json` manual; ia memandu ke CLI.
- README & CLAUDE.md disinkronkan dengan keadaan sebenarnya (jumlah test, tools, baris,
  default `MEMORY_DIR`, sifat `LOCK_CWD_TO_PROJECT`, file yang di-gitignore).

## [2.2.0] - 2026-06-28

### Added — MCP Global (tersedia otomatis di tiap project)

**`odin global enable [--migrate]` / `odin global disable` — perintah baru:**
- Memasang ODIN MCP di **scope-user** (`~/.claude/settings.json`) sehingga `mcp__odin__*` muncul **otomatis di setiap project terdaftar** tanpa entry per-repo.
- Satu entry global memanggil launcher `client/odin_mcp_launch.py` yang **meresolusi project+server dari `cwd` di waktu-spawn** (cocokkan ke `local_workdir`, dukung subdir via longest-prefix). Dir yang bukan project terdaftar ditolak bersih (exit 1) tanpa spawn SSH.
- Penulisan global **anti-clobber**: hook & permission milik user dipertahankan (hook guard di-merge per-matcher, allow read-only di-dedup).
- `--migrate` mencabut jejak `odin` per-workdir lama (mcpServers + hook + allow) → global jadi satu-satunya sumber, tanpa definisi/hook ganda.
- `_detect` (launcher) me-resolve `local_workdir` simbolik agar cocok dengan `cwd` yang ter-resolve.

### Added — Kemudahan Multi-Project (CLI)

**`odin project add` — mode non-interaktif (flags):**
- Flag baru: `--name`, `--server`, `--remote-root`, `--workdir`, dan `-y/--yes`.
- Memungkinkan registrasi batch / scripting tanpa prompt, mis.:
  ```sh
  odin project add --name ekampus --server gibtha_srv \
      --remote-root /var/www/ekampus --workdir ~/PROJECTS/eKAMPUS --yes
  ```
- Tanpa `--yes` tetap interaktif; flag yang diisi melewati prompt terkait. Di mode `--yes`, field wajib yang kosong gagal jelas (exit code 2) alih-alih menggantung.

**`odin project sync` — perintah baru:**
- `odin project sync [name] [--all]` meregenerasi config lokal (`.claude/settings.json`) dari manifest `~/.odin/projects/*.yaml`.
- Pemulihan satu-perintah saat `settings.json` terhapus, atau saat **repo ODIN dipindah** (guard-path absolut di hook diperbarui otomatis).

### Changed

- **Satu sumber kebenaran config lokal: `.claude/settings.json`.** `project add` & `sync` kini **memigrasikan** entry `odin` dari `.mcp.json` format lama (server MCP lain dipertahankan; file dihapus bila kosong) → tidak ada lagi definisi MCP ganda/drift.
- `project remove` membersihkan entry `odin` di **kedua** lokasi (`.claude/settings.json` + `.mcp.json`) → tidak ada entry yatim.
- `project status` mendeteksi entry MCP `odin` di kedua lokasi (tak lagi false-FAIL untuk project format lama) dan menampilkan sumbernya.
- CLI version diselaraskan ke **2.1.0** (sebelumnya keliru `2.0.0`); `odin --version` membaca versi dari agent.

### Security

- `remote-root` divalidasi (harus path absolut tanpa karakter shell) dan di-`shlex.quote` saat dikirim ke server → menutup celah command-injection lewat path project.

### Tests

- +9 unit test (validasi remote_root, migrasi/deteksi `.mcp.json`, `project add` non-interaktif, `project sync`). Total suite: **690 passed, 1 skipped**.

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
