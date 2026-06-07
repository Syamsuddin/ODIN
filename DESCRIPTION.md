# ODIN — Deskripsi Lengkap

## Apa Itu ODIN

**ODIN (v1.2)** adalah **MCP Agent AI untuk server Linux** — sebuah jembatan yang menghubungkan Claude Code di laptop (sebagai "otak") dengan server Linux live (sebagai "tangan"). Seluruh sistem terdiri dari **2 file Python + 1 launcher Bash**, dengan satu-satunya dependensi runtime: `mcp[cli]`. Tidak ada framework, tidak ada build step, tidak ada daemon.

Codebase dan seluruh komentar/docstring ditulis dalam **Bahasa Indonesia**.

---

## Arsitektur: Dua Sisi, Satu Protokol

```
LAPTOP (Claude Code)                       SERVER (VPS, user: odin)
┌────────────────────────┐  SSH stdio MCP  ┌──────────────────────────┐
│ client/                │ ──────────────▶ │ server/                  │
│   odin_guard.py (651)  │  spawned fresh  │   odin_agent.py (2046)   │
│   update_checker.py    │  per session    │   run.sh (launcher)      │
│   (PreToolUse hook)    │                 │   memory/ (JSONL)        │
│   Risk Engine + Gate   │                 │   audit.jsonl            │
└────────────────────────┘                 └──────────────────────────┘
```

Koneksi: Claude Code spawn `ssh vps-app /home/odin/run.sh` yang mengaktifkan MCP server via stdio transport. Sesi selesai, server mati. Setiap sesi = proses baru, tanpa state in-memory yang bertahan (kecuali memory di disk).

---

## Komponen 1: `server/odin_agent.py` (2046 baris)

Ini adalah **otak operasional** — FastMCP server yang berjalan di VPS sebagai user `odin`.

### 17 MCP Tools

| # | Tool | Fungsi | Approval |
|---|------|--------|----------|
| 1 | `run_command` | Primitif serbaguna — jalankan satu perintah shell | READ: auto, WRITE: risk card |
| 2 | `tail_log` | Baca N baris terakhir file log (path dibatasi ALLOWED_LOG_DIRS) | Auto-allow |
| 3 | `service_action` | Kelola service systemd (status/restart/stop/reload) | status: auto, lainnya: risk card |
| 4 | `laravel_deploy` | Deploy Laravel end-to-end (git, composer, migrate, cache, fpm) | Risk card TINGGI |
| 5 | `run_tests` | Jalankan test suite (PHPUnit/Pest) | Auto-allow |
| 6 | `http_health_check` | Health check via curl (verifikasi pasca-deploy) | Auto-allow |
| 7 | `server_info` | Ringkasan server: kernel, disk, memori, PHP | Auto-allow |
| 8 | `session_history` | Riwayat tool execution sesi ini (in-memory) | Auto-allow |
| 9 | `inspect_server` | Inspeksi ulang server, derive mode operasi | Auto-allow |
| 10 | `runbook` | Eksekusi multi-step workflow berurutan (maks 20 langkah) | Risk card tertinggi |
| 11 | `runbook_templates` | Daftar/ambil template runbook (builtin + custom) | Auto-allow |
| 12 | `rollback_plan` | Saran rollback berdasarkan pre-state yang ditangkap | Auto-allow |
| 13 | `memory_write` | Simpan fakta/arahan ke memory persisten (upsert by ns+key) | Risk card RENDAH |
| 14 | `memory_recall` | Cari/ambil memory persisten (filter ns/query/tag) | Auto-allow |
| 15 | `memory_forget` | Hapus memory secara logis (tombstone) | Risk card RENDAH |
| 16 | `memory_digest` | Ringkasan memory yang di-inject ke konteks startup | Auto-allow |
| 17 | `audit_tail` | Baca audit log lintas-sesi (forensik) | Auto-allow |

### 2 MCP Resources

- **`memory://{ns}`** — read-only view memory per namespace
- **`health://live`** — watchdog ringkas (disk, memory, load, service status) untuk polling via `/loop`

### Subsistem Dalam Server

#### a) Command Execution Engine

- **`_build_invocation()`** — konstruksi argv untuk local (`bash -lc`) atau ssh
- **`_run()`** — eksekusi subprocess dengan timeout, truncate output, hard-block via `_DANGER_RE`
- **`_smart_output()`** — jika stdout > 5000 chars (CONTEXT_BUDGET), ganti dengan head 5 + tail 10 baris + metadata
- **`_truncate()`** — jika output > 20000 chars (OUTPUT_LIMIT), potong tengah

Dua mode eksekusi:
- **local**: perintah dijalankan di mesin yang sama dengan server (`bash -lc`)
- **ssh**: perintah dijalankan di server remote lewat binary `ssh` (tanpa lib tambahan)

#### b) Output Intelligence (23 Error Patterns)

Saat command gagal (exit != 0), `_analyze_output()` scan stdout+stderr terhadap 23 pola error yang diurutkan spesifik-lebih-dulu (SQLSTATE sebelum generic "Connection refused"):

**Database:**

| Pola | error_type | Contoh |
|------|-----------|--------|
| `SQLSTATE[HY000] [1045]` / `Access denied` | `db_auth` | Autentikasi DB gagal |
| `SQLSTATE[HY000] [2002]` / `Can't connect` | `db_conn` | Tidak bisa konek ke database |
| `SQLSTATE[42S02]` / `Table doesn't exist` | `db_table_missing` | Tabel DB tidak ada |
| `SQLSTATE[42S22]` / `Unknown column` | `db_column_missing` | Kolom DB tidak ada |
| `SQLSTATE[23000]` / `Duplicate entry` | `db_constraint` | Pelanggaran constraint |
| `Too many connections` | `db_max_conn` | Koneksi DB penuh |
| `lock wait timeout` / `Deadlock found` | `db_lock` | Deadlock/lock timeout |
| `SQLSTATE` (generik) | `db_error` | Error database umum |

**PHP / Laravel:**

| Pola | error_type | Contoh |
|------|-----------|--------|
| `Class not found` / `ReflectionException` | `class_not_found` | Class PHP tidak ditemukan |
| `PHP Fatal error` / `Uncaught Exception` | `php_fatal` | Error fatal PHP |
| `Allowed memory size exhausted` | `php_oom` | PHP kehabisan memori |
| `max_execution_time exceeded` | `php_timeout` | Timeout PHP |
| `Composer detected issues` | `composer_lock` | composer.lock tidak sinkron |

**Sistem:**

| Pola | error_type | Contoh |
|------|-----------|--------|
| `No space left on device` | `disk_full` | Disk penuh |
| `Out of memory` / `oom-kill` / `SIGKILL` | `killed` | Proses di-kill (OOM) |
| `Permission denied` | `permission` | Akses ditolak |
| `command not found` | `missing_cmd` | Perintah tidak ditemukan |
| `Connection refused` | `conn_refused` | Koneksi ditolak |
| `No such file or directory` | `file_not_found` | File tidak ditemukan |
| `Address already in use` | `port_in_use` | Port sudah terpakai |

**Tool-specific:**

| Pola | error_type | Contoh |
|------|-----------|--------|
| `nginx: [emerg]` / `test failed` | `nginx_config` | Config Nginx error |
| `SSL error` / `certificate expired` | `ssl` | Masalah SSL |
| `npm ERR!` | `npm_error` | Error npm |

18 dari 23 pola menyertakan `suggested_commands` — daftar perintah follow-up yang actionable beserta tier risikonya.

**Error frequency tracking**: per-session `_error_counts` dict. Ketika `error_type` yang sama muncul >= 3x, `_analysis` menyertakan `recurring: true` + `recurring_hint` yang merekomendasikan investigasi root cause.

#### c) Memory System

Penyimpanan persisten di `MEMORY_DIR` (default `/home/odin/memory/`), **sengaja di luar PROJECT_ROOT** agar tidak kena `git reset --hard` saat deploy dan tidak bisa diakses via `run_command` atau `tail_log`.

- **Storage**: append-only JSONL (event log)
- **Fold**: last-write-wins per `id` (`ns:slug(key)`), tombstone untuk delete, TTL via `expires_at`
- **Concurrency**: `O_APPEND` + `fcntl.flock` — aman concurrent write
- **Compaction**: atomic (temp + `os.replace`) ketika entry melebihi `MEMORY_MAX_ENTRIES` (2000)
- **Fold cache**: `_fold_cache` dalam sesi, otomatis invalidate saat `_mem_append()` atau `_mem_compact()`
- **3 namespace** (allowlist ketat):

| Namespace | Isi | Sumber |
|-----------|-----|--------|
| `server` | Fakta infrastruktur: nama service, versi, quirk deploy | Agent (selama operasi) |
| `instruction` | Arahan/preferensi durable dari user | Dari percakapan |
| `profile` | Identitas user (nama, peran, kontak) | Sekali, jarang berubah |

- **Startup injection**: `_build_memory_digest()` inject active memory ke `FastMCP(instructions=...)` sehingga memory otomatis termuat di konteks setiap sesi baru. Digest menampilkan: PROFIL USER, INSTRUKSI DURABLE DARI USER, dan FAKTA SERVER (yang di-pin).
- **Secret guard**: tolak nilai matching pola berikut (kecuali `allow_secret=True`):
  - `-----BEGIN PRIVATE KEY-----`
  - `password|token|api_key = <value>`
  - AWS access key (`AKIA...`)
  - GitHub token (`ghp_...`)
  - JWT (`eyJ...`)
- **Permissions**: dir `700`, file `600`

#### d) Server Profile & Operation Mode

Saat startup, ODIN melakukan inspeksi atau pakai cache:

**1. `_try_cached_startup()`** — cek apakah `server:stack-profile` di memory masih segar (< 1 jam). Jika ya, muat mode dan type dari cache — skip inspeksi penuh.

**2. `_full_inspect()`** — pipeline inspeksi penuh (dijalankan jika cache kadaluarsa atau on-demand via `inspect_server`):

| Tahap | Fungsi | Detail |
|-------|--------|--------|
| 1 | `_inspect_base()` | OS, kernel, uptime, disk, memory, firewall, fail2ban, SSH config, cron, users. Satu command batch dengan `@@MARKER@@` delimiter, diparse oleh `_parse_sections()` |
| 2 | `_detect_type()` | Cek `which nginx/php/mysql/docker/etc.` untuk classify server |
| 3 | Stack inspection | Per-tipe deep scan (lihat tabel di bawah) |
| 4 | `_inspect_app()` | Cek .env, vendor, node_modules, framework markers (artisan/manage.py/package.json), git state |
| 5 | `_derive_mode()` | Tentukan mode operasi dari profile data |

**Tipe server dan inspeksi stack:**

| Tipe | Deteksi | Stack yang diperiksa |
|------|---------|---------------------|
| `web-app` | Ada web server (nginx/apache) + runtime (PHP/Node) | Nginx/Apache, PHP/FPM, Composer, Node, MySQL/PG, Redis, Supervisor, SSL |
| `database` | Ada DB (MySQL/PG/Mongo) tanpa web server | MySQL/PG/Mongo, Redis, file backup |
| `container` | Ada Docker + containers berjalan | Docker version, running containers, images, Docker Compose |
| `general` | Tidak cocok tipe lain | Minimal |

**Mode operasi dan derivasi:**

| Mode | Kondisi | Perilaku |
|------|---------|----------|
| `setup` | Komponen kritis belum ada (web server, runtime, app, atau DB tidak jalan) | Toleransi tinggi untuk konfigurasi infrastruktur |
| `deploy` | Komponen ada tapi server muda (uptime < 7 hari) atau disk tinggi (> 80%); tipe `general` selalu deploy | Operasi standar, WRITE perlu konfirmasi user |
| `production` | Semua jalan, uptime > 7 hari, disk sehat | Operasi tulis diperketat (lihat Mode Enforcement) |

Memory override via `server:mode-override` selalu menang atas derivasi otomatis.

**Mode enforcement (dual layer):**

- **Server (`_mode_gate`)**: di mode `production`, blokir:
  - Tool `laravel_deploy`
  - `apt install/remove/purge/upgrade`
  - `dpkg -i/-r`
  - `pip install`
  - `npm install/ci`
- **Guard (`_shift_tier`)**: di mode `production`, naikkan tier risiko 1 level (RENDAH menjadi SEDANG, dst.) + tambahkan warning "Mode PRODUCTION" di kartu risiko
- **Auto mode sync (PostToolUse)**: setelah `inspect_server`, guard otomatis parse mode dari tool result dan tulis ke `~/.odin_mode`

Profile summary disimpan ke memory (id: `server:stack-profile`, pinned) sehingga bertahan lintas sesi. Skip inspeksi startup saat testing dengan `ODIN_SKIP_INSPECT=1`.

#### e) Deploy Pipeline (`laravel_deploy`)

Workflow end-to-end untuk deploy aplikasi Laravel:

**Preflight (`_preflight_deploy`):**

| Cek | Kondisi blocker | Kondisi warning |
|-----|-----------------|-----------------|
| Disk usage | >= 95% (blocker: deploy dibatalkan) | >= 85% (warning) |
| Git dirty files | - | Dicatat (akan hilang saat git reset --hard) |
| Current commit | - | Dicatat untuk referensi |
| PHP version | - | Dicatat |
| Drift detection | - | Perubahan sejak deploy terakhir dilaporkan |

**Steps (berurutan, berhenti pada kegagalan pertama):**

1. `artisan down` (maintenance mode)
2. `git fetch --all --prune && git reset --hard origin/<branch>`
3. `composer install --no-dev --optimize-autoloader` (jika `composer=True`)
4. `php artisan migrate --force` (jika `migrate=True`)
5. `optimize:clear + config:cache + route:cache + view:cache`
6. `npm ci && npm run build` (jika `npm_build=True`)
7. `artisan up` (keluar maintenance mode)
8. `sudo -n systemctl reload <fpm_service>` (jika `fpm_service` diisi)

**Post-deploy (jika berhasil):**
- Simpan deploy config ke memory (untuk auto-fill parameter di deploy berikutnya)
- Capture deploy fingerprint: git HEAD hash, composer.lock MD5, migration count, .env line count
- Simpan fingerprint ke memory (untuk drift detection di deploy berikutnya)

#### f) Deploy Fingerprint & Drift Detection

**Fingerprint** yang dicapture setelah deploy berhasil:

| Field | Sumber |
|-------|--------|
| `git_hash` | `git rev-parse HEAD` |
| `composer_lock_md5` | `md5sum composer.lock` |
| `migration_count` | `php artisan migrate:status | grep -c 'Ran'` |
| `env_lines` | `wc -l < .env` |
| `captured_at` | Timestamp ISO |

**Drift detection** (`_detect_drift`) sebelum deploy berikutnya membandingkan state saat ini vs fingerprint tersimpan, melaporkan perubahan seperti:
- "Git HEAD berubah: abc12345 -> def67890"
- "composer.lock berubah (dependency di-update di luar deploy)"
- "+3 migrasi baru sejak deploy terakhir"
- ".env berubah (45 -> 47 baris)"

Terintegrasi ke dalam `_preflight_deploy()` — drift data muncul di `checks["drift"]`.

#### g) Rollback Tracking

Sistem pelacakan state untuk operasi destruktif:

**Pre-state capture (`_capture_pre_state`):**

| Trigger | State yang ditangkap |
|---------|---------------------|
| `git reset/checkout/merge/rebase/pull/clean` | `git rev-parse HEAD` |
| `artisan migrate` | `migrate:status | tail -3` |
| `systemctl restart/stop/reload <svc>` | `systemctl is-active <svc>` |

**Rollback suggestion (`_suggest_rollback`):**

| Pre-state | Saran rollback |
|-----------|---------------|
| `git_head` tersimpan | `git reset --hard <hash>` |
| `migrate_tail` tersimpan | `php artisan migrate:rollback --step=1 --force` |
| Service was active | `sudo -n systemctl restart <svc>` |
| `composer install/update` | `composer install --no-dev --optimize-autoloader` |
| `rm` | "(penghapusan file tidak bisa di-undo — cek backup)" |

Tool `rollback_plan(last=5)` query session history dan tampilkan saran rollback untuk operasi WRITE terakhir. Rollback adalah **saran** — operator meninjau dan menjalankan sendiri.

#### h) Runbook Engine

Sequential multi-step executor untuk workflow operasional:

- Maks **20 langkah** per runbook
- Berhenti pada **kegagalan pertama** (kecuali langkah tersebut punya `continue_on_fail=True`)
- Setiap langkah: error analysis + rollback tracking
- Di mode production, setiap command langkah dicek terhadap `_mode_gate()`

**4 template builtin:**

| Template | Deskripsi | Langkah |
|----------|-----------|---------|
| `ssl-renew` | Perpanjang sertifikat SSL Let's Encrypt | certbot renew, nginx -t, systemctl reload nginx |
| `db-backup` | Backup database MySQL/MariaDB | mysqldump, verify file |
| `log-cleanup` | Bersihkan log lama untuk bebaskan disk | check disk, hapus .gz > 30 hari, truncate laravel.log, verify disk |
| `health-check` | Cek kesehatan dasar server | disk, memory, nginx, mysql, php-fpm (semua continue_on_fail) |

Template custom disimpan di memory (key `server:runbook-<nama>`, format JSON dengan `description` + `steps`). Custom override builtin dengan nama yang sama.

#### i) Audit Log

Append-only `audit.jsonl` — **TIDAK pernah di-compact atau dihapus**. Berbeda dari memory yang di-fold/compact, audit log adalah catatan kronologis permanen untuk investigasi insiden.

Setiap record:

| Field | Isi |
|-------|-----|
| `ts` | Timestamp ISO UTC |
| `tool` | Nama tool (run_command, laravel_deploy, dll) |
| `summary` | Ringkasan operasi (maks 500 chars) |
| `success` | Boolean |
| `exit_code` | Integer atau null |
| `duration_sec` | Durasi eksekusi |
| `mode` | Mode deploy (local/ssh) |

Tool `audit_tail` untuk membaca dengan filter: `last` (count, maks 100), `tool_filter`, `success_only`, `since` (timestamp ISO).

Disable dengan `AUDIT_ENABLED=0`.

#### j) Session History

In-memory list `_SESSION_LOG` yang melacak semua tool execution untuk sesi saat ini (hilang saat server respawn). Setiap entry mencatat: seq, timestamp, tool, summary, success, exit_code, duration, pre_state, rollback. Tool `session_history(last=0)` mengembalikan daftar ini. Read-only.

#### k) Trend Detection

Ring-buffer metrik di memory (maks 7 snapshot, key `server:metrics-history`). Setiap inspeksi:
1. Simpan snapshot: disk_pct, memory_pct, uptime_days
2. Bandingkan dengan snapshot tertua
3. Jika delta >= 3%, laporkan tren (naik/turun/stabil)

Hasil trend muncul di `profile["_trend"]` setelah inspeksi.

#### l) Watchdog Resource (`health://live`)

Health check ringkas tanpa daemon atau port — Claude polls via `/loop`:

| Metrik | Threshold anomali |
|--------|-------------------|
| Disk | >= 85% |
| Memory | >= 90% |
| Nginx | Status bukan "active" |
| MySQL | Status bukan "active" |
| Load | Dilaporkan (tanpa threshold) |
| PHP-FPM | Dilaporkan (tanpa threshold) |

Return `status: OK` atau `status: ANOMALI` dengan daftar issue.

#### m) Environment Variables

| Variable | Default | Fungsi |
|----------|---------|--------|
| `DEPLOY_MODE` | `local` | Mode eksekusi: `local` atau `ssh` |
| `SSH_TARGET` | (kosong) | `user@host` (wajib jika mode ssh) |
| `SSH_PORT` | `22` | Port SSH |
| `SSH_KEY` | (kosong) | Path private key (opsional) |
| `PROJECT_ROOT` | (kosong) | Default cwd, BUKAN pagar keamanan |
| `LOCK_CWD_TO_PROJECT` | `0` | `1` = kembalikan kurungan cwd lama |
| `ALLOWED_LOG_DIRS` | `/var/log,/var/www,/home,/tmp` | Folder yang boleh dibaca tail_log |
| `DEFAULT_TIMEOUT` | `180` | Timeout default (detik) |
| `MAX_TIMEOUT` | `900` | Timeout maksimum (detik) |
| `OUTPUT_LIMIT` | `20000` | Potong output panjang |
| `CONTEXT_BUDGET` | `5000` | Threshold smart output |
| `MEMORY_DIR` | `/home/odin/memory` | Folder simpanan memory |
| `MEMORY_MAX_TEXT` | `4000` | Panjang maks teks satu entry |
| `MEMORY_MAX_ENTRIES` | `2000` | Batas entry hidup (lebih = compaction) |
| `AUDIT_ENABLED` | `1` | `0` = matikan audit log |
| `AGENT_LOG_LEVEL` | `INFO` | Level logging |
| `ODIN_SKIP_INSPECT` | (kosong) | `1` = lewati inspeksi startup (untuk testing) |

---

## Komponen 2: `client/odin_guard.py` (651 baris)

**PreToolUse hook + PostToolUse mode-sync** di sisi laptop. Ini adalah **gatekeeper UX** — bukan batas keamanan (yang sebenarnya adalah hak OS user `odin` + sudoers).

Guard membaca JSON dari stdin, mengklasifikasikan tool call, dan mengembalikan `permissionDecision` (`allow` atau `ask`) beserta `reason` (kartu risiko). Pada error apa pun, exit 0 tanpa output — jangan memblokir karena bug guard.

### Klasifikasi READ/WRITE (`classify_command`)

Proses klasifikasi berurutan:

1. **Hard-block check**: `DANGER` regex (selaras dengan `_DANGER_RE` server + tambahan `kill(all)?` dan `pkill` — guard lebih ketat by design)
2. **Command substitution**: `$()` dan backtick -> selalu "ask" (cegah bypass via subshell)
3. **Redirect/dev/null cleanup**: abaikan `N>/dev/null` dan `2>&1` (lazim di perintah read)
4. **Quote stripping**: `_strip_quotes()` kosongkan isi string ber-quote agar operator SQL (`>`, `<`, `|`) di dalam `-e "..."` tidak dikira metakarakter shell. Handle juga `$'...'` (ANSI-C quoting).
5. **Redirect detection**: `>` atau `>>` ke file nyata (bukan didahului digit fd) atau `tee` -> "ask"
6. **Pipeline split**: pecah command di `||`, `&&`, `|`, `;`, `&`, `\n`
7. **Per-segment classification** via `seg_is_read()` — semua segment harus READ agar command keseluruhan dianggap READ

### 23 Sub-Command Classifiers dalam `seg_is_read()`

| # | Command | READ sub-commands/conditions |
|---|---------|------------------------------|
| 1 | `git` | status, log, diff, show, rev-parse, describe, ls-files, ls-tree, blame, shortlog, cat-file, reflog, grep, name-rev, count-objects, var, whatchanged (17 sub-commands) |
| 2 | `php` | -v, --version, -m, -i, -l, --ini, --rf, --ri (8 flags) |
| 3 | `composer` | show, --version, -V, diagnose, validate, licenses, outdated, status, about, depends, prohibits, why (12 sub-commands) |
| 4 | `systemctl` | status, is-active, is-enabled, is-failed, list-units, list-unit-files, show, cat, get-default (9 sub-commands) |
| 5 | `docker` | ps, images, logs, inspect, version, info, stats, top, port, diff, history (11 sub-commands) |
| 6 | `sed` | READ kecuali ada `-i` flag (in-place edit) |
| 7 | `find` | READ kecuali ada `-delete`, `-exec`, `-execdir`, `-ok`, `-okdir`, `-fprint`, `-fls`, `-fprintf` |
| 8 | `awk/gawk/mawk` | READ kecuali ada `system()` call atau print redirect (`>`, `>>`) |
| 9 | DB clients (`mysql`, `mariadb`, `psql`, `sqlite3`, `mysqldump`, `mysqladmin`) | Khusus handler `_db_seg_is_read()`: mysqldump/mysqladmin selalu WRITE; input dari file/heredoc selalu WRITE; cek SQL verb (SELECT/SHOW/DESCRIBE = READ, INSERT/UPDATE/DELETE/DROP = WRITE); cek meta-command psql/sqlite |
| 10 | `apt/apt-get/apt-cache` | list, show, search, depends, rdepends, policy, showsrc, changelog, madison, info (10 sub-commands) |
| 11 | `dpkg` | -l, --list, -L, --listfiles, -s, --status, -S, --search, -p, --print-avail, --info, -I, --contents, -c, --audit (14 flags) |
| 12 | `pip/pip3` | list, show, freeze, check, config, --version, -V (7 sub-commands) |
| 13 | `npm/yarn/pnpm` | list, ls, view, info, outdated, why, audit, explain, --version, -v (10 sub-commands) |
| 14 | `ip` | addr, a, address, route, r, link, l, neigh, n, neighbour, rule, tunnel, maddress, mroute, monitor — TANPA aksi write (add, del, change, replace, set, flush, append) |
| 15 | `ufw` | status, show, version (3 sub-commands) |
| 16 | `nginx/apache2ctl/httpd` | -t, -T, -V, -v (4 flags) |
| 17 | `fail2ban-client` | status, get, ping, banned, version (5 sub-commands) |
| 18 | `certbot` | certificates, --help, -h (3 sub-commands) |
| 19 | `timedatectl` | status, show, timesync-status, list-timezones (4 sub-commands); tanpa argumen juga READ |
| 20 | `loginctl` | list-sessions, list-users, show-session, show-user, session-status, user-status (6 sub-commands) |
| 21 | `curl` | READ kecuali ada flag write: --request, --data, --data-raw, --data-binary, --data-urlencode, --form, --upload-file, --output, --remote-name, atau short flags -X, -d, -F, -T, -o, -O |
| 22 | `sudo/su/doas` | Selalu WRITE (tidak dievaluasi lebih lanjut) |
| 23 | Base READ commands | 64 perintah inspeksi murni: ls, ll, cat, bat, head, tail, less, more, grep, rg, find, fd, stat, file, wc, df, du, free, uptime, uname, hostname, whoami, id, who, w, groups, ps, pgrep, pstree, date, cal, echo, printf, pwd, env, printenv, which, command, type, whereis, basename, dirname, readlink, realpath, tree, sort, uniq, cut, tr, column, paste, comm, join, diff, cmp, md5sum, sha256sum, xxd, od, hexdump, strings, jq, yq, getent, lsblk, lscpu, lsof, ss, netstat, ping, dig, nslookup, host, vmstat, iostat, dmesg, journalctl, dll |

### Risk Engine (`assess_command`)

Dua mesin penilaian berjalan paralel, dan tier **tertinggi** yang dipilih (agar kartu tidak menyesatkan pada perintah majemuk seperti `SELECT && rm -rf`):

**1. Shell Rules (`_SHELL_RULES`)** — 26 aturan regex:

| Tier | Pola | Aksi | Efek |
|------|------|------|------|
| KRITIS | `rm -rf /` | rm -rf pada root filesystem | Kerusakan total OS; server tak bisa boot |
| KRITIS | `mkfs` | Format filesystem | Seluruh data partisi hilang |
| KRITIS | `dd of=/dev/` | dd menimpa block device | Disk/partisi tertimpa; data hilang |
| KRITIS | `shutdown/reboot/halt/init 0` | Matikan/restart server | Server offline; semua layanan terputus |
| KRITIS | `chmod -R 777 /` | chmod 777 rekursif dari root | Izin sistem rusak; risiko keamanan |
| KRITIS | `:(){ {` | Fork bomb | Habiskan resource; server hang |
| TINGGI | `rm -rf` (bukan root) | Hapus rekursif paksa | Folder & isinya terhapus permanen |
| TINGGI | `git reset --hard` | Reset git hard | Perubahan lokal belum ter-commit hilang |
| TINGGI | `git clean -f` | Clean git force | File untracked terhapus permanen |
| TINGGI | `killall/pkill` | Hentikan banyak proses | Semua proses cocok nama dimatikan |
| TINGGI | `find -delete` | Hapus banyak file | Semua file cocok kriteria terhapus |
| TINGGI | `find -exec` | Jalankan perintah pada hasil find | Bisa rm/chmod massal |
| SEDANG | `apt/dpkg install/remove` | Ubah paket sistem | Dependensi & layanan terpengaruh |
| SEDANG | `systemctl restart/stop` | Restart/stop service | Downtime singkat |
| SEDANG | `artisan migrate` | Migrasi skema DB | Struktur DB berubah |
| SEDANG | `chown` | Ubah kepemilikan file | Bisa pengaruhi akses layanan |
| SEDANG | `chmod` | Ubah izin file | Salah set bisa bocor/rusak akses |
| SEDANG | `mv` | Pindah/rename file | Bisa menimpa tujuan |
| SEDANG | `git checkout/reset/revert/rebase/merge` | Ubah state git | Branch/working tree berubah |
| SEDANG | `crontab` | Ubah jadwal cron | Tugas terjadwal berubah |
| SEDANG | `npm/yarn build/ci/install` | Build/instalasi frontend | Berat I/O & lama |
| SEDANG | `composer install/update/require/remove` | Ubah dependensi PHP | vendor/ berubah |
| SEDANG | `rm` (tanpa -rf) | Hapus file | File terhapus; tak masuk trash |
| RENDAH | `systemctl reload / nginx -s reload` | Reload konfigurasi | Tanpa downtime |
| RENDAH | `mkdir/touch/ln/cp` | Buat/salin file | cp bisa menimpa tujuan |
| RENDAH | `git add/commit/stash/tag/fetch/pull/push` | Operasi git umum | Umumnya reversibel |

Tambahan: redirect/tee ke file nyata yang terdeteksi di luar string ber-quote = RENDAH ("Tulis output ke berkas").

**2. DB Assessor (`_assess_db`)** — khusus untuk klien database:

| Tier | Operasi | Detail |
|------|---------|--------|
| KRITIS | `DROP DATABASE` | SEMUA tabel & data hilang permanen |
| TINGGI | `DROP TABLE/VIEW/INDEX/TRIGGER/PROCEDURE/FUNCTION` | Objek beserta isinya hilang permanen |
| TINGGI | `TRUNCATE` | Semua baris tabel terhapus (DDL, tak ter-rollback di MySQL) |
| TINGGI | `DELETE` tanpa `WHERE` | SEMUA baris tabel terhapus |
| TINGGI | `UPDATE` tanpa `WHERE` | SEMUA baris tabel berubah |
| TINGGI | SQL dari file (`mysql < file.sql`) | Isi file tak terlihat — bisa memuat DROP/DELETE massal |
| SEDANG | `DELETE` dengan `WHERE` | Menghapus baris yang cocok |
| SEDANG | `UPDATE` dengan `WHERE` | Mengubah kolom pada baris yang cocok |
| SEDANG | `ALTER TABLE` | Struktur tabel berubah; pada tabel besar bisa lock |
| SEDANG | `GRANT/REVOKE` | Privilege user database berubah |
| SEDANG | `SELECT INTO OUTFILE/DUMPFILE` | Menulis berkas ke filesystem server DB |
| SEDANG | Verb tak dikenali | Tak bisa dipastikan baca/tulis |
| RENDAH | `INSERT/REPLACE INTO` | Menambah baris (REPLACE bisa menimpa) |
| RENDAH | `CREATE TABLE/DATABASE/INDEX/VIEW` | Membuat objek baru; tak menyentuh data lama |
| RENDAH | `mysqldump` | Backup/dump (operasi baca) |
| AMAN | `SELECT/SHOW/DESCRIBE/EXPLAIN` | Hanya membaca; data tak berubah |

### Risk Card Output

Setiap WRITE command menghasilkan **kartu risiko** yang ditampilkan ke user sebelum konfirmasi:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟡 RISIKO: SEDANG
Cmd   : systemctl restart nginx
Dir   : /var/www/simuru
Aksi  : Restart/stop service
Efek  : Layanan mati sesaat -> request gagal (downtime singkat)
Saran : Saat trafik rendah; pakai reload bila cukup
Undo  : systemctl restart nginx (restart ulang)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Icon per tier: AMAN/RENDAH = `🟢`, SEDANG = `🟡`, TINGGI = `🟠`, KRITIS = `🔴`.

Tambahan kontekstual pada kartu:
- Mode PRODUCTION: "tier dinaikkan 1 level"
- Tier KRITIS: "Server akan MENOLAK kecuali allow_dangerous=True"
- `allow_dangerous=True`: "Flag allow_dangerous aktif — rem darurat dilepas"

### Undo Hints (12 Patterns)

Guard menyertakan saran undo di kartu risiko:

| Pola command | Saran undo |
|-------------|-----------|
| `git reset --hard` | `git reflog` lalu `git reset --hard <commit-sebelumnya>` |
| `git clean -f` | (file untracked hilang permanen — tak bisa undo) |
| `git checkout` | `git checkout <branch-sebelumnya>` |
| `git merge` | `git merge --abort` atau `git reset --hard HEAD~1` |
| `git rebase` | `git rebase --abort` atau `git reflog` lalu reset |
| `artisan migrate` | `php artisan migrate:rollback --step=1 --force` |
| `systemctl restart` | `systemctl restart <service>` (restart ulang) |
| `systemctl stop` | `systemctl start <service>` |
| `systemctl reload` | `systemctl reload <service>` (reload ulang) |
| `composer install/update` | `composer install` (ulangi setelah perbaiki) |
| `rm` | (penghapusan file tidak bisa di-undo — cek backup) |
| `mv` | `mv <tujuan> <asal>` (balik manual) |

### Mode-Aware Tier Shifting

Guard membaca mode dari `~/.odin_mode` (file) atau `ODIN_MODE` (env var), default `deploy`.

Di mode **production**: tier dinaikkan 1 level via `_shift_tier()`:
- AMAN -> RENDAH
- RENDAH -> SEDANG
- SEDANG -> TINGGI
- TINGGI -> KRITIS
- KRITIS -> KRITIS (tetap)

### PostToolUse: Auto Mode Sync

Guard juga berfungsi sebagai **PostToolUse handler**. Setelah `inspect_server` selesai:
1. Cek apakah `tool_result` berisi field `mode`
2. Jika mode valid (setup/deploy/production), tulis ke `~/.odin_mode`
3. Guard sesi berikutnya otomatis membaca mode baru — tanpa perlu `echo` manual

### Tool-Specific Approval Logic

Guard punya handler khusus per tool ODIN:

| Tool | Logika |
|------|--------|
| `run_command` | `allow_dangerous=True` -> ask + flag warning; `classify_command()` = allow -> auto; lainnya -> risk card |
| `service_action` | status/is-active/is-enabled -> auto-allow; restart/stop/start/reload -> service card |
| `laravel_deploy` | Dekomposisi langkah -> kartu risiko TINGGI |
| `run_tests` | Auto-allow |
| `runbook` | Hitung tier tertinggi dari semua langkah; semua READ -> auto-allow; ada WRITE -> kartu tier tertinggi |
| `memory_write` | Kartu RENDAH (tampilkan ns, key, preview isi 100 chars) |
| `memory_forget` | Kartu RENDAH (tampilkan id yang dihapus) |
| `rollback_plan` | Auto-allow |
| `audit_tail` | Auto-allow |
| `runbook_templates` | Auto-allow |
| `inspect_server` | Auto-allow |
| Tool lain | Fall-through (tanpa pendapat) |

### Update Checker Integration

Saat guard dijalankan (`__main__`), sebelum memproses hook, ia menjalankan `_check_update_bg()` yang spawn `update_checker.py` di background (fire-and-forget, tidak memblokir guard).

---

## Komponen 3: `client/update_checker.py` (155 baris)

Background checker untuk notifikasi update:

1. **Baca versi lokal** (`_local_version`): parse `__version__` dari `server/odin_agent.py` lokal (cari di `~/.odin/server/` atau relatif dari file checker)
2. **Ambil versi remote** (`_remote_version`):
   - Coba GitHub Releases API (`/repos/{REPO}/releases/latest`) dulu
   - Fallback: baca `__version__` langsung dari raw file di main branch
3. **Bandingkan** (`_parse_version`): semantic versioning, parse ke tuple integer
4. **Cache** (`_read_cache` / `_write_cache`): simpan hasil di `~/.odin/.update-cache.json`, TTL 6 jam
5. **Notifikasi**: jika ada update, tampilkan banner berwarna di stderr:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚡ ODIN update tersedia!  v1.1.0 -> v1.2.0

  Update sekarang:
    odin-update
    curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash

  Changelog:
    https://github.com/.../releases/tag/v1.2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Komponen 4: `server/run.sh` (9 baris)

Launcher minimalis yang di-exec oleh SSH:

```bash
export DEPLOY_MODE=local
export PROJECT_ROOT=/var/www/simuru
export ALLOWED_LOG_DIRS=/var/log,/var/www
export MEMORY_DIR=/home/odin/memory
cd "$PROJECT_ROOT" || exit 1
exec /home/odin/.venv/bin/python /home/odin/odin_agent.py
```

Set environment variables lalu exec Python di venv. Memory sengaja di luar PROJECT_ROOT.

---

## Model Keamanan: 4 Lapis Defense-in-Depth

```
Layer 1: READ/WRITE Classifier (client)
         23 sub-command classifiers, 64+ base READ commands
         auto-allow READ, ask WRITE
              |
              v
Layer 2: Risk Engine (client)
         26 shell rules + DB assessor
         5-tier risk card (AMAN/RENDAH/SEDANG/TINGGI/KRITIS)
         + undo hints + mode-aware tier shifting
              |
              v
Layer 3: Hard-block (server)
         _DANGER_RE blocks catastrophic commands
         (rm -rf /, mkfs, dd, shutdown, fork bomb, DROP DATABASE)
         kecuali allow_dangerous=True
              |
              v
Layer 4: OS-level (server)
         User `odin` dengan limited sudoers
         INI batas keamanan sebenarnya
```

**Perlindungan tambahan:**

| Mekanisme | Tujuan |
|-----------|--------|
| Command substitution detection | `$()` dan backtick selalu "ask" — cegah bypass via subshell |
| Quote stripping (`_strip_quotes`) | Cegah operator SQL di string ber-quote dikira redirect shell |
| Secret detection di memory | Tolak password/token/private-key/JWT/AWS-key sebelum masuk JSONL |
| Double brake (guard + server) | Guard bertanya dulu, server blokir lagi — dua titik cegah |
| Mode enforcement (dual layer) | Server blokir tool/command, guard naikkan tier — keduanya aktif |
| DANGER sync requirement | `_DANGER_RE` (server) dan `DANGER` (client) harus sinkron |

---

## Alur Data End-to-End

```
1. User minta Claude Code: "deploy ke production"

2. Claude Code panggil tool MCP `laravel_deploy`

3. odin_guard.py (PreToolUse) intercept:
   - Dekomposisi langkah deploy
   - Hasilkan risk card TINGGI
   - User diminta konfirmasi

4. User approve -> MCP call diteruskan ke server

5. odin_agent.py di server:
   a. _mode_gate()       -> cek apakah mode production memblokir
   b. _preflight_deploy() -> cek disk, git dirty, PHP, drift
   c. Eksekusi step-by-step (git -> composer -> migrate -> cache -> fpm)
   d. Per-step: _capture_pre_state() + _analyze_output() + _suggest_rollback()
   e. Post-deploy: simpan config + fingerprint ke memory
   f. Audit log dicatat

6. Result dikembalikan ke Claude Code via MCP stdio

7. odin_guard.py (PostToolUse) bisa sync mode

8. Claude Code analisis result, putuskan langkah berikutnya
```

---

## Deployment

Repo ini adalah source of truth. File disalin ke server (bukan deploy via git di sisi server):

```
server/odin_agent.py  ->  /home/odin/odin_agent.py  (chmod 600)
server/run.sh         ->  /home/odin/run.sh          (chmod 755)
```

MCP server di-spawn fresh per sesi Claude Code via `ssh vps-app /home/odin/run.sh`.

---

## Versioning

ODIN menggunakan semantic versioning. String `__version__` ada di kedua file Python (`server/odin_agent.py` dan `client/odin_guard.py`) dan **harus tetap sinkron**. Perubahan dilacak di `CHANGELOG.md`. Nama MCP server: `odin` (prefix tool: `mcp__odin__` atau `mcp__deploy-agent__` tergantung konfigurasi MCP client).

---

## Metrik Keseluruhan

| Metrik | Nilai |
|--------|-------|
| Total source lines | 2852 (server 2046 + guard 651 + updater 155) |
| MCP tools | 17 |
| MCP resources | 2 |
| Error patterns | 23 (18 dengan suggested_commands) |
| Shell risk rules | 26 + DB assessor |
| READ sub-classifiers | 23 (covering 64+ base commands) |
| Undo hint patterns | 12 |
| Runbook templates | 4 builtin + custom |
| Risk tiers | 5 (AMAN < RENDAH < SEDANG < TINGGI < KRITIS) |
| Operation modes | 3 (setup < deploy < production) |
| Security layers | 4 |
| Memory namespaces | 3 |
| Runtime dependencies | 1 (`mcp[cli]`) |
| Test files | 10 (485 tests, 3545 lines) |

---

## Filosofi Desain

ODIN dibangun dengan prinsip **"akses penuh, tapi sadar risiko"**:

1. **Tidak ada sandbox yang membatasi** — batas keamanan sebenarnya ada di level OS (user `odin` + sudoers). Guard dan risk engine adalah UX layer yang membantu user menilai dampak.

2. **Setiap operasi meninggalkan jejak** — session history (sesi ini) + audit log (permanen) + memory (persisten). Tidak ada operasi yang hilang tanpa rekam.

3. **Rollback selalu tersedia sebagai saran** — pre-state ditangkap sebelum operasi destruktif, undo commands disarankan, tapi keputusan tetap di tangan operator.

4. **Memory menjadikan ODIN stateful lintas sesi** — ingat profil user, instruksi durable, dan fakta server. Server di-spawn fresh tiap sesi, tapi "ingatan" bertahan di disk.

5. **Deploy bersifat idempoten** — `git reset --hard` memastikan server state selalu match repo. Perubahan manual di server sengaja dibuang.

6. **Minimalis dalam dependensi** — satu dependensi (`mcp[cli]`), dua file Python, satu launcher Bash. Tidak ada framework, tidak ada build step, tidak ada daemon yang harus dijaga.

7. **Dual language** — code dan UX dalam Bahasa Indonesia (kartu risiko, pesan error, docstring), sementara tetap mengikuti konvensi teknis internasional (variable naming, MCP protocol).
