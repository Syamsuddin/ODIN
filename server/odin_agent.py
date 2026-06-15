#!/usr/bin/env python3
"""
ODIN v1.0 — MCP server "tangan" untuk Claude Code.

Otak  = Claude Code (CLI).
Tangan = ODIN server ini. Claude Code memanggil tool di sini lewat protokol MCP (stdio),
server mengeksekusi perintah di Linux, lalu MENGEMBALIKAN stdout/stderr/exit code/log
sebagai UMPAN BALIK. Claude Code membaca hasil itu dan memutuskan langkah berikutnya
(deploy -> testing -> baca log -> perbaiki -> ulang).

Cocok untuk stack PHP / Laravel / MySQL / Nginx / Ubuntu.

Mode eksekusi:
  - local : perintah dijalankan di mesin yang sama dengan server ini.
  - ssh   : perintah dijalankan di server remote lewat binary `ssh` (tanpa lib tambahan).

=============================== MEMORY (baru) ============================
Server ini di-spawn FRESH tiap sesi. Saat startup, memory dibaca dari disk dan
disuntikkan ke FastMCP(instructions=...) -> profil user + instruksi durable +
fakta server ter-pin OTOMATIS muncul di konteks model tiap sesi baru.

Tiga namespace tetap (allowlist):
  - server      : fakta infrastruktur (nama service, versi, quirk deploy). Ditulis agent.
  - instruction : arahan/preferensi durable dari user ("selalu backup sebelum deploy").
  - profile     : identitas user (nama, peran, kontak).

Storage = append-only JSONL (event log) + fold (last-write-wins per (ns,key),
tombstone untuk hapus). Aman konkuren (O_APPEND + flock). File di MEMORY_DIR,
SENGAJA di luar PROJECT_ROOT agar tak kena sandbox run_command dan tak ikut
`git reset --hard` saat deploy.
=========================================================================

================================ ENV VARS ================================
  DEPLOY_MODE        local | ssh                     (default: local)
  SSH_TARGET         user@host                        (wajib jika mode ssh)
  SSH_PORT           22                               (default: 22)
  SSH_KEY            /path/private_key                (opsional)
  PROJECT_ROOT       /var/www/app                     (default cwd; TIDAK mengurung)
  LOCK_CWD_TO_PROJECT 0 | 1                           (1 = kembalikan kurungan cwd lama)
  ALLOWED_LOG_DIRS   /var/log,/var/www,/home,/tmp     (folder yang boleh dibaca tail_log)
  DEFAULT_TIMEOUT    180   MAX_TIMEOUT 900             (detik)
  OUTPUT_LIMIT       20000                            (potong output panjang)
  AGENT_LOG_LEVEL    INFO
  MEMORY_DIR         /home/odin/memory                (folder simpanan memory)
  MEMORY_MAX_TEXT    4000                             (panjang maks teks satu entry)
  MEMORY_MAX_ENTRIES 2000                             (batas entry hidup; lebih -> compaction)
  AUDIT_ENABLED      1                                (0 = matikan audit log)
=========================================================================

Pasang:  pip install "mcp[cli]"
Jalan :  python3 odin_agent.py     (dijalankan otomatis oleh Claude Code via MCP)
"""

from __future__ import annotations

__version__ = "2.1.0"

import atexit
import fcntl
import json
import logging
import math
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging WAJIB ke stderr. Jangan pernah print() ke stdout di server stdio,
# karena stdout dipakai untuk lalu lintas protokol MCP (akan korup kalau dicemari).
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=os.environ.get("AGENT_LOG_LEVEL", "INFO"),
    stream=sys.stderr,
    format="%(asctime)s [odin] %(levelname)s: %(message)s",
)
log = logging.getLogger("odin")

# ---------------------------------------------------------------------------
# Konfigurasi dari environment
# ---------------------------------------------------------------------------
MODE = os.environ.get("DEPLOY_MODE", "local").strip().lower()   # local | ssh
SSH_TARGET = os.environ.get("SSH_TARGET", "").strip()           # user@host
SSH_PORT = os.environ.get("SSH_PORT", "22").strip()
SSH_KEY = os.environ.get("SSH_KEY", "").strip()

DEFAULT_TIMEOUT = int(os.environ.get("DEFAULT_TIMEOUT", "180"))
MAX_TIMEOUT = int(os.environ.get("MAX_TIMEOUT", "900"))
OUTPUT_LIMIT = int(os.environ.get("OUTPUT_LIMIT", "20000"))
CONTEXT_BUDGET = int(os.environ.get("CONTEXT_BUDGET", "5000"))
CACHE_TTL_READ = int(os.environ.get("CACHE_TTL_READ", "60"))   # detik; 0 = matikan cache

PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "").strip().rstrip("/")
PROJECT_NAME = os.environ.get("PROJECT_NAME", "").strip()
# Niat user: AKSES PENUH server. PROJECT_ROOT kini hanya DEFAULT cwd (di-set run.sh),
# BUKAN pagar. Gerbang keamanan = konfirmasi WRITE + kartu risiko (PreToolUse hook) +
# hard-block katastrofik di bawah. Set LOCK_CWD_TO_PROJECT=1 utk mengembalikan kurungan.
LOCK_CWD_TO_PROJECT = os.environ.get("LOCK_CWD_TO_PROJECT", "0").strip().lower() in ("1", "true", "yes")

ALLOWED_LOG_DIRS = [
    p.strip().rstrip("/")
    for p in os.environ.get("ALLOWED_LOG_DIRS", "/var/log,/var/www,/home,/tmp").split(",")
    if p.strip()
]

# ----- Memory config -------------------------------------------------------
MEMORY_DIR = os.environ.get(
    "MEMORY_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
).strip().rstrip("/")
MEMORY_FILE = os.path.join(MEMORY_DIR, "memory.jsonl")
MEMORY_MAX_TEXT = int(os.environ.get("MEMORY_MAX_TEXT", "4000"))
MEMORY_MAX_ENTRIES = int(os.environ.get("MEMORY_MAX_ENTRIES", "2000"))
STALE_DAYS = int(os.environ.get("STALE_DAYS", "30"))
DIGEST_BUDGET = int(os.environ.get("DIGEST_BUDGET", "3000"))

# ----- Cortex: global memory lintas-project (consciousness layer) ----------
GLOBAL_MEMORY_DIR = os.environ.get(
    "GLOBAL_MEMORY_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "_cortex"),
).strip().rstrip("/")
GLOBAL_MEMORY_FILE = os.path.join(GLOBAL_MEMORY_DIR, "memory.jsonl")
GLOBAL_EVENTS_FILE = os.path.join(GLOBAL_MEMORY_DIR, "events.jsonl")
EVENTS_DIGEST_HOURS = int(os.environ.get("EVENTS_DIGEST_HOURS", "24"))

CORTEX_NAMESPACES = ("profile", "cross")
PROJECT_NAMESPACES = ("server", "instruction")
MEMORY_NAMESPACES = CORTEX_NAMESPACES + PROJECT_NAMESPACES

# ----- Audit log (jejak eksekusi append-only — JANGAN dihapus) ----------------
AUDIT_FILE = os.path.join(MEMORY_DIR, "audit.jsonl")
AUDIT_ENABLED = os.environ.get("AUDIT_ENABLED", "1").strip().lower() not in ("0", "false", "no")

# Pola perintah katastrofik -> ditolak kecuali allow_dangerous=True.
# Ini JARING PENGAMAN, bukan sandbox. Batas keamanan sebenarnya = hak akses
# user OS + aturan sudoers. Jalankan server ini sebagai user terbatas.
_DANGER_PATTERNS = [
    r"\brm\s+-rf\s+/(\s|$|\*)",       # rm -rf /  |  rm -rf /*
    r"\brm\s+-rf\s+~",
    r"\bmkfs\b",
    r"\bdd\b.*\bof=/dev/",
    r">\s*/dev/sd[a-z]",
    r":\(\)\s*\{",                     # fork bomb
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\binit\s+0\b",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bchown\s+-R\b.*\s/\s*$",
    r"\bdrop\s+database\b", r"\bmysqladmin\b.*\bdrop\b",  # DB drop: rem darurat (approval ganda)
]
_DANGER_RE = re.compile("|".join(_DANGER_PATTERNS), re.IGNORECASE)

# Pola nilai mirip-rahasia -> ditolak saat memory_write kecuali allow_secret=True.
# Analog _DANGER_RE: jaring pengaman agar password/kunci tak masuk simpanan.
_SECRET_PATTERNS = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\b(password|passwd|pwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S+",
    r"\bAKIA[0-9A-Z]{16}\b",                       # AWS access key id
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",             # GitHub token
    r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",  # JWT
]
_SECRET_RE = re.compile("|".join(_SECRET_PATTERNS), re.IGNORECASE)


# ---------------------------------------------------------------------------
# INOVASI 1 — Result Cache TTL
# Model sering memanggil perintah status yang sama berulang (server_info, ps,
# df, free). Cache in-memory per session; key = (command, cwd); TTL dari
# env CACHE_TTL_READ (default 60 s). Hanya berlaku untuk perintah READ-only
# yang berhasil. WRITE command selalu melewati cache dan tidak di-cache.
# Set CACHE_TTL_READ=0 untuk menonaktifkan cache sepenuhnya.
# ---------------------------------------------------------------------------
_READ_ONLY_RE = re.compile(
    r"^\s*("
    r"df\b|free\b|uptime\b|uname\b|hostname\b|date\b"
    r"|ps\b|pgrep\b|pidof\b|top\b"
    r"|cat\b|ls\b|find\b|wc\b|head\b|tail\b|grep\b|awk\b|sort\b|uniq\b"
    r"|sed\b(?!\s+-i)"                             # sed tanpa -i = read-only
    r"|git\s+(log|status|diff|show|branch|remote|tag|describe|rev-parse)\b"
    r"|systemctl\s+(status|is-active|is-enabled|list-units)\b"
    r"|php\s+(-[vi]|--version)\b|composer\s+--version\b"
    r"|python[23]?\s+--version\b|node\s+--version\b|npm\s+--version\b"
    r"|whoami\b|id\b|which\b|type\b|env\b|printenv\b|echo\b"
    r"|ss\b|netstat\b|ip\b|ping\b|curl\b(?!.*-[XdT])"  # curl GET saja
    r")",
    re.IGNORECASE,
)

_result_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _cache_get(command: str, cwd: str) -> dict | None:
    """Kembalikan salinan hasil cache jika masih segar, None jika miss/expired/tidak eligible."""
    if not CACHE_TTL_READ or not _READ_ONLY_RE.match(command):
        return None
    key = (command, cwd)
    entry = _result_cache.get(key)
    if not entry:
        return None
    ts, cached = entry
    age = time.monotonic() - ts
    if age > CACHE_TTL_READ:
        _result_cache.pop(key, None)
        return None
    out = dict(cached)
    out["_cached"] = True
    out["_cache_age_sec"] = round(age, 1)
    return out


def _cache_set(command: str, cwd: str, result: dict) -> None:
    """Simpan hasil ke cache jika perintah read-only dan eksekusi sukses."""
    if not CACHE_TTL_READ or not _READ_ONLY_RE.match(command):
        return
    if not result.get("success"):
        return
    _result_cache[(command, cwd)] = (time.monotonic(), {
        k: v for k, v in result.items()
        if k not in ("_cached", "_cache_age_sec")   # jangan ikutkan meta cache sendiri
    })


# ---------------------------------------------------------------------------
# Inti: bangun & jalankan perintah (local atau ssh)
# ---------------------------------------------------------------------------
def _truncate(text: str) -> str:
    if not text or len(text) <= OUTPUT_LIMIT:
        return text or ""
    half = OUTPUT_LIMIT // 2
    return f"{text[:half]}\n...[{len(text) - OUTPUT_LIMIT} karakter dipotong]...\n{text[-half:]}"


# ---------------------------------------------------------------------------
# INOVASI 3 — Domain-aware Output Filter
# _smart_output lama memotong dengan naive head(5)+tail(10): baris kritis di
# tengah (mis. proses service dari ps aux) ikut terbuang, model terpaksa minta
# ulang → ekstra round-trip. Filter semantik per jenis perintah DIPRIORITASKAN
# sebelum fallback head+tail, sehingga hanya baris bermakna yang dikirim.
# ---------------------------------------------------------------------------
_SERVICES_KNOWN = frozenset({
    "nginx", "php-fpm", "php8.", "php7.", "php5.",
    "mysql", "mysqld", "mariadbd", "mariadb",
    "odin", "deploy", "syamadmin",
    "sshd", "fail2ban", "ufw",
    "redis-server", "memcached",
    "node", "npm", "python", "uvicorn", "gunicorn",
    "supervisord", "supervisor",
    "cron", "atd",
    "rabbitmq", "celery",
})
_SEVERITY_KW = frozenset({
    "error", "err", "warn", "warning",
    "fail", "failed", "failure",
    "crit", "critical", "emerg", "alert",
    "exception", "traceback", "panic",
})


def _filter_ps(lines: list[str]) -> list[str]:
    """ps aux/axu: header + baris yang mengandung service dikenal atau user www-data/odin/deploy."""
    if not lines:
        return lines
    header = lines[0]
    kept = [header]
    for line in lines[1:]:
        low = line.lower()
        if any(svc in low for svc in _SERVICES_KNOWN):
            kept.append(line)
        elif re.search(r"\b(www-data|odin|deploy)\b", line):
            kept.append(line)
    # Fallback: jika tidak ada yang cocok, kembalikan 15 baris pertama
    return kept if len(kept) > 1 else lines[:15]


def _filter_journal(lines: list[str]) -> list[str]:
    """journalctl: hanya baris mengandung kata kunci severity; fallback 30 baris terakhir."""
    filtered = [l for l in lines if any(kw in l.lower() for kw in _SEVERITY_KW)]
    return filtered[-50:] if filtered else lines[-30:]


def _filter_git_log(lines: list[str]) -> list[str]:
    """git log: batasi ke 40 baris — cukup untuk ~8–10 commit dengan pesan penuh."""
    return lines[:40]


def _filter_find(lines: list[str]) -> list[str]:
    """find: batasi ke 60 hasil; lebih dari itu biasanya tidak diproses model sekaligus."""
    return lines[:60]


def _filter_env(lines: list[str]) -> list[str]:
    """env/printenv: buang baris yang terlihat seperti nilai panjang/binary, keep 50."""
    kept = [l for l in lines if len(l) < 200 and not re.search(r"[^\x09\x20-\x7E]", l)]
    return kept[:50]


# Urutan penting: pattern lebih spesifik duluan.
_DOMAIN_FILTERS: list[tuple[re.Pattern, "callable[[list[str]], list[str]]"]] = [
    (re.compile(r"\bps\s+(aux|axu|ax|-e)\b",    re.I), _filter_ps),
    (re.compile(r"\bjournalctl\b",               re.I), _filter_journal),
    (re.compile(r"\bgit\s+log\b",               re.I), _filter_git_log),
    (re.compile(r"\bfind\s+",                   re.I), _filter_find),
    (re.compile(r"\b(env|printenv)\b",           re.I), _filter_env),
]


def _smart_output(result: dict) -> dict:
    """Kompresi output jika stdout > CONTEXT_BUDGET.

    Urutan:
    1. Coba domain filter semantik (Inovasi 3) — pertahankan baris bermakna.
    2. Jika setelah filter masih > CONTEXT_BUDGET, terapkan head+tail fallback.
    3. Jika tidak ada domain filter cocok, langsung fallback head+tail.
    """
    stdout = result.get("stdout", "")
    if not stdout or len(stdout) <= CONTEXT_BUDGET:
        return result

    lines = stdout.splitlines()
    total_lines_orig = len(lines)
    cmd = result.get("command", "")
    filter_name: str | None = None

    # Tahap 1: domain filter semantik
    for pattern, fn in _DOMAIN_FILTERS:
        if pattern.search(cmd):
            lines = fn(lines)
            filter_name = fn.__name__
            break

    joined = "\n".join(lines)
    if len(joined) <= CONTEXT_BUDGET:
        # Domain filter cukup — tidak perlu head+tail
        result["stdout"] = joined
        result["_output_meta"] = {
            "total_chars": len(stdout),
            "total_lines": total_lines_orig,
            "filtered_lines": len(lines),
            "filter": filter_name or "none",
            "truncated": False,
        }
        return result

    # Tahap 2: fallback head+tail (domain filter belum cukup atau tidak ada)
    total_lines = len(lines)
    head = lines[:5]
    tail = lines[-10:]
    result["_output_meta"] = {
        "total_chars": len(stdout),
        "total_lines": total_lines_orig,
        "filtered_lines": total_lines,
        "filter": filter_name or "none",
        "truncated": True,
        "head_lines": 5,
        "tail_lines": 10,
    }
    result["stdout"] = (
        "\n".join(head)
        + f"\n\n...[{total_lines - 15} baris diringkas — total {total_lines} baris, "
        f"{len(stdout)} karakter]...\n\n"
        + "\n".join(tail)
    )
    return result


def _path_inside(path: str, allowed: list[str]) -> bool:
    """Cek prefix path. Mode local: resolve symlink. Mode ssh: string-only."""
    norm = os.path.normpath(path)
    if MODE == "local":
        try:
            norm = os.path.realpath(norm)
        except OSError:
            pass
    if not norm.startswith("/") or ".." in norm.split("/"):
        return False
    return any(norm == d or norm.startswith(d + "/") for d in allowed)


def _build_invocation(command: str, cwd: str | None) -> list[str]:
    remote_cmd = f"cd {shlex.quote(cwd)} && {command}" if cwd else command
    if MODE == "ssh":
        if not SSH_TARGET:
            raise RuntimeError("DEPLOY_MODE=ssh tetapi SSH_TARGET kosong.")
        ssh = ["ssh", "-p", SSH_PORT, "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=accept-new"]
        if SSH_KEY:
            ssh += ["-i", SSH_KEY]
        # ssh menggabungkan argumen sisa dgn spasi lalu dieksekusi shell remote.
        # remote_cmd di-quote agar menjadi satu argumen utuh untuk `bash -lc`.
        ssh += [SSH_TARGET, "bash", "-lc", shlex.quote(remote_cmd)]
        return ssh
    # local: bash login shell agar PATH (composer/php/node) ikut termuat
    return ["bash", "-lc", remote_cmd]


def _run(command: str, cwd: str | None = None, timeout: int = DEFAULT_TIMEOUT,
         allow_dangerous: bool = False) -> dict:
    timeout = max(1, min(int(timeout), MAX_TIMEOUT))

    if not allow_dangerous and _DANGER_RE.search(command):
        return {"success": False, "blocked": True, "exit_code": None,
                "stdout": "", "command": command,
                "stderr": "DITOLAK: cocok pola perintah berbahaya. "
                          "Set allow_dangerous=True hanya jika ini memang disengaja."}

    if LOCK_CWD_TO_PROJECT and PROJECT_ROOT and cwd and not _path_inside(cwd, [PROJECT_ROOT]):
        return {"success": False, "exit_code": None, "stdout": "", "command": command,
                "stderr": f"cwd '{cwd}' di luar PROJECT_ROOT ({PROJECT_ROOT}). "
                          f"(LOCK_CWD_TO_PROJECT aktif — akses penuh dimatikan.)"}

    try:
        argv = _build_invocation(command, cwd)
    except Exception as e:
        return {"success": False, "exit_code": None, "stdout": "",
                "stderr": f"Konfigurasi salah: {e}", "command": command}

    log.info("RUN (%s) cwd=%s :: %s", MODE, cwd or "-", command)
    start = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {"success": proc.returncode == 0, "exit_code": proc.returncode,
                "stdout": _truncate(proc.stdout), "stderr": _truncate(proc.stderr),
                "duration_sec": round(time.monotonic() - start, 2),
                "mode": MODE, "command": command}
    except subprocess.TimeoutExpired as e:
        return {"success": False, "timeout": True, "exit_code": None,
                "stdout": _truncate(e.stdout if isinstance(e.stdout, str) else ""),
                "stderr": f"TIMEOUT setelah {timeout}s. "
                          f"{_truncate(e.stderr if isinstance(e.stderr, str) else '')}",
                "duration_sec": timeout, "command": command}
    except Exception as e:
        return {"success": False, "exit_code": None, "stdout": "",
                "stderr": f"ERROR: {e}", "command": command}


# ---------------------------------------------------------------------------
# OUTPUT INTELLIGENCE: deteksi pola error umum, sertakan hint untuk Claude.
# Tidak mengubah perilaku — hanya menambah field `_analysis` ke result dict
# agar Claude bisa merespons kegagalan dengan lebih cerdas.
# ---------------------------------------------------------------------------
_ERROR_PATTERNS = [
    # --- Database (spesifik SQLSTATE dulu, sebelum pola umum "Connection refused") ---
    # Format: (regex, error_type, hint_text, suggested_commands)
    # suggested_commands opsional — tuple 3-elem (tanpa saran) tetap didukung.
    (r"SQLSTATE\[HY000\] \[1045\]|Access denied for user", "db_auth",
     "Autentikasi DB gagal. Cek: cat .env | grep DB_PASSWORD, lalu mysql -u<user> -p secara manual.",
     [{"cmd": "cat .env | grep DB_", "risk": "AMAN"},
      {"cmd": "mysql -u$DB_USERNAME -p -e 'SELECT 1'", "risk": "AMAN"}]),
    (r"SQLSTATE\[HY000\] \[2002\]|Can't connect to .* MySQL", "db_conn",
     "Tidak bisa konek ke database. Cek: systemctl status mysql && cat .env | grep DB_",
     [{"cmd": "systemctl status mysql", "risk": "AMAN"},
      {"cmd": "cat .env | grep DB_", "risk": "AMAN"},
      {"cmd": "systemctl restart mysql", "risk": "SEDANG"}]),
    (r"SQLSTATE\[42S02\]|Table .+ doesn't exist|Base table .+ not found", "db_table_missing",
     "Tabel DB tidak ada. Jalankan: php artisan migrate --force.",
     [{"cmd": "php artisan migrate:status", "risk": "AMAN"},
      {"cmd": "php artisan migrate --force", "risk": "SEDANG"}]),
    (r"SQLSTATE\[42S22\]|Unknown column", "db_column_missing",
     "Kolom DB tidak ada. Cek migrasi terbaru; mungkin perlu php artisan migrate.",
     [{"cmd": "php artisan migrate:status", "risk": "AMAN"},
      {"cmd": "php artisan migrate --force", "risk": "SEDANG"}]),
    (r"SQLSTATE\[23000\]|Duplicate entry|Integrity constraint", "db_constraint",
     "Pelanggaran constraint DB (duplikat/FK). Cek data konflik sebelum retry."),
    (r"Too many connections|max_connections", "db_max_conn",
     "Koneksi DB penuh. Cek: mysql -e 'SHOW PROCESSLIST'.",
     [{"cmd": "mysql -e 'SHOW PROCESSLIST'", "risk": "AMAN"}]),
    (r"lock wait timeout|Deadlock found", "db_lock",
     "Deadlock/lock timeout. Cek: mysql -e 'SHOW ENGINE INNODB STATUS' | grep -A5 DEADLOCK.",
     [{"cmd": "mysql -e 'SHOW ENGINE INNODB STATUS' | grep -A5 DEADLOCK", "risk": "AMAN"}]),
    (r"SQLSTATE", "db_error",
     "Error database. Periksa pesan SQLSTATE lengkap untuk diagnosis."),
    # --- PHP / Laravel ---
    (r"Class ['\"]?[\w\\]+['\"]? not found|ReflectionException", "class_not_found",
     "Class PHP tidak ditemukan. Jalankan: composer dump-autoload atau composer install.",
     [{"cmd": "composer dump-autoload", "risk": "RENDAH"},
      {"cmd": "composer install --no-dev", "risk": "SEDANG"}]),
    (r"PHP Fatal error|PHP Parse error|Uncaught (?:Error|Exception)", "php_fatal",
     "Error fatal PHP. Periksa file & baris yang disebutkan di pesan error."),
    (r"Allowed memory size .+ exhausted", "php_oom",
     "PHP kehabisan memori. Naikkan memory_limit di php.ini atau jalankan di CLI.",
     [{"cmd": "php -i | grep memory_limit", "risk": "AMAN"},
      {"cmd": "free -h", "risk": "AMAN"}]),
    (r"max_execution_time|Maximum execution time .+ exceeded", "php_timeout",
     "Timeout PHP. Di CLI tidak ada batas; atau naikkan max_execution_time di php.ini.",
     [{"cmd": "php -i | grep max_execution_time", "risk": "AMAN"}]),
    (r"Your lock file does not contain a compatible set|Composer detected issues", "composer_lock",
     "composer.lock tidak sinkron. Jalankan: composer update (bukan install).",
     [{"cmd": "composer validate", "risk": "AMAN"},
      {"cmd": "composer update", "risk": "SEDANG"}]),
    # --- Sistem ---
    (r"No space left on device", "disk_full",
     "Disk penuh. Jalankan: df -h && du -sh /var/log/* /tmp/* | sort -rh | head",
     [{"cmd": "df -h", "risk": "AMAN"},
      {"cmd": "du -sh /var/log/* /tmp/* | sort -rh | head", "risk": "AMAN"},
      {"cmd": "find /var/log -name '*.gz' -mtime +30 -delete", "risk": "TINGGI"}]),
    (r"Out of memory|oom-kill|Cannot allocate memory|SIGKILL|signal 9|Killed", "killed",
     "Proses di-kill (kemungkinan OOM). Cek: dmesg | tail -20 && free -h.",
     [{"cmd": "dmesg | tail -20", "risk": "AMAN"},
      {"cmd": "free -h", "risk": "AMAN"}]),
    (r"Permission denied", "permission",
     "Akses ditolak. Periksa kepemilikan (ls -la) dan izin user.",
     [{"cmd": "ls -la", "risk": "AMAN"},
      {"cmd": "id", "risk": "AMAN"}]),
    (r"command not found", "missing_cmd",
     "Perintah tidak ditemukan. Cek: which <cmd> atau apt list --installed | grep <pkg>."),
    (r"Connection refused", "conn_refused",
     "Koneksi ditolak. Cek apakah service jalan: systemctl status <service>.",
     [{"cmd": "ss -tlnp", "risk": "AMAN"}]),
    (r"failed to open stream|No such file or directory", "file_not_found",
     "File/directory tidak ditemukan. Verifikasi path: ls -la <path>."),
    (r"Address already in use|port .+ already in use", "port_in_use",
     "Port sudah terpakai. Cek: ss -tlnp | grep <port>.",
     [{"cmd": "ss -tlnp", "risk": "AMAN"}]),
    # --- Tool-specific ---
    (r"nginx: \[emerg\]|nginx:.*test failed|nginx:.*configuration.*failed", "nginx_config",
     "Config Nginx error. Jalankan: nginx -t untuk detail. JANGAN reload sebelum config valid.",
     [{"cmd": "nginx -t", "risk": "AMAN"},
      {"cmd": "tail -20 /var/log/nginx/error.log", "risk": "AMAN"}]),
    (r"SSL.+error|certificate.+expired|SSL_ERROR", "ssl",
     "Masalah SSL. Cek: certbot certificates.",
     [{"cmd": "certbot certificates", "risk": "AMAN"},
      {"cmd": "openssl s_client -connect localhost:443 2>/dev/null | head -5", "risk": "AMAN"}]),
    (r"npm ERR!", "npm_error",
     "Error npm. Coba: rm -rf node_modules && npm ci.",
     [{"cmd": "node -v && npm -v", "risk": "AMAN"},
      {"cmd": "rm -rf node_modules && npm ci", "risk": "SEDANG"}]),
]

# Counter error per-session (reset tiap server di-spawn ulang).
_error_counts: dict[str, int] = {}


def _analyze_output(result: dict) -> dict:
    """Scan stdout+stderr untuk pola error umum. Kembalikan hints + suggested_commands
    untuk Claude. Lacak frekuensi error per-session; tandai jika berulang."""
    if result.get("success"):
        return {}
    hints: list[str] = []
    suggestions: list[dict] = []
    error_type = None
    if result.get("timeout"):
        hints.append("Perintah timeout. Naikkan timeout atau pecah jadi langkah lebih kecil.")
        error_type = "timeout"
    combined = ((result.get("stdout") or "") + " " + (result.get("stderr") or "")).strip()
    if combined:
        for entry in _ERROR_PATTERNS:
            pattern, etype, hint = entry[0], entry[1], entry[2]
            if re.search(pattern, combined, re.IGNORECASE):
                if not error_type:
                    error_type = etype
                    if len(entry) > 3 and entry[3]:
                        suggestions = entry[3]
                hints.append(hint)
    if not hints and result.get("exit_code") and result["exit_code"] > 0:
        hints.append(f"Exit code {result['exit_code']}. Baca stderr untuk diagnosis.")
        error_type = error_type or "generic_failure"
    if not hints:
        return {}
    analysis: dict = {"error_type": error_type, "hints": hints}
    if suggestions:
        analysis["suggested_commands"] = suggestions
    if error_type:
        _error_counts[error_type] = _error_counts.get(error_type, 0) + 1
        if _error_counts[error_type] >= 3:
            analysis["recurring"] = True
            analysis["recurring_hint"] = (
                f"Error '{error_type}' sudah terjadi {_error_counts[error_type]}x sesi ini. "
                "Pertimbangkan investigasi root cause atau memory_write untuk catat quirk ini.")
    return analysis


def _build_summary(result: dict) -> str:
    cmd = result.get("command", "?")
    dur = result.get("duration_sec", "")
    dur_str = f" ({dur}s)" if dur else ""
    if result.get("blocked"):
        return f"⛔ DITOLAK — {cmd}"
    if result.get("blocked_by_mode"):
        return f"⛔ DIBLOKIR mode {result.get('mode', '?')} — {cmd}"
    if result.get("timeout"):
        return f"⏱ TIMEOUT — {cmd}{dur_str}"
    analysis = result.get("_analysis", {})
    etype = analysis.get("error_type", "")
    if result.get("success"):
        return f"✓ OK — {cmd}{dur_str}"
    tag = f" [{etype}]" if etype else ""
    return f"✗ GAGAL (exit {result.get('exit_code', '?')}){tag} — {cmd}{dur_str}"


# ---------------------------------------------------------------------------
# INOVASI 2 — Slim Envelope
# Respons run_command membawa sejumlah field yang nilai-nya konstan di setup
# local SIMURU (mode=local, ssh_target=null, agent_mode=local) atau tidak
# informatif untuk READ command (stderr kosong, command sudah diketahui model).
# WRITE command tetap menerima envelope penuh untuk audit trail yang lengkap.
# ---------------------------------------------------------------------------
_WRITE_CMDS_RE = re.compile(
    r"\b("
    r"systemctl\s+(restart|start|stop|reload|enable|disable)\b"
    r"|git\s+(pull|push|reset|checkout|merge|commit|rebase|clean|rm)\b"
    r"|mv\b|cp\b|rm\b|chmod\b|chown\b|mkdir\b|touch\b|tee\b"
    r"|sed\s+-i\b"
    r"|mysql\b|mysqldump\b|mysqladmin\b|mysqlcheck\b"
    r"|composer\s+(install|update|require|remove|dump-autoload)\b"
    r"|apt(?:-get)?\s+(install|remove|purge|upgrade|autoremove|dist-upgrade)\b"
    r"|pip[23]?\s+(install|uninstall|upgrade)\b"
    r"|npm\s+(install|uninstall|update|ci|run|rebuild)\b"
    r"|yarn\s+(add|remove|install|upgrade)\b"
    r"|sudo\s+/usr/local/bin\b"
    r"|truncate\b|dd\b|mkfs\b|mount\b|umount\b"
    r"|crontab\b|at\b"
    r")",
    re.IGNORECASE,
)

# Field yang bernilai konstan di setup local — tidak memberikan informasi baru
_SLIM_DROP_ALWAYS = frozenset({"mode", "agent_mode", "ssh_target"})


def _slim(result: dict, command: str) -> dict:
    """Kembalikan envelope tipis untuk READ command; penuh untuk WRITE/GAGAL.

    Aturan strip:
    - WRITE atau gagal: kembalikan utuh (audit trail prioritas).
    - READ sukses: buang mode/agent_mode/ssh_target (konstan) + stderr kosong
      + field 'command' (model sudah tahu apa yang dipanggil).
    """
    if _WRITE_CMDS_RE.search(command) or not result.get("success"):
        return result
    return {
        k: v for k, v in result.items()
        if k not in _SLIM_DROP_ALWAYS
        and not (k == "stderr" and not v)
        and k != "command"
    }


# ---------------------------------------------------------------------------
# AUDIT LOG: jejak eksekusi append-only — JANGAN dihapus / compact.
# Berbeda dari memory (yang di-fold/compact), audit log adalah catatan
# kronologis permanen untuk investigasi insiden.
# ---------------------------------------------------------------------------
def _audit(tool: str, summary: str, result: dict) -> None:
    if not AUDIT_ENABLED:
        return
    record = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tool": tool, "summary": summary[:500],
        "success": result.get("success"), "exit_code": result.get("exit_code"),
        "duration_sec": result.get("duration_sec"), "mode": MODE,
        "project": PROJECT_NAME or None,
    }
    try:
        os.makedirs(MEMORY_DIR, mode=0o700, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        log.warning("gagal tulis audit log", exc_info=True)


# ---------------------------------------------------------------------------
# SESSION HISTORY: riwayat in-memory sesi ini (hilang saat server respawn).
# Melengkapi audit log (persisten) dengan riwayat cepat tanpa baca file.
# ---------------------------------------------------------------------------
_SESSION_LOG: list[dict] = []


def _session_log(tool: str, summary: str, result: dict,
                 pre_state: dict | None = None, rollback: list[str] | None = None) -> None:
    entry = {
        "seq": len(_SESSION_LOG) + 1,
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tool": tool, "summary": summary[:200],
        "success": result.get("success"),
        "exit_code": result.get("exit_code"),
        "duration_sec": result.get("duration_sec"),
    }
    if pre_state:
        entry["_pre_state"] = pre_state
    if rollback:
        entry["_rollback"] = rollback
    _SESSION_LOG.append(entry)


# ---------------------------------------------------------------------------
# ROLLBACK TRACKING: tangkap state sebelum command destruktif, sarankan undo
# ---------------------------------------------------------------------------
def _capture_pre_state(command: str, cwd: str | None) -> dict:
    """Tangkap state relevan sebelum eksekusi command, untuk rollback."""
    state: dict = {}
    if re.search(r'\bgit\s+(reset|checkout|merge|rebase|pull|clean)\b', command) and cwd:
        r = _run("git rev-parse HEAD 2>/dev/null", cwd, 10)
        if r.get("success"):
            state["git_head"] = r["stdout"].strip()
    if re.search(r'\bartisan\s+migrate\b', command) and cwd:
        r = _run("php artisan migrate:status 2>/dev/null | tail -3", cwd, 15)
        if r.get("success"):
            state["migrate_tail"] = r["stdout"].strip()
    svc = re.search(r'systemctl\s+(?:restart|stop|reload)\s+(\S+)', command)
    if svc:
        name = svc.group(1).strip("'\"")
        r = _run(f"systemctl is-active {shlex.quote(name)} 2>/dev/null", None, 10)
        if r.get("success"):
            state["svc_was"] = f"{name}={r['stdout'].strip()}"
    return state


def _suggest_rollback(command: str, pre: dict) -> list[str]:
    """Sarankan perintah rollback berdasarkan pre-state."""
    rb: list[str] = []
    head = pre.get("git_head")
    if head:
        rb.append(f"git reset --hard {head}")
    if pre.get("migrate_tail"):
        rb.append("php artisan migrate:rollback --step=1 --force")
    svc = pre.get("svc_was", "")
    if "=active" in svc:
        name = svc.split("=")[0]
        rb.append(f"sudo -n systemctl restart {shlex.quote(name)}")
    if re.search(r'\bcomposer\s+(install|update)\b', command):
        rb.append("composer install --no-dev --optimize-autoloader (ulangi setelah perbaiki)")
    if re.search(r'\brm\s', command):
        rb.append("(penghapusan file tidak bisa di-undo — cek backup)")
    return rb


# ---------------------------------------------------------------------------
# MEMORY: penyimpanan append-only JSONL + fold (last-write-wins, tombstone)
# CATATAN: memory selalu di mesin tempat server ini berjalan (proses Python ini),
# yaitu sisi yang sama dengan mode `local`. Tidak lewat _run, jadi tak terpengaruh
# sandbox PROJECT_ROOT maupun DEPLOY_MODE.
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:60] or "x"


# ---------------------------------------------------------------------------
# SEMANTIC SEARCH ENGINE — TF-IDF ringan, stdlib-only, tanpa dependency.
# Dipakai oleh memory_recall (ranking cerdas) dan memory_write (deteksi duplikat).
# ---------------------------------------------------------------------------
_STOP_WORDS = frozenset({
    "dan", "di", "ke", "dari", "yang", "ini", "itu", "dengan", "untuk",
    "pada", "adalah", "atau", "juga", "sudah", "akan", "bisa", "ada",
    "tidak", "ya", "oleh", "jika", "maka", "saat", "telah", "bagi",
    "harus", "dalam", "secara", "agar", "karena", "belum", "lagi",
    "masih", "lebih", "sangat", "semua", "hanya", "saja", "setiap",
    "the", "is", "are", "was", "be", "been", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "and", "but", "or", "not", "so", "if", "then", "an", "as",
    "this", "that", "these", "those", "it", "its", "may", "might",
})
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*[a-z0-9]|[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [w for w in _TOKEN_RE.findall(text.lower()) if w not in _STOP_WORDS]


def _entry_text(rec: dict) -> str:
    parts = [rec.get("key") or "", rec.get("text") or ""]
    parts.extend(str(t) for t in (rec.get("tags") or []))
    return " ".join(parts)


def _compute_idf(corpus: list[list[str]]) -> dict[str, float]:
    n = len(corpus)
    if n == 0:
        return {}
    df: dict[str, int] = {}
    for doc in corpus:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1
    return {term: math.log((n + 1) / (freq + 1)) + 1.0 for term, freq in df.items()}


def _tfidf_score(query_tokens: list[str], doc_tokens: list[str],
                 idf: dict[str, float]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    doc_tf = Counter(doc_tokens)
    doc_len = len(doc_tokens)
    score = 0.0
    for qt in set(query_tokens):
        if qt in doc_tf:
            tf = doc_tf[qt] / doc_len
            score += tf * idf.get(qt, 1.0)
    if score > 0:
        score /= max(math.sqrt(len(set(query_tokens))), 1.0)
    return score


def _word_overlap(text_a: str, text_b: str) -> float:
    a = set(_tokenize(text_a))
    b = set(_tokenize(text_b))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _find_similar(ns: str, new_id: str, key: str, text: str,
                  threshold: float = 0.45) -> list[dict]:
    try:
        live = _cortex_fold() if _is_cortex_ns(ns) else _mem_fold()
    except Exception:
        return []
    new_text = f"{key} {text}"
    results = []
    for rid, rec in live.items():
        if rid == new_id or rec.get("ns") != ns:
            continue
        existing_text = f"{rec.get('key') or ''} {rec.get('text') or ''}"
        overlap = _word_overlap(new_text, existing_text)
        if overlap >= threshold:
            results.append({"id": rid, "key": rec.get("key"), "text": rec.get("text", "")[:120],
                            "overlap": round(overlap, 2)})
    results.sort(key=lambda x: x["overlap"], reverse=True)
    return results[:5]


def _is_stale(rec: dict, now: datetime) -> bool:
    updated = rec.get("updated_at") or rec.get("created_at") or ""
    if not updated:
        return False
    try:
        age = now - datetime.fromisoformat(updated)
        return age.days >= STALE_DAYS
    except ValueError:
        return False


def _ensure_store() -> None:
    os.makedirs(MEMORY_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(MEMORY_DIR, 0o700)
    except OSError:
        pass
    if not os.path.exists(MEMORY_FILE):
        # buat file kosong dengan perm ketat
        fd = os.open(MEMORY_FILE, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)


def _mem_append(record: dict) -> None:
    """Tulis satu record sebagai satu baris JSON. O_APPEND + flock = aman konkuren."""
    _ensure_store()
    _fold_invalidate()
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(MEMORY_FILE, os.O_WRONLY | os.O_APPEND)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _is_expired(rec: dict, now: datetime) -> bool:
    exp = rec.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) <= now
    except ValueError:
        return False


_fold_cache: dict[str, dict] | None = None


def _fold_invalidate() -> None:
    global _fold_cache
    _fold_cache = None


def _mem_fold() -> dict[str, dict]:
    """Baca seluruh log, lipat jadi state terkini per id (last-write-wins).
    Buang record ber-deleted=True dan yang sudah kedaluwarsa.
    Hasil di-cache dalam sesi; invalidasi otomatis saat append/compact."""
    global _fold_cache
    if _fold_cache is not None:
        return _fold_cache
    if not os.path.exists(MEMORY_FILE):
        _fold_cache = {}
        return _fold_cache
    state: dict[str, dict] = {}
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # baris korup -> lewati, jangan jatuhkan server
                rid = rec.get("id")
                if rid:
                    state[rid] = rec
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    now = datetime.now(timezone.utc)
    _fold_cache = {rid: r for rid, r in state.items()
                   if not r.get("deleted") and not _is_expired(r, now)}
    return _fold_cache


def _mem_compact(live: dict[str, dict]) -> None:
    """Tulis ulang log hanya berisi record hidup (atomic via temp + os.replace)."""
    _ensure_store()
    _fold_invalidate()
    tmp = MEMORY_FILE + ".tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        for rec in live.values():
            os.write(fd, (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, MEMORY_FILE)
    log.info("memory compacted -> %d entri hidup", len(live))


# ---------------------------------------------------------------------------
# CORTEX: global memory lintas-project (consciousness layer).
# Menyimpan profil operator, fakta cross-project, dan event journal.
# Terpisah dari per-project memory — selalu di-inject ke semua sesi.
# ---------------------------------------------------------------------------
_cortex_fold_cache: dict[str, dict] | None = None


def _cortex_fold_invalidate() -> None:
    global _cortex_fold_cache
    _cortex_fold_cache = None


def _cortex_ensure_store() -> None:
    os.makedirs(GLOBAL_MEMORY_DIR, mode=0o700, exist_ok=True)
    if not os.path.exists(GLOBAL_MEMORY_FILE):
        fd = os.open(GLOBAL_MEMORY_FILE, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)


def _cortex_append(record: dict) -> None:
    _cortex_ensure_store()
    _cortex_fold_invalidate()
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(GLOBAL_MEMORY_FILE, os.O_WRONLY | os.O_APPEND)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _cortex_fold() -> dict[str, dict]:
    global _cortex_fold_cache
    if _cortex_fold_cache is not None:
        return _cortex_fold_cache
    if not os.path.exists(GLOBAL_MEMORY_FILE):
        _cortex_fold_cache = {}
        return _cortex_fold_cache
    state: dict[str, dict] = {}
    try:
        with open(GLOBAL_MEMORY_FILE, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    rid = rec.get("id")
                    if rid:
                        state[rid] = rec
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    now = datetime.now(timezone.utc)
    _cortex_fold_cache = {rid: r for rid, r in state.items()
                          if not r.get("deleted") and not _is_expired(r, now)}
    return _cortex_fold_cache


def _cortex_compact(live: dict[str, dict]) -> None:
    _cortex_ensure_store()
    _cortex_fold_invalidate()
    tmp = GLOBAL_MEMORY_FILE + ".tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        for rec in live.values():
            os.write(fd, (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, GLOBAL_MEMORY_FILE)
    log.info("cortex compacted -> %d entri hidup", len(live))


def _is_cortex_ns(ns: str) -> bool:
    return ns in CORTEX_NAMESPACES


# ---------------------------------------------------------------------------
# EVENT JOURNAL: log otomatis aktivitas lintas-project.
# Append-only, time-series, auto-pruned by age saat baca.
# ---------------------------------------------------------------------------
def _event_append(project: str, event: str, detail: str,
                  severity: str = "info") -> None:
    if not project:
        return
    _cortex_ensure_store()
    record = {
        "ts": _now_iso(), "project": project,
        "event": event, "detail": detail[:500],
        "severity": severity,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(GLOBAL_EVENTS_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _events_read(hours: int = 24, exclude_project: str = "",
                 project_filter: str = "") -> list[dict]:
    if not os.path.exists(GLOBAL_EVENTS_FILE):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, hours))).isoformat()
    events: list[dict] = []
    try:
        with open(GLOBAL_EVENTS_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("ts", "") < cutoff:
                    continue
                if exclude_project and rec.get("project") == exclude_project:
                    continue
                if project_filter and rec.get("project") != project_filter:
                    continue
                events.append(rec)
    except OSError:
        pass
    return events[-200:]


# ---------------------------------------------------------------------------
# ORCHESTRATOR: sistem saraf otonom ODIN.
# Tiga fungsi berjalan otomatis di setiap tool call utama:
#   _enrich_context  — thalamus: recall memory yang relevan, attach ke result
#   _suggest_next    — cerebellum: sarankan langkah berikutnya berdasar pola
#   _check_attention — reticular activating system: flag kondisi penting
# Dipanggil via _orchestrate() yang di-inject di akhir setiap tool utama.
# ---------------------------------------------------------------------------
def _enrich_context(tool_name: str, args: dict) -> list[dict]:
    """Auto-recall memory yang relevan untuk tool call ini."""
    terms: list[str] = []
    if tool_name == "run_command":
        terms = _tokenize(args.get("command", ""))[:8]
    elif tool_name == "laravel_deploy":
        terms = _tokenize("deploy backup migration artisan composer")
    elif tool_name == "service_action":
        terms = _tokenize(f"{args.get('service', '')} {args.get('action', '')} service")
    elif tool_name == "tail_log":
        terms = _tokenize(args.get("path", ""))
    elif tool_name == "http_health_check":
        terms = _tokenize("health check website http error")
    elif tool_name == "run_tests":
        terms = _tokenize("test testing phpunit pest")
    if not terms:
        return []
    try:
        all_items = list(_cortex_fold().values()) + list(_mem_fold().values())
    except Exception:
        return []
    relevant = [r for r in all_items if r.get("ns") in ("instruction", "cross")]
    if not relevant:
        return []
    terms_set = set(terms)
    scored: list[tuple[int, dict]] = []
    for r in relevant:
        doc_tokens = set(_tokenize(_entry_text(r)))
        overlap = len(terms_set & doc_tokens)
        if overlap > 0:
            scored.append((overlap, r))
    scored.sort(key=lambda x: -x[0])
    return [{"id": r.get("id"), "key": r.get("key"),
             "text": r.get("text", "")[:200], "ns": r.get("ns")}
            for _, r in scored[:3]]


_NEXT_STEP_MAP: dict[str, list[dict]] = {
    "laravel_deploy:ok": [
        {"tool": "http_health_check", "reason": "verifikasi website setelah deploy"},
    ],
    "laravel_deploy:fail": [
        {"tool": "rollback_plan", "reason": "lihat opsi rollback"},
        {"tool": "tail_log", "reason": "cek error log Laravel"},
    ],
    "service_action:restart": [
        {"tool": "http_health_check", "reason": "verifikasi setelah restart service"},
    ],
    "service_action:reload": [
        {"tool": "http_health_check", "reason": "verifikasi setelah reload service"},
    ],
    "http_health_check:fail": [
        {"tool": "tail_log", "reason": "cek error log untuk detail"},
        {"tool": "service_action", "reason": "cek status service (nginx/php-fpm)"},
    ],
    "run_tests:fail": [
        {"tool": "tail_log", "reason": "cek log untuk detail error"},
    ],
}


def _suggest_next(tool_name: str, args: dict, result: dict) -> list[dict]:
    """Sarankan langkah berikutnya berdasar pola tool + outcome."""
    success = result.get("success", True)
    suggestions: list[dict] = []

    if tool_name in ("laravel_deploy",):
        key = f"{tool_name}:{'ok' if success else 'fail'}"
        suggestions.extend(_NEXT_STEP_MAP.get(key, []))

    elif tool_name == "service_action":
        action = args.get("action", "")
        if action in ("restart", "reload", "start") and success:
            suggestions.extend(_NEXT_STEP_MAP.get(f"service_action:{action}", []))

    elif tool_name == "http_health_check" and not success:
        suggestions.extend(_NEXT_STEP_MAP.get("http_health_check:fail", []))

    elif tool_name == "run_tests" and not success:
        suggestions.extend(_NEXT_STEP_MAP.get("run_tests:fail", []))

    elif tool_name == "run_command" and result.get("_analysis"):
        etype = result["_analysis"].get("error_type", "")
        if "db" in etype or "sql" in etype:
            suggestions.append({"tool": "service_action",
                                "reason": f"cek status database (error: {etype})"})
        if "permission" in etype:
            suggestions.append({"tool": "run_command",
                                "reason": "cek ownership/permission file terkait"})
        if "disk" in etype:
            suggestions.append({"tool": "run_command",
                                "reason": "cek disk usage (df -h) dan bersihkan jika perlu"})
        if "nginx" in etype:
            suggestions.append({"tool": "run_command",
                                "reason": "cek nginx config (nginx -t)"})

    return suggestions[:3]


def _check_attention(tool_name: str, args: dict, result: dict) -> list[dict]:
    """Flag kondisi yang perlu perhatian Claude."""
    flags: list[dict] = []
    analysis = result.get("_analysis")
    if analysis and analysis.get("recurring"):
        etype = analysis.get("error_type", "?")
        flags.append({
            "level": "warn",
            "msg": f"Error '{etype}' BERULANG — pertimbangkan investigasi root cause "
                   "atau memory_write untuk catat workaround"})
    try:
        events = _events_read(hours=6, exclude_project=PROJECT_NAME)
        warn_events = [e for e in events if e.get("severity") in ("warn", "error")]
        for ev in warn_events[-2:]:
            flags.append({
                "level": "info",
                "msg": f"[{ev.get('project', '?')}] {ev.get('event', '')}: "
                       f"{ev.get('detail', '')[:80]}"})
    except Exception:
        pass
    return flags[:5]


def _orchestrate(tool_name: str, args: dict, result: dict) -> None:
    """Sistem saraf otonom: enrich result dengan context, suggestions, attention.
    Dipanggil di akhir setiap tool execution utama. Tidak mengubah state."""
    try:
        ctx = _enrich_context(tool_name, args)
        if ctx:
            result["_memory_context"] = ctx
        nxt = _suggest_next(tool_name, args, result)
        if nxt:
            result["_suggested_next"] = nxt
        att = _check_attention(tool_name, args, result)
        if att:
            result["_attention"] = att
    except Exception:
        pass


def _build_memory_digest() -> str:
    """Cortex (global) + project memory + cross-project events, budget-aware."""
    now = datetime.now(timezone.utc)

    def fmt(rec: dict) -> str:
        key = rec.get("key")
        tags = rec.get("tags") or []
        tagstr = f"  [{', '.join(tags)}]" if tags else ""
        head = f"{key}: " if key else "- "
        stale = " [STALE?]" if _is_stale(rec, now) else ""
        return f"- {head}{rec.get('text', '')}{tagstr}{stale}"

    def _sort_key(r: dict):
        return (0 if r.get("pinned") else 1,
                r.get("updated_at") or r.get("created_at") or "")

    try:
        cortex_live = list(_cortex_fold().values())
    except Exception:
        cortex_live = []
    try:
        project_live = list(_mem_fold().values())
    except Exception:
        project_live = []
    try:
        events = _events_read(hours=EVENTS_DIGEST_HOURS, exclude_project=PROJECT_NAME)
    except Exception:
        events = []

    if not cortex_live and not project_live and not events:
        return ""

    cortex_by_ns: dict[str, list] = {ns: [] for ns in CORTEX_NAMESPACES}
    for r in cortex_live:
        ns = r.get("ns")
        if ns in cortex_by_ns:
            cortex_by_ns[ns].append(r)

    project_by_ns: dict[str, list] = {ns: [] for ns in PROJECT_NAMESPACES}
    for r in project_live:
        ns = r.get("ns")
        if ns in project_by_ns:
            project_by_ns[ns].append(r)
        elif ns == "profile":
            cortex_by_ns.setdefault("profile", []).append(r)

    footer = "\nPakai memory_recall untuk detail, memory_write untuk simpan, cortex_log untuk catat event."
    budget = max(500, DIGEST_BUDGET) - len(footer)
    sections: list[str] = []
    used = 0
    truncated = 0

    def _add_section(header_text: str, items: list, pin_only: bool = False):
        nonlocal used, truncated
        if pin_only:
            items = [r for r in items if r.get("pinned")]
        if not items:
            return
        items = sorted(items, key=_sort_key, reverse=True)
        lines: list[str] = []
        for r in items:
            line = fmt(r)
            if used + len(header_text) + len(line) + 2 > budget:
                truncated += 1
                continue
            lines.append(line)
            used += len(line) + 1
        if lines:
            used += len(header_text)
            sections.append(header_text + "\n".join(lines))

    cortex_header = "## ODIN CORTEX (global — berlaku di semua project)\n"
    if cortex_live or events:
        used += len(cortex_header)

    _add_section("### PROFIL OPERATOR\n",
                 cortex_by_ns.get("profile", []))
    _add_section("### CROSS-PROJECT\n",
                 cortex_by_ns.get("cross", []))

    if events:
        ev_header = f"### AKTIVITAS PROJECT LAIN ({EVENTS_DIGEST_HOURS}j terakhir)\n"
        ev_lines: list[str] = []
        for ev in events[-15:]:
            sev = ""
            if ev.get("severity") == "warn":
                sev = " ⚠"
            elif ev.get("severity") == "error":
                sev = " ✗"
            ts_short = ev.get("ts", "")[:16].replace("T", " ")
            line = f"- [{ev.get('project','')}] {ts_short} — {ev.get('event','')}: {ev.get('detail','')[:80]}{sev}"
            if used + len(ev_header) + len(line) + 2 > budget:
                truncated += 1
                continue
            ev_lines.append(line)
            used += len(line) + 1
        if ev_lines:
            used += len(ev_header)
            sections.append(ev_header + "\n".join(ev_lines))

    prj_label = f": {PROJECT_NAME}" if PROJECT_NAME else ""
    project_header = f"\n## MEMORY PROJECT{prj_label}\n"
    if project_live:
        used += len(project_header)

    instructions = project_by_ns.get("instruction", [])
    goal = [r for r in instructions if r.get("key") == "project-goal"]
    non_goal = [r for r in instructions if r.get("key") != "project-goal"]
    if goal:
        goal_text = goal[0].get("text", "")
        goal_line = f"### TUJUAN PROJECT\n{goal_text}\n"
        if used + len(goal_line) <= budget:
            sections.append(goal_line)
            used += len(goal_line)

    _add_section("### INSTRUKSI DURABLE (patuhi)\n", non_goal)
    _add_section("### FAKTA SERVER (pinned)\n",
                 project_by_ns.get("server", []), pin_only=True)

    if not sections:
        return ""

    parts: list[str] = []
    _prj_prefixes = ("### INSTRUKSI", "### FAKTA", "### TUJUAN")
    cortex_sections = [s for s in sections if not any(s.startswith(p) for p in _prj_prefixes)]
    project_sections = [s for s in sections if any(s.startswith(p) for p in _prj_prefixes)]
    if cortex_sections:
        parts.append(cortex_header + "\n\n".join(cortex_sections))
    if project_sections:
        parts.append(project_header + "\n\n".join(project_sections))
    body = "\n".join(parts)
    trunc_note = f"\n\n_({truncated} entry lagi tidak ditampilkan — pakai memory_recall.)_" if truncated else ""
    return f"{body}{trunc_note}{footer}"


def _validate_ns(ns: str) -> str | None:
    if ns not in MEMORY_NAMESPACES:
        return f"ns '{ns}' tidak dikenal. Pilih: {list(MEMORY_NAMESPACES)}"
    return None


# ---------------------------------------------------------------------------
# SERVER PROFILE: inspeksi otomatis + deteksi tipe + stack + derive mode
# Dijalankan saat startup DAN on-demand via tool inspect_server.
# ---------------------------------------------------------------------------
_PROFILE: dict = {}
_CURRENT_MODE: str = "deploy"
_MODE_LEVELS = ("setup", "deploy", "production")


def _parse_sections(output: str) -> dict[str, str]:
    """Parse output dengan pemisah @@KEY@@."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in output.split("\n"):
        s = line.strip()
        if s.startswith("@@") and s.endswith("@@") and len(s) > 4:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = s.strip("@")
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _inspect_base() -> dict:
    """Inspeksi informasi dasar sistem."""
    r = _run(
        "echo '@@OS@@'; lsb_release -ds 2>/dev/null || (. /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\") || echo unknown; "
        "echo '@@KERNEL@@'; uname -r 2>/dev/null || echo unknown; "
        "echo '@@UPTIME@@'; uptime -s 2>/dev/null || echo unknown; "
        "echo '@@DISK@@'; df --output=pcent / 2>/dev/null | tail -1 || df -h / | awk 'NR==2{print $5}'; "
        "echo '@@MEM_PCT@@'; free 2>/dev/null | awk '/^Mem:/{printf \"%.0f\", $3/$2*100}' || echo 0; "
        "echo '@@MEM@@'; free -m 2>/dev/null | awk '/^Mem:/{printf \"%d/%dMB\", $3, $2}' || echo unknown; "
        "echo '@@UFW@@'; ufw status 2>/dev/null | head -1 || echo not-installed; "
        "echo '@@F2B@@'; systemctl is-active fail2ban 2>/dev/null || echo not-installed; "
        "echo '@@SSHPORT@@'; grep -E '^Port ' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' || echo 22; "
        "echo '@@SSHAUTH@@'; grep -E '^PasswordAuthentication ' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' || echo unknown; "
        "echo '@@CRON@@'; crontab -l 2>/dev/null | grep -cv '^#\\|^$' || echo 0; "
        "echo '@@USERS@@'; getent passwd 2>/dev/null | awk -F: '$7 ~ /\\/(bash|sh|zsh)$/ {print $1}' | paste -sd, || echo unknown",
        None, 30)
    s = _parse_sections(r.get("stdout", ""))
    uptime_days = 0
    up = s.get("UPTIME", "")
    if up and up != "unknown":
        try:
            boot = datetime.strptime(up.strip(), "%Y-%m-%d %H:%M:%S")
            uptime_days = (datetime.now() - boot).days
        except (ValueError, TypeError):
            pass
    dp = s.get("DISK", "0").strip().rstrip("%").strip()
    return {
        "os": s.get("OS", "unknown"), "kernel": s.get("KERNEL", "unknown"),
        "uptime_days": uptime_days,
        "disk_pct": int(dp) if dp.isdigit() else 0,
        "memory": s.get("MEM", "unknown"),
        "memory_pct": int(s.get("MEM_PCT", "0").strip() or "0"),
        "firewall": "active" if "active" in s.get("UFW", "").lower() else s.get("UFW", "unknown"),
        "fail2ban": s.get("F2B", "not-installed"),
        "ssh_port": s.get("SSHPORT", "22").strip(),
        "ssh_auth": "password" if s.get("SSHAUTH", "").strip().lower() == "yes" else "key",
        "cron_jobs": int(s.get("CRON", "0").strip() or "0"),
        "login_users": [u.strip() for u in s.get("USERS", "").split(",") if u.strip()],
    }


def _detect_type() -> tuple[str, dict[str, bool]]:
    """Deteksi tipe server dari binary terinstall."""
    r = _run(
        "echo '@@NGINX@@'; which nginx 2>/dev/null && echo found || echo none; "
        "echo '@@APACHE@@'; (which apache2 || which httpd) 2>/dev/null && echo found || echo none; "
        "echo '@@PHP@@'; which php 2>/dev/null && echo found || echo none; "
        "echo '@@NODE@@'; which node 2>/dev/null && echo found || echo none; "
        "echo '@@MYSQL@@'; which mysql 2>/dev/null && echo found || echo none; "
        "echo '@@PGSQL@@'; which psql 2>/dev/null && echo found || echo none; "
        "echo '@@MONGOD@@'; which mongod 2>/dev/null && echo found || echo none; "
        "echo '@@DOCKER@@'; which docker 2>/dev/null && echo found || echo none; "
        "echo '@@CONTAINERS@@'; docker ps -q 2>/dev/null | wc -l || echo 0",
        None, 15)
    s = _parse_sections(r.get("stdout", ""))
    found = {k: ("found" in v) for k, v in s.items() if k != "CONTAINERS"}
    has_web = found.get("NGINX") or found.get("APACHE")
    has_runtime = found.get("PHP") or found.get("NODE")
    has_db = found.get("MYSQL") or found.get("PGSQL") or found.get("MONGOD")
    has_docker = found.get("DOCKER")
    containers = int(s.get("CONTAINERS", "0").strip() or "0")
    if has_web and has_runtime:
        return "web-app", found
    if has_docker and containers > 0:
        return "container", found
    if has_db and not has_web:
        return "database", found
    return "general", found


def _inspect_stacks_web() -> dict:
    """Inspeksi stack web-app: web server, PHP, DB, cache, SSL, dll."""
    r = _run(
        "echo '@@NV@@'; nginx -v 2>&1 || echo none; "
        "echo '@@NS@@'; systemctl is-active nginx 2>/dev/null || echo x; "
        "echo '@@SITES@@'; ls /etc/nginx/sites-enabled/ 2>/dev/null | wc -l || echo 0; "
        "echo '@@AV@@'; apache2 -v 2>&1 | head -1 || httpd -v 2>&1 | head -1 || echo none; "
        "echo '@@AS@@'; systemctl is-active apache2 2>/dev/null || systemctl is-active httpd 2>/dev/null || echo x; "
        "echo '@@PV@@'; php -v 2>/dev/null | head -1 || echo none; "
        "echo '@@FPM@@'; systemctl list-units --type=service --state=running 2>/dev/null "
        "| grep -o 'php[0-9.]*-fpm' | head -1 || echo none; "
        "echo '@@PMODS@@'; php -m 2>/dev/null | tr '\\n' ',' || echo none; "
        "echo '@@CV@@'; composer -V 2>/dev/null | head -1 || echo none; "
        "echo '@@NOV@@'; node -v 2>/dev/null || echo none; "
        "echo '@@NPV@@'; npm -v 2>/dev/null || echo none; "
        "echo '@@MV@@'; mysql --version 2>/dev/null || echo none; "
        "echo '@@MS@@'; systemctl is-active mysql 2>/dev/null || systemctl is-active mariadb 2>/dev/null || echo x; "
        "echo '@@PGV@@'; psql --version 2>/dev/null || echo none; "
        "echo '@@PGS@@'; systemctl is-active postgresql 2>/dev/null || echo x; "
        "echo '@@RS@@'; systemctl is-active redis-server 2>/dev/null || systemctl is-active redis 2>/dev/null || echo x; "
        "echo '@@SS@@'; systemctl is-active supervisor 2>/dev/null || echo x; "
        "echo '@@SSL@@'; certbot certificates 2>/dev/null "
        "| grep -E '(Certificate Name|Expiry|Domains)' | head -6 || echo none",
        None, 30)
    s = _parse_sections(r.get("stdout", ""))
    st: dict = {}
    nv = s.get("NV", "none")
    if nv != "none":
        m = re.search(r"nginx/([\d.]+)", nv)
        st["web"] = {"name": "nginx", "version": m.group(1) if m else "?",
                     "status": s.get("NS", "?"), "sites": int(s.get("SITES", "0").strip() or "0")}
    elif s.get("AV", "none") != "none":
        m = re.search(r"Apache/([\d.]+)", s["AV"])
        st["web"] = {"name": "apache", "version": m.group(1) if m else "?",
                     "status": s.get("AS", "?")}
    pv = s.get("PV", "none")
    if pv != "none":
        m = re.search(r"PHP ([\d.]+)", pv)
        mods = s.get("PMODS", "").lower()
        keys = [mod for mod in ["pdo_mysql", "pdo_pgsql", "mbstring", "openssl",
                                "curl", "gd", "redis", "zip", "bcmath"]
                if mod in mods]
        fpm = s.get("FPM", "none")
        st["runtime"] = {"name": "php", "version": m.group(1) if m else "?",
                         "fpm": fpm if fpm != "none" else "not-running", "modules": keys}
    cv = s.get("CV", "none")
    if cv != "none":
        m = re.search(r"([\d.]+)", cv)
        st["composer"] = m.group(1) if m else "installed"
    nov = s.get("NOV", "none")
    if nov != "none":
        st["node"] = {"version": nov.strip().lstrip("v"), "npm": s.get("NPV", "?").strip()}
    mv = s.get("MV", "none")
    pgv = s.get("PGV", "none")
    if mv != "none":
        m = re.search(r"([\d.]+)", mv)
        nm = "mariadb" if "mariadb" in mv.lower() else "mysql"
        st["database"] = {"name": nm, "version": m.group(1) if m else "?", "status": s.get("MS", "?")}
    elif pgv != "none":
        m = re.search(r"([\d.]+)", pgv)
        st["database"] = {"name": "postgresql", "version": m.group(1) if m else "?",
                          "status": s.get("PGS", "?")}
    if s.get("RS", "x") != "x":
        st["cache"] = {"name": "redis", "status": s["RS"]}
    if s.get("SS", "x") != "x":
        st["queue"] = {"name": "supervisor", "status": s["SS"]}
    ssl_raw = s.get("SSL", "none")
    if ssl_raw != "none" and "Certificate" in ssl_raw:
        dm = re.search(r"Domains:\s*(.+)", ssl_raw)
        em = re.search(r"Expiry.*?(\d{4}-\d{2}-\d{2})", ssl_raw)
        st["ssl"] = {"provider": "letsencrypt",
                     "domain": dm.group(1).strip() if dm else "?",
                     "expires": em.group(1) if em else "?"}
    return st


def _inspect_stacks_database() -> dict:
    """Inspeksi stack database server."""
    r = _run(
        "echo '@@MV@@'; mysql --version 2>/dev/null || echo none; "
        "echo '@@MS@@'; systemctl is-active mysql 2>/dev/null || systemctl is-active mariadb 2>/dev/null || echo x; "
        "echo '@@PGV@@'; psql --version 2>/dev/null || echo none; "
        "echo '@@PGS@@'; systemctl is-active postgresql 2>/dev/null || echo x; "
        "echo '@@MGV@@'; mongod --version 2>/dev/null | head -1 || echo none; "
        "echo '@@MGS@@'; systemctl is-active mongod 2>/dev/null || echo x; "
        "echo '@@RS@@'; systemctl is-active redis-server 2>/dev/null "
        "|| systemctl is-active redis 2>/dev/null || echo x; "
        "echo '@@BACKUP@@'; ls -1t /var/backups/*.sql* /var/backups/*/*.sql* "
        "2>/dev/null | head -3 || echo none",
        None, 20)
    s = _parse_sections(r.get("stdout", ""))
    st: dict = {}
    mv, pgv, mgv = s.get("MV", "none"), s.get("PGV", "none"), s.get("MGV", "none")
    if mv != "none":
        m = re.search(r"([\d.]+)", mv)
        nm = "mariadb" if "mariadb" in mv.lower() else "mysql"
        st["database"] = {"name": nm, "version": m.group(1) if m else "?", "status": s.get("MS", "?")}
    elif pgv != "none":
        m = re.search(r"([\d.]+)", pgv)
        st["database"] = {"name": "postgresql", "version": m.group(1) if m else "?",
                          "status": s.get("PGS", "?")}
    elif mgv != "none":
        m = re.search(r"([\d.]+)", mgv)
        st["database"] = {"name": "mongodb", "version": m.group(1) if m else "?",
                          "status": s.get("MGS", "?")}
    if s.get("RS", "x") != "x":
        st["cache"] = {"name": "redis", "status": s["RS"]}
    bk = s.get("BACKUP", "none")
    if bk != "none":
        st["backup_files"] = [f.strip() for f in bk.split("\n") if f.strip()][:3]
    return st


def _inspect_stacks_container() -> dict:
    """Inspeksi stack container host."""
    r = _run(
        "echo '@@DV@@'; docker version --format '{{.Server.Version}}' 2>/dev/null || echo none; "
        "echo '@@DS@@'; systemctl is-active docker 2>/dev/null || echo x; "
        "echo '@@NAMES@@'; docker ps --format '{{.Names}}' 2>/dev/null || echo none; "
        "echo '@@IMGS@@'; docker images -q 2>/dev/null | wc -l || echo 0; "
        "echo '@@COMPOSE@@'; docker compose version --short 2>/dev/null || echo none",
        None, 20)
    s = _parse_sections(r.get("stdout", ""))
    st: dict = {}
    dv = s.get("DV", "none")
    if dv != "none":
        names_raw = s.get("NAMES", "none")
        containers = [c.strip() for c in names_raw.split("\n")
                      if c.strip() and c.strip() != "none"]
        st["docker"] = {
            "version": dv.strip(), "status": s.get("DS", "?"),
            "running_count": len(containers), "containers": containers[:10],
            "images": int(s.get("IMGS", "0").strip() or "0"),
            "compose": s.get("COMPOSE", "none") != "none",
        }
    return st


def _inspect_app(app_path: str) -> dict | None:
    """Inspeksi direktori aplikasi."""
    if not app_path:
        return None
    ap = shlex.quote(app_path)
    r = _run(
        f"echo '@@EXISTS@@'; test -d {ap} && echo yes || echo no; "
        f"echo '@@ENV@@'; test -f {ap}/.env && echo yes || echo no; "
        f"echo '@@VENDOR@@'; test -d {ap}/vendor && echo yes || echo no; "
        f"echo '@@NODEMOD@@'; test -d {ap}/node_modules && echo yes || echo no; "
        f"echo '@@ARTISAN@@'; test -f {ap}/artisan && echo yes || echo no; "
        f"echo '@@MANAGE@@'; test -f {ap}/manage.py && echo yes || echo no; "
        f"echo '@@PKGJSON@@'; test -f {ap}/package.json && echo yes || echo no; "
        f"echo '@@STORAGE@@'; test -w {ap}/storage && echo yes || echo no; "
        f"echo '@@GIT@@'; cd {ap} 2>/dev/null && git log --oneline -1 2>/dev/null || echo none; "
        f"echo '@@BRANCH@@'; cd {ap} 2>/dev/null && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo none; "
        f"echo '@@DIRTY@@'; cd {ap} 2>/dev/null && git status --porcelain 2>/dev/null | wc -l || echo 0",
        None, 15)
    s = _parse_sections(r.get("stdout", ""))
    if s.get("EXISTS") != "yes":
        return {"path": app_path, "exists": False}
    fw = "unknown"
    if s.get("ARTISAN") == "yes":
        fw = "laravel"
    elif s.get("MANAGE") == "yes":
        fw = "django"
    elif s.get("PKGJSON") == "yes":
        fw = "node"
    return {
        "path": app_path, "exists": True, "framework": fw,
        "env_exists": s.get("ENV") == "yes",
        "vendor_exists": s.get("VENDOR") == "yes",
        "node_modules": s.get("NODEMOD") == "yes",
        "storage_writable": s.get("STORAGE") == "yes",
        "git_branch": s.get("BRANCH") if s.get("BRANCH") != "none" else None,
        "git_commit": s.get("GIT") if s.get("GIT") != "none" else None,
        "git_dirty": int(s.get("DIRTY", "0").strip() or "0"),
    }


def _full_inspect() -> dict:
    """Inspeksi lengkap: base + tipe + stack + app + mode."""
    log.info("Memulai inspeksi server...")
    base = _inspect_base()
    stype, _found = _detect_type()
    fold = _mem_fold()
    to = fold.get("server:type-override")
    if to and to.get("text", "").strip() in ("web-app", "database", "container", "general"):
        stype = to["text"].strip()
        log.info("Type override: %s", stype)
    if stype == "web-app":
        stacks = _inspect_stacks_web()
    elif stype == "database":
        stacks = _inspect_stacks_database()
    elif stype == "container":
        stacks = _inspect_stacks_container()
    else:
        stacks = {}
    app = _inspect_app(PROJECT_ROOT) if PROJECT_ROOT else None
    profile = {"type": stype, "base": base, "stacks": stacks, "app": app,
               "inspected_at": _now_iso()}
    profile["mode"] = _derive_mode(profile)
    _save_profile_summary(profile)
    # Trend detection: simpan snapshot metrik dan bandingkan dengan histori.
    try:
        fold = _mem_fold()
        hist_rec = fold.get("server:metrics-history")
        history: list[dict] = []
        if hist_rec:
            try:
                history = json.loads(hist_rec.get("text", "[]"))
            except (json.JSONDecodeError, TypeError):
                pass
        trend = _compute_trend(base, history)
        if trend:
            profile["_trend"] = trend
        _save_metrics_snapshot(base)
    except Exception:
        log.debug("trend detection gagal", exc_info=True)
    log.info("Inspeksi selesai: type=%s mode=%s", stype, profile["mode"])
    return profile


def _derive_mode(profile: dict) -> str:
    """Tentukan mode operasi dari profile server."""
    fold = _mem_fold()
    mo = fold.get("server:mode-override")
    if mo and mo.get("text", "").strip() in _MODE_LEVELS:
        return mo["text"].strip()
    stype = profile.get("type", "general")
    stacks = profile.get("stacks", {})
    app = profile.get("app")
    base = profile.get("base", {})
    up = base.get("uptime_days", 0)
    disk = base.get("disk_pct", 100)
    if stype == "web-app":
        if not stacks.get("web") or not stacks.get("runtime"):
            return "setup"
        if app and not app.get("exists"):
            return "setup"
        if app and app.get("exists") and (not app.get("env_exists") or not app.get("vendor_exists")):
            return "setup"
        web_ok = stacks.get("web", {}).get("status") == "active"
        fpm = stacks.get("runtime", {}).get("fpm", "")
        fpm_ok = fpm not in ("not-running", "none", "")
        if web_ok and fpm_ok and up > 7 and disk < 80:
            return "production"
        return "deploy"
    if stype == "database":
        db = stacks.get("database", {})
        if not db or db.get("status") not in ("active", "running"):
            return "setup"
        if up > 7:
            return "production"
        return "deploy"
    if stype == "container":
        docker = stacks.get("docker", {})
        if not docker or docker.get("status") not in ("active", "running"):
            return "setup"
        if docker.get("running_count", 0) > 0 and up > 7:
            return "production"
        return "deploy"
    return "deploy"


def _save_profile_summary(profile: dict) -> None:
    """Simpan ringkasan padat profile ke memory persisten."""
    tp, md = profile["type"], profile["mode"]
    b = profile.get("base", {})
    st = profile.get("stacks", {})
    app = profile.get("app")
    lines = [f"type={tp} | mode={md} | inspected={profile.get('inspected_at', '?')}"]
    lines.append(f"os: {b.get('os','?')} | kernel: {b.get('kernel','?')} | uptime: {b.get('uptime_days','?')}d")
    lines.append(f"disk: {b.get('disk_pct','?')}% | mem: {b.get('memory_pct','?')}% ({b.get('memory','?')})")
    lines.append(f"firewall: {b.get('firewall','?')} | fail2ban: {b.get('fail2ban','?')} | "
                 f"ssh: port {b.get('ssh_port','?')} ({b.get('ssh_auth','?')})")
    for k, v in st.items():
        if isinstance(v, dict):
            nm = v.get("name", k)
            ver = v.get("version", "")
            status = v.get("status", "")
            extra = ""
            if k == "runtime" and "modules" in v:
                extra = f" [{','.join(v['modules'][:6])}]"
            if k == "ssl":
                extra = f" ({v.get('domain','?')} exp {v.get('expires','?')})"
            if k == "docker":
                extra = f" ({v.get('running_count',0)} containers)"
            lines.append(f"{k}: {nm}/{ver} ({status}){extra}")
        elif isinstance(v, str):
            lines.append(f"{k}: {v}")
    if app and app.get("exists"):
        lines.append(f"app: {app['path']} ({app.get('framework','?')}) "
                     f"env={'Y' if app.get('env_exists') else 'N'} "
                     f"vendor={'Y' if app.get('vendor_exists') else 'N'} "
                     f"git={app.get('git_branch') or '?'}@{(app.get('git_commit') or '?')[:8]}")
    elif app:
        lines.append(f"app: {app['path']} (NOT EXISTS)")
    text = "\n".join(lines)
    try:
        _mem_append({"id": "server:stack-profile", "ns": "server", "key": "stack-profile",
                     "text": text, "tags": ["profile", "auto-inspect"], "source": "inspect",
                     "created_at": _now_iso(), "pinned": True, "deleted": False})
    except Exception as e:
        log.warning("gagal simpan profile ke memory: %s", e)


def _save_metrics_snapshot(base: dict) -> None:
    """Simpan snapshot metrik ke ring-buffer (maks 7 entry) di memory."""
    fold = _mem_fold()
    rec = fold.get("server:metrics-history")
    history: list[dict] = []
    if rec:
        try:
            history = json.loads(rec.get("text", "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
    snapshot = {
        "ts": _now_iso(),
        "disk_pct": base.get("disk_pct", 0),
        "memory_pct": base.get("memory_pct", 0),
        "uptime_days": base.get("uptime_days", 0),
    }
    history.append(snapshot)
    history = history[-7:]
    try:
        _mem_append({"id": "server:metrics-history", "ns": "server",
                     "key": "metrics-history", "text": json.dumps(history),
                     "tags": ["metrics", "auto"], "created_at": _now_iso(),
                     "pinned": False, "deleted": False})
    except Exception:
        log.debug("gagal simpan metrics snapshot", exc_info=True)


def _compute_trend(current: dict, history: list[dict]) -> dict:
    """Bandingkan metrik saat ini dengan snapshot tertua untuk deteksi tren."""
    if not history:
        return {}
    oldest = history[0]
    trend: dict = {}
    for key in ("disk_pct", "memory_pct"):
        cur_val = current.get(key, 0)
        old_val = oldest.get(key, 0)
        diff = cur_val - old_val
        span = len(history)
        if abs(diff) >= 3:
            trend[key] = {"delta": diff, "direction": "naik" if diff > 0 else "turun",
                          "summary": f"{'+' if diff > 0 else ''}{diff}% vs {span} snapshot lalu"}
        else:
            trend[key] = {"delta": 0, "direction": "stabil", "summary": "stabil"}
    return trend


# ---------------------------------------------------------------------------
# MODE ENFORCEMENT: batasi operasi berdasarkan mode
# ---------------------------------------------------------------------------
_PRODUCTION_BLOCKED_TOOLS = {"laravel_deploy"}
_PRODUCTION_BLOCKED_CMDS = [
    r"\bapt(?:-get)?\s+(install|remove|purge|upgrade|dist-upgrade)\b",
    r"\bdpkg\s+(-i|--install|-r|--remove)\b",
    r"\bpip3?\s+install\b",
    r"\bnpm\s+(install|ci)\b",
]


def _mode_gate(tool_name: str, command: str = "") -> dict | None:
    """None jika diizinkan; dict error jika diblokir oleh mode saat ini."""
    if _CURRENT_MODE != "production":
        return None
    if tool_name in _PRODUCTION_BLOCKED_TOOLS:
        return {"success": False, "blocked_by_mode": True, "mode": _CURRENT_MODE,
                "error": f"Tool '{tool_name}' diblokir di mode PRODUCTION. "
                         "Override: memory_write(ns='server', key='mode-override', text='deploy')"}
    if command:
        for pat in _PRODUCTION_BLOCKED_CMDS:
            if re.search(pat, command, re.I):
                return {"success": False, "blocked_by_mode": True, "mode": _CURRENT_MODE,
                        "error": f"Command diblokir di mode PRODUCTION (pola: {pat}). "
                                 "Override: memory_write(ns='server', key='mode-override', text='deploy')"}
    return None


def _build_instructions() -> str:
    """Gabungkan memory digest + info profile/mode untuk FastMCP instructions."""
    digest = _build_memory_digest()
    mode_info = (f"\n\n## MODE OPERASI: {_CURRENT_MODE.upper()}\n"
                 f"Tipe server: {_PROFILE.get('type', 'unknown')}\n")
    if _CURRENT_MODE == "production":
        mode_info += ("PERHATIAN: Mode PRODUCTION aktif — operasi tulis diperketat.\n"
                      "Tool laravel_deploy DIBLOKIR. apt install/remove DIBLOKIR.\n"
                      "Override: memory_write(ns='server', key='mode-override', text='deploy')\n")
    elif _CURRENT_MODE == "setup":
        mode_info += "Mode SETUP — toleransi tinggi untuk konfigurasi infrastruktur.\n"
    else:
        mode_info += "Mode DEPLOY — operasi standar, WRITE perlu konfirmasi user.\n"
    return digest + mode_info


_STARTUP_CACHE_MAX_AGE = 3600  # detik — pakai cache profile jika inspeksi < 1 jam lalu


def _try_cached_startup() -> bool:
    """Coba muat profile dari memory jika inspeksi terakhir masih segar.
    Return True jika berhasil pakai cache (skip full inspect)."""
    global _PROFILE, _CURRENT_MODE
    try:
        fold = _mem_fold()
        rec = fold.get("server:stack-profile")
        if not rec:
            return False
        text = rec.get("text", "")
        first_line = text.split("\n", 1)[0]
        parts = {p.split("=", 1)[0].strip(): p.split("=", 1)[1].strip()
                 for p in first_line.split("|") if "=" in p}
        inspected = parts.get("inspected")
        if not inspected:
            return False
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(inspected)).total_seconds()
        if age > _STARTUP_CACHE_MAX_AGE:
            return False
        stype = parts.get("type", "general")
        mode = parts.get("mode", "deploy")
        _PROFILE = {"type": stype, "mode": mode, "inspected_at": inspected,
                     "_cached": True}
        _CURRENT_MODE = mode
        log.info("Pakai cache profile (usia %ds): type=%s mode=%s", int(age), stype, mode)
        return True
    except Exception as e:
        log.debug("cache startup gagal: %s — fallback ke full inspect", e)
        return False


# Inspeksi saat startup (lewati jika ODIN_SKIP_INSPECT=1, mis. saat testing)
if os.environ.get("ODIN_SKIP_INSPECT", "").strip() in ("1", "true", "yes"):
    log.info("ODIN_SKIP_INSPECT=1 — inspeksi dilewati")
elif not _try_cached_startup():
    try:
        _PROFILE = _full_inspect()
        _CURRENT_MODE = _PROFILE.get("mode", "deploy")
    except Exception as e:
        log.warning("inspeksi startup gagal: %s — fallback mode=deploy", e)

mcp = FastMCP("odin", instructions=_build_instructions())


# ---------------------------------------------------------------------------
# TOOLS yang dilihat & dipanggil Claude Code
# (Docstring = deskripsi tool yang dibaca model. Tulis jelas!)
# ---------------------------------------------------------------------------
@mcp.tool()
def run_command(command: str, cwd: str = "", timeout: int = DEFAULT_TIMEOUT,
                allow_dangerous: bool = False) -> dict:
    """Jalankan SATU perintah shell di server target; kembalikan stdout, stderr, dan exit_code
    sebagai umpan balik. Ini primitif serbaguna untuk deploy/testing: git, composer, npm,
    php artisan, ls, cat, grep, systemctl status, dll.

    Args:
        command: perintah shell, mis. "php artisan migrate:status".
        cwd: working directory absolut, mis. "/var/www/simuru". Kosong = home default.
        timeout: batas detik (auto-clamp ke MAX_TIMEOUT).
        allow_dangerous: True hanya untuk perintah destruktif yang disengaja (default diblokir).
    """
    block = _mode_gate("run_command", command)
    if block:
        return block

    # ── Inovasi 1: cache check untuk READ-only command ───────────────────
    _cwd_key = cwd or ""
    cached = _cache_get(command, _cwd_key)
    if cached:
        cached["_summary"] = f"✓ CACHE ({cached['_cache_age_sec']}s) — {command}"
        _session_log("run_command", command, cached)
        return _slim(cached, command)   # slim juga berlaku untuk cache hit

    pre = _capture_pre_state(command, cwd or None)
    result = _run(command, cwd or None, timeout, allow_dangerous)
    analysis = _analyze_output(result)
    if analysis:
        result["_analysis"] = analysis
    rb = _suggest_rollback(command, pre) if pre else []
    if rb:
        result["_rollback_hint"] = rb
    result["_summary"] = _build_summary(result)
    result = _smart_output(result)

    # ── Inovasi 1: simpan ke cache (full, sebelum slim) ──────────────────
    _cache_set(command, _cwd_key, result)

    _audit("run_command", command, result)
    _session_log("run_command", command, result, pre_state=pre or None, rollback=rb or None)
    _orchestrate("run_command", {"command": command, "cwd": cwd}, result)

    # ── Inovasi 2: slim envelope sebelum return ───────────────────────────
    return _slim(result, command)


@mcp.tool()
def tail_log(path: str, lines: int = 100, grep: str = "") -> dict:
    """Baca N baris TERAKHIR sebuah file log sebagai umpan balik (mis. storage/logs/laravel.log,
    /var/log/nginx/error.log). Path harus berada di dalam ALLOWED_LOG_DIRS.

    Args:
        path: path absolut ke file log.
        lines: jumlah baris terakhir (maks 2000).
        grep: filter baris (case-insensitive), mis. "ERROR" atau "production.ERROR".
    """
    if not _path_inside(path, ALLOWED_LOG_DIRS):
        return {"success": False, "stdout": "",
                "stderr": f"Path '{path}' di luar ALLOWED_LOG_DIRS: {ALLOWED_LOG_DIRS}"}
    n = max(1, min(int(lines), 2000))
    norm = os.path.normpath(path)
    cmd = f"tail -n {n} {shlex.quote(norm)}"
    if grep:
        cmd += f" | grep -i -- {shlex.quote(grep)} || true"
    result = _run(cmd, None, 60)
    result = _smart_output(result)
    _audit("tail_log", f"{path} lines={n} grep={grep!r}", result)
    _session_log("tail_log", f"{path} grep={grep!r}", result)
    _orchestrate("tail_log", {"path": path, "grep": grep}, result)
    return result


@mcp.tool()
def service_action(service: str, action: str = "status") -> dict:
    """Cek/kelola service systemd (mis. nginx, php8.3-fpm, mysql, redis).

    Args:
        service: nama unit systemd.
        action: status | is-active | is-enabled | reload | restart | start | stop.
    Catatan: reload/restart/start/stop memakai `sudo -n` (non-interaktif). Pastikan user
    punya entri sudoers untuk `systemctl <action> <service>`.
    """
    actions = {"status", "is-active", "is-enabled", "reload", "restart", "start", "stop"}
    if action not in actions:
        return {"success": False, "stderr": f"action tidak dikenal. Pilih: {sorted(actions)}"}
    if not re.fullmatch(r"[A-Za-z0-9._@-]+", service):
        return {"success": False, "stderr": "Nama service tidak valid."}
    sudo = "" if action in {"status", "is-active", "is-enabled"} else "sudo -n "
    pager = " --no-pager" if action == "status" else ""
    cmd = f"{sudo}systemctl {action} {shlex.quote(service)}{pager}"
    pre = _capture_pre_state(cmd, None)
    result = _run(cmd, None, 60)
    analysis = _analyze_output(result)
    if analysis:
        result["_analysis"] = analysis
    rb = _suggest_rollback(cmd, pre) if pre else []
    if rb:
        result["_rollback_hint"] = rb
    _audit("service_action", f"{action} {service}", result)
    _session_log("service_action", f"{action} {service}", result,
                 pre_state=pre or None, rollback=rb or None)
    if action in ("restart", "stop", "start", "reload") and result.get("success"):
        _event_append(PROJECT_NAME, f"service_{action}", service)
    _orchestrate("service_action", {"service": service, "action": action}, result)
    return result


def _load_deploy_defaults() -> dict:
    """Muat konfigurasi deploy terakhir dari memory (jika ada)."""
    fold = _mem_fold()
    rec = fold.get("server:deploy-config")
    if rec:
        try:
            return json.loads(rec.get("text", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _save_deploy_config(app_path: str, branch: str, fpm_service: str,
                        npm_build: bool) -> None:
    """Simpan konfigurasi deploy yang berhasil ke memory untuk sesi berikutnya."""
    config = json.dumps({"app_path": app_path, "branch": branch,
                         "fpm_service": fpm_service, "npm_build": npm_build})
    _mem_append({"id": "server:deploy-config", "ns": "server", "key": "deploy-config",
                 "text": config, "tags": ["deploy", "auto"],
                 "created_at": _now_iso(), "pinned": True, "deleted": False})


def _capture_deploy_fingerprint(app_path: str) -> dict:
    """Ambil fingerprint deploy: git hash, composer.lock md5, migration count, .env line count."""
    fp: dict = {}
    r = _run("git rev-parse HEAD 2>/dev/null", app_path, 10)
    if r.get("success"):
        fp["git_hash"] = r["stdout"].strip()
    r = _run("md5sum composer.lock 2>/dev/null | awk '{print $1}'", app_path, 10)
    if r.get("success") and r["stdout"].strip():
        fp["composer_lock_md5"] = r["stdout"].strip()
    r = _run("php artisan migrate:status 2>/dev/null | grep -c '| Ran'", app_path, 15)
    if r.get("success"):
        n = r["stdout"].strip()
        fp["migration_count"] = int(n) if n.isdigit() else 0
    r = _run("wc -l < .env 2>/dev/null", app_path, 10)
    if r.get("success"):
        n = r["stdout"].strip()
        fp["env_lines"] = int(n) if n.isdigit() else 0
    fp["captured_at"] = _now_iso()
    return fp


def _save_deploy_fingerprint(fp: dict) -> None:
    """Simpan fingerprint deploy ke memory."""
    _mem_append({"id": "server:deploy-fingerprint", "ns": "server",
                 "key": "deploy-fingerprint", "text": json.dumps(fp),
                 "tags": ["deploy", "fingerprint", "auto"],
                 "created_at": _now_iso(), "pinned": True, "deleted": False})


def _load_deploy_fingerprint() -> dict:
    """Muat fingerprint deploy terakhir dari memory."""
    fold = _mem_fold()
    rec = fold.get("server:deploy-fingerprint")
    if rec:
        try:
            return json.loads(rec.get("text", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _detect_drift(app_path: str) -> dict:
    """Bandingkan state server saat ini vs fingerprint deploy terakhir."""
    prev = _load_deploy_fingerprint()
    if not prev:
        return {}
    current = _capture_deploy_fingerprint(app_path)
    drift: dict = {"previous": prev, "current": current, "changes": []}
    if prev.get("git_hash") and current.get("git_hash"):
        if prev["git_hash"] != current["git_hash"]:
            drift["changes"].append(
                f"Git HEAD berubah: {prev['git_hash'][:8]} → {current['git_hash'][:8]}")
    if prev.get("composer_lock_md5") and current.get("composer_lock_md5"):
        if prev["composer_lock_md5"] != current["composer_lock_md5"]:
            drift["changes"].append("composer.lock berubah (dependency di-update di luar deploy)")
    if prev.get("migration_count") is not None and current.get("migration_count") is not None:
        diff = current["migration_count"] - prev["migration_count"]
        if diff > 0:
            drift["changes"].append(f"+{diff} migrasi baru sejak deploy terakhir")
        elif diff < 0:
            drift["changes"].append(f"Migrasi berkurang {abs(diff)} (rollback?)")
    if prev.get("env_lines") is not None and current.get("env_lines") is not None:
        diff = current["env_lines"] - prev["env_lines"]
        if diff != 0:
            drift["changes"].append(
                f".env berubah ({prev['env_lines']} → {current['env_lines']} baris)")
    drift["has_drift"] = len(drift["changes"]) > 0
    return drift


def _preflight_deploy(app_path: str) -> tuple[dict, list[str]]:
    """Cek prasyarat sebelum deploy. Return (checks, blockers).
    Jika blockers tidak kosong, deploy dibatalkan dengan laporan."""
    checks: dict = {}
    blockers: list[str] = []
    r = _run("df --output=pcent / 2>/dev/null | tail -1 || df -h / | awk 'NR==2{print $5}'",
             None, 10)
    if r.get("success"):
        pct_str = r["stdout"].strip().rstrip("%").strip()
        if pct_str.isdigit():
            pct = int(pct_str)
            checks["disk_use_pct"] = pct
            if pct >= 95:
                blockers.append(f"Disk {pct}% penuh — deploy bisa gagal di tengah jalan")
            elif pct >= 85:
                checks["disk_warning"] = f"Disk {pct}% — ruang terbatas, pantau setelah deploy"
    r = _run("git status --porcelain 2>/dev/null | wc -l", app_path, 10)
    if r.get("success"):
        n = r["stdout"].strip()
        dirty = int(n) if n.isdigit() else 0
        checks["git_dirty_files"] = dirty
        if dirty:
            checks["git_note"] = f"{dirty} file berubah lokal — akan hilang saat git reset --hard"
    r = _run("git log --oneline -1 2>/dev/null", app_path, 10)
    if r.get("success"):
        checks["current_commit"] = r["stdout"].strip()
    r = _run("php -r 'echo phpversion();' 2>/dev/null", None, 10)
    if r.get("success"):
        checks["php_version"] = r["stdout"].strip()
    try:
        drift = _detect_drift(app_path)
        if drift.get("has_drift"):
            checks["drift"] = drift
    except Exception:
        log.debug("drift detection gagal", exc_info=True)
    return checks, blockers


@mcp.tool()
def laravel_deploy(app_path: str, branch: str = "main", composer: bool = True,
                   migrate: bool = True, npm_build: bool = False,
                   fpm_service: str = "", maintenance: bool = True) -> dict:
    """Deploy aplikasi Laravel dengan langkah standar; kembalikan log setiap langkah dan
    berhenti melapor pada langkah pertama yang gagal.

    Urutan: (artisan down) -> git fetch + reset --hard origin/<branch> ->
    composer install --no-dev -> migrate --force -> optimize:clear + config/route/view cache ->
    (npm ci && npm run build) -> (artisan up) -> reload php-fpm.

    PERHATIAN: `git reset --hard origin/<branch>` MEMBUANG perubahan lokal di server (sengaja,
    agar deploy idempoten). Jangan dipakai di folder yang menyimpan edit manual.

    Args:
        app_path: root project Laravel, mis. "/var/www/simuru".
        branch: branch git yang dideploy.
        fpm_service: nama service php-fpm utk di-reload, mis. "php8.3-fpm". Kosong = lewati.
    """
    block = _mode_gate("laravel_deploy")
    if block:
        return block
    # Isi parameter kosong dari config deploy terakhir (jika ada di memory).
    defaults = _load_deploy_defaults()
    if defaults:
        if not fpm_service and defaults.get("fpm_service"):
            fpm_service = defaults["fpm_service"]
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        return {"success": False, "failed_steps": ["validation"],
                "app_path": app_path, "branch": branch, "steps": [],
                "error": "Nama branch tidak valid (hanya alfanumerik, titik, garis miring, strip)."}
    preflight, blockers = _preflight_deploy(app_path)
    if blockers:
        return {"success": False, "failed_steps": ["preflight"],
                "app_path": app_path, "branch": branch, "steps": [],
                "preflight": preflight, "blockers": blockers}
    steps: list[dict] = []

    def do(label: str, command: str, t: int = DEFAULT_TIMEOUT) -> bool:
        pre = _capture_pre_state(command, app_path)
        r = _run(command, app_path, t)
        analysis = _analyze_output(r)
        if analysis:
            r["_analysis"] = analysis
        rb = _suggest_rollback(command, pre) if pre else []
        if rb:
            r["_rollback_hint"] = rb
        steps.append({"step": label, **r})
        return bool(r.get("success"))

    if maintenance:
        do("maintenance:down", "php artisan down || true", 60)

    ok = do("git", f"git fetch --all --prune && git reset --hard origin/{shlex.quote(branch)}", 240)
    if ok and composer:
        ok = do("composer", "composer install --no-dev --optimize-autoloader --no-interaction --prefer-dist", 600)
    if ok and migrate:
        ok = do("migrate", "php artisan migrate --force", 300)
    if ok:
        ok = do("optimize",
                "php artisan optimize:clear && php artisan config:cache "
                "&& php artisan route:cache && php artisan view:cache", 120)
    if ok and npm_build:
        ok = do("npm", "npm ci && npm run build", 900)

    if maintenance:
        do("maintenance:up", "php artisan up", 60)
    if fpm_service:
        do("php-fpm", f"sudo -n systemctl reload {shlex.quote(fpm_service)}", 60)

    failed = [s["step"] for s in steps
              if not s.get("success") and s["step"] != "maintenance:down"]
    result = {"success": len(failed) == 0, "failed_steps": failed,
              "app_path": app_path, "branch": branch,
              "preflight": preflight, "steps": steps}
    if not failed:
        try:
            _save_deploy_config(app_path, branch, fpm_service, npm_build)
            fp = _capture_deploy_fingerprint(app_path)
            _save_deploy_fingerprint(fp)
            result["_fingerprint"] = fp
        except Exception:
            log.debug("gagal simpan deploy config/fingerprint", exc_info=True)
    if defaults:
        result["_deploy_defaults_used"] = defaults
    _audit("laravel_deploy", f"{branch} -> {app_path} failed={failed}", result)
    _session_log("laravel_deploy", f"deploy {branch} -> {app_path}", result)
    sev = "error" if failed else "info"
    _event_append(PROJECT_NAME, "deploy", f"branch={branch} path={app_path} failed={failed}", sev)
    _orchestrate("laravel_deploy", {"branch": branch, "app_path": app_path}, result)
    return result


@mcp.tool()
def run_tests(app_path: str, filter: str = "", testsuite: str = "",
              runner: str = "auto") -> dict:
    """Jalankan test suite (PHPUnit/Pest) dan kembalikan hasilnya sebagai umpan balik.

    Args:
        app_path: root project.
        filter: nama test/method untuk difilter (--filter).
        testsuite: nama suite (--testsuite), mis. "Feature".
        runner: auto | artisan | pest | phpunit. (auto -> "php artisan test").
    """
    cmd = {"pest": "./vendor/bin/pest",
           "phpunit": "./vendor/bin/phpunit"}.get(runner, "php artisan test")
    if testsuite:
        cmd += f" --testsuite={shlex.quote(testsuite)}"
    if filter:
        cmd += f" --filter={shlex.quote(filter)}"
    result = _run(cmd, app_path, 900)
    analysis = _analyze_output(result)
    if analysis:
        result["_analysis"] = analysis
    _audit("run_tests", f"{app_path} filter={filter!r}", result)
    _session_log("run_tests", f"{app_path} filter={filter!r}", result)
    _orchestrate("run_tests", {"app_path": app_path}, result)
    return result


@mcp.tool()
def http_health_check(url: str, expect_status: int = 200, timeout: int = 30) -> dict:
    """Cek kesehatan aplikasi web via satu request HTTP (curl). Kembalikan http_status,
    waktu respons, dan apakah cocok dengan expect_status. Bagus sebagai verifikasi pasca-deploy.

    Args:
        url: URL lengkap, harus diawali http:// atau https://.
        expect_status: status HTTP yang diharapkan (default 200).
    """
    if not re.match(r"^https?://", url):
        return {"success": False, "stderr": "URL harus diawali http:// atau https://"}
    fmt = "HTTPSTATUS:%{http_code} TIME:%{time_total}s SIZE:%{size_download}"
    r = _run(f"curl -sS -o /dev/null -m {int(timeout)} -w {shlex.quote(fmt)} {shlex.quote(url)}",
             None, timeout + 10)
    m = re.search(r"HTTPSTATUS:(\d+)", r.get("stdout", "") or "")
    status = int(m.group(1)) if m else None
    r["http_status"] = status
    r["matches_expected"] = status == expect_status
    r["success"] = status == expect_status
    _audit("http_health_check", f"{url} expect={expect_status}", r)
    _session_log("http_health_check", f"{url} -> {r.get('http_status')}", r)
    _orchestrate("http_health_check", {"url": url, "expect_status": expect_status}, r)
    return r


@mcp.tool()
def server_info() -> dict:
    """Ringkasan server target: kernel, uptime, disk, memori, versi PHP & Composer, serta mode
    agent. Berguna sebagai cek awal sebelum deploy."""
    cmd = ("echo '## uname'; uname -a; "
           "echo '## uptime'; uptime; "
           "echo '## disk'; df -h / 2>/dev/null; "
           "echo '## mem'; free -h 2>/dev/null; "
           "echo '## php'; php -v 2>/dev/null | head -1; "
           "echo '## composer'; composer --version 2>/dev/null")
    r = _run(cmd, None, 60)
    r["agent_mode"] = MODE
    r["ssh_target"] = SSH_TARGET if MODE == "ssh" else None
    r["project_name"] = PROJECT_NAME
    _session_log("server_info", "ringkasan server", r)
    return r


@mcp.tool()
def session_history(last: int = 0) -> dict:
    """Riwayat perintah yang telah dijalankan di sesi ini (sejak server di-spawn).
    Berguna untuk meninjau apa yang sudah dikerjakan tanpa membaca ulang semua output.

    Args:
        last: jumlah entry terakhir yang dikembalikan (0 = semua).
    """
    items = _SESSION_LOG if not last else _SESSION_LOG[-max(1, last):]
    return {"success": True, "count": len(items), "total": len(_SESSION_LOG),
            "entries": items}


@mcp.tool()
def audit_tail(last: int = 20, tool_filter: str = "", success_only: bool = False,
               since: str = "") -> dict:
    """Baca N entry terakhir dari audit log (forensik lintas-sesi). Read-only.

    Args:
        last: jumlah entry terakhir (maks 100).
        tool_filter: filter nama tool, mis. "run_command" atau "laravel_deploy".
        success_only: True = hanya tampilkan yang berhasil.
        since: filter temporal ISO datetime, mis. "2026-06-07T00:00:00". Hanya entry >= since.
    """
    if not os.path.exists(AUDIT_FILE):
        return {"success": True, "count": 0, "entries": []}
    entries: list[dict] = []
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if tool_filter and rec.get("tool") != tool_filter:
                continue
            if success_only and not rec.get("success"):
                continue
            if since and rec.get("ts", "") < since:
                continue
            entries.append(rec)
    entries = entries[-max(1, min(int(last), 100)):]
    return {"success": True, "count": len(entries), "entries": entries}


@mcp.tool()
def inspect_server() -> dict:
    """Inspeksi ulang server: deteksi tipe, stack, app, dan derive mode operasi.
    Otomatis berjalan saat startup; panggil ulang setelah install/uninstall package,
    setup service baru, atau perubahan infrastruktur signifikan.

    Hasil disimpan ke memory persisten (namespace server, key stack-profile).
    Mode operasi (setup/deploy/production) diturunkan otomatis dari hasil inspeksi.
    """
    global _PROFILE, _CURRENT_MODE
    try:
        _PROFILE = _full_inspect()
        _CURRENT_MODE = _PROFILE.get("mode", "deploy")
    except Exception as e:
        return {"success": False, "error": f"Inspeksi gagal: {e}"}
    _session_log("inspect_server", f"type={_PROFILE['type']} mode={_CURRENT_MODE}",
                 {"success": True})
    return {"success": True, "profile": _PROFILE, "mode": _CURRENT_MODE,
            "hint": f"Simpan mode ke laptop agar guard aware: echo '{_CURRENT_MODE}' > ~/.odin_mode"}


@mcp.tool()
def runbook(name: str, steps: list[dict], app_path: str = "") -> dict:
    """Jalankan serangkaian langkah berurutan (runbook workflow). Berhenti pada langkah pertama
    yang gagal kecuali langkah tersebut punya continue_on_fail=True. Setiap langkah dicatat
    lengkap dengan analisis error dan saran rollback.

    Claude membangun daftar langkah berdasarkan konteks situasi — tool ini mengeksekusinya
    secara berurutan dengan tracking lengkap. Cocok untuk operasi multi-step: backup-restore DB,
    SSL renewal, nginx config update, log cleanup, dsb.

    Args:
        name: nama identifikasi runbook, mis. "backup-db", "ssl-renew", "nginx-update".
        steps: daftar langkah berurutan. Setiap langkah adalah dict:
            - label (str, wajib): nama langkah, mis. "backup-dump".
            - command (str, wajib): perintah shell yang akan dijalankan.
            - timeout (int, opsional): batas detik, default 180.
            - continue_on_fail (bool, opsional): True = lanjut meskipun langkah ini gagal.
        app_path: working directory untuk semua langkah. Kosong = home default.
    """
    block = _mode_gate("runbook")
    if block:
        return block
    if _CURRENT_MODE == "production":
        for i, s in enumerate(steps):
            sb = _mode_gate("run_command", s.get("command", ""))
            if sb:
                sb["error"] = f"Langkah '{s.get('label', i+1)}': {sb['error']}"
                return sb
    if not steps:
        return {"success": False, "error": "steps kosong."}
    if len(steps) > 20:
        return {"success": False, "error": "Maks 20 langkah per runbook."}

    cwd = app_path or None
    results: list[dict] = []

    for i, step in enumerate(steps):
        label = step.get("label", f"step-{i+1}")
        command = step.get("command", "")
        t = int(step.get("timeout", DEFAULT_TIMEOUT))
        cont = step.get("continue_on_fail", False)

        if not command:
            results.append({"step": label, "success": False,
                            "stderr": "command kosong", "skipped": False})
            if not cont:
                break
            continue

        pre = _capture_pre_state(command, cwd)
        r = _run(command, cwd, t)
        analysis = _analyze_output(r)
        if analysis:
            r["_analysis"] = analysis
        rb = _suggest_rollback(command, pre) if pre else []
        if rb:
            r["_rollback_hint"] = rb
        r["step"] = label
        if pre:
            r["_pre_state"] = pre
        results.append(r)

        if not r.get("success") and not cont:
            break

    executed = len(results)
    total = len(steps)
    skipped = total - executed
    failed = [r["step"] for r in results if not r.get("success")]

    out = {
        "success": len(failed) == 0,
        "name": name,
        "executed": executed, "total": total, "skipped": skipped,
        "failed_steps": failed,
        "steps": results,
    }
    _audit("runbook", f"{name} ({executed}/{total}) failed={failed}", out)
    _session_log("runbook", f"{name} ({executed}/{total})", out)
    return out


@mcp.tool()
def rollback_plan(last: int = 5) -> dict:
    """Tampilkan saran rollback untuk operasi WRITE terakhir di sesi ini.
    Berdasarkan state yang ditangkap sebelum setiap perintah dijalankan. Berguna saat
    operasi menghasilkan error tak terduga dan perlu dikembalikan ke state sebelumnya.

    Args:
        last: jumlah operasi terakhir yang diperiksa (default 5).
    """
    relevant: list[dict] = []
    for entry in reversed(_SESSION_LOG):
        if entry.get("_pre_state") or entry.get("_rollback"):
            relevant.append({
                "seq": entry["seq"],
                "tool": entry["tool"],
                "summary": entry["summary"],
                "success": entry.get("success"),
                "pre_state": entry.get("_pre_state"),
                "rollback_commands": entry.get("_rollback", []),
            })
        if len(relevant) >= max(1, last):
            break
    return {
        "success": True,
        "count": len(relevant),
        "operations": relevant,
        "note": "Perintah rollback di atas adalah SARAN. Tinjau dan sesuaikan sebelum menjalankan."
    }


# ---------------------------------------------------------------------------
# RUNBOOK TEMPLATES: workflow siap pakai + custom dari memory
# ---------------------------------------------------------------------------
_BUILTIN_TEMPLATES = {
    "ssl-renew": {
        "description": "Perpanjang sertifikat SSL Let's Encrypt",
        "steps": [
            {"label": "renew", "command": "sudo -n certbot renew", "timeout": 300},
            {"label": "test-nginx", "command": "sudo -n nginx -t", "timeout": 30},
            {"label": "reload-nginx", "command": "sudo -n systemctl reload nginx", "timeout": 60},
        ],
    },
    "db-backup": {
        "description": "Backup database MySQL/MariaDB",
        "steps": [
            {"label": "dump", "command": "mysqldump --single-transaction --no-tablespaces {db} > /var/backups/{app}/$(date +%Y%m%d_%H%M%S).sql", "timeout": 600},
            {"label": "verify", "command": "ls -lh /var/backups/{app}/*.sql | tail -1", "timeout": 10},
        ],
    },
    "log-cleanup": {
        "description": "Bersihkan log lama untuk bebaskan disk",
        "steps": [
            {"label": "check-disk", "command": "df -h /", "timeout": 10},
            {"label": "old-logs", "command": "find /var/log -name '*.gz' -mtime +30 -delete", "timeout": 60},
            {"label": "laravel-log", "command": "truncate -s 0 {app_path}/storage/logs/laravel.log", "timeout": 10},
            {"label": "verify-disk", "command": "df -h /", "timeout": 10},
        ],
    },
    "health-check": {
        "description": "Cek kesehatan dasar server (disk, memory, service)",
        "steps": [
            {"label": "disk", "command": "df -h /", "timeout": 10},
            {"label": "memory", "command": "free -h", "timeout": 10},
            {"label": "nginx", "command": "systemctl is-active nginx", "timeout": 10, "continue_on_fail": True},
            {"label": "mysql", "command": "systemctl is-active mysql", "timeout": 10, "continue_on_fail": True},
            {"label": "php-fpm", "command": "systemctl list-units --type=service --state=running | grep php.*fpm || echo 'no fpm'", "timeout": 10, "continue_on_fail": True},
        ],
    },
}


@mcp.tool()
def runbook_templates(name: str = "") -> dict:
    """Lihat template runbook bawaan + custom dari memory. Tanpa argumen = daftar semua.
    Dengan nama = detail template (langsung bisa dipakai di tool runbook).

    Template custom disimpan di memory (ns=server, key=runbook-<nama>). Claude bisa
    membuat runbook lalu menyimpannya sebagai template via memory_write.

    Args:
        name: nama template, mis. "ssl-renew". Kosong = daftar semua.
    """
    custom: dict = {}
    try:
        fold = _mem_fold()
        for rid, rec in fold.items():
            if rid.startswith("server:runbook-") and rec.get("text"):
                tname = rid.replace("server:runbook-", "", 1)
                try:
                    custom[tname] = json.loads(rec["text"])
                except (json.JSONDecodeError, TypeError):
                    pass
    except Exception:
        pass
    all_templates = {**_BUILTIN_TEMPLATES, **custom}
    if not name:
        listing = {k: v.get("description", "") for k, v in all_templates.items()}
        return {"success": True, "templates": listing,
                "builtin": list(_BUILTIN_TEMPLATES.keys()),
                "custom": list(custom.keys())}
    tpl = all_templates.get(name)
    if not tpl:
        return {"success": False, "error": f"Template '{name}' tidak ada. "
                f"Pilihan: {sorted(all_templates.keys())}"}
    return {"success": True, "name": name, **tpl}


# ---------------------------------------------------------------------------
# TOOLS: Memory management (server | instruction | profile)
# ---------------------------------------------------------------------------
@mcp.tool()
def memory_write(ns: str, text: str, key: str = "", tags: list[str] | None = None,
                 pinned: bool = False, expires_in_days: int = 0,
                 allow_secret: bool = False) -> dict:
    """Simpan SATU fakta/arahan ke memory persisten. Routing otomatis:
    - ns "profile"/"cross" → cortex (global, lintas-project)
    - ns "server"/"instruction" → project memory (isolated per project)

    Args:
        ns: "server" (fakta infra), "instruction" (arahan project), "profile" (identitas
            operator — global), "cross" (fakta lintas-project — global).
        text: isi memory, ringkas dan mandiri (<= MEMORY_MAX_TEXT karakter).
        key: kunci stabil untuk upsert, mis. "fpm_service". Kosong = entry baru tiap kali.
        tags: label opsional untuk pencarian, mis. ["deploy","mysql"].
        pinned: True -> ikut ringkasan auto-load tiap sesi (untuk fakta penting).
        expires_in_days: >0 -> fakta sementara yang otomatis kedaluwarsa.
        allow_secret: True hanya jika sengaja menyimpan nilai mirip rahasia.
    """
    err = _validate_ns(ns)
    if err:
        return {"success": False, "error": err}
    text = (text or "").strip()
    if not text:
        return {"success": False, "error": "text kosong."}
    if len(text) > MEMORY_MAX_TEXT:
        return {"success": False, "error": f"text melebihi {MEMORY_MAX_TEXT} karakter."}
    if not allow_secret and _SECRET_RE.search(text):
        return {"success": False, "blocked_secret": True,
                "error": "DITOLAK: teks terlihat memuat rahasia (password/token/kunci). "
                         "Jangan simpan kredensial di memory. Set allow_secret=True hanya bila "
                         "ini memang bukan rahasia (mis. menjelaskan NAMA env var, bukan nilainya)."}
    tags = [str(t).strip() for t in (tags or []) if str(t).strip()][:12]

    cortex = _is_cortex_ns(ns)
    append_fn = _cortex_append if cortex else _mem_append
    fold_fn = _cortex_fold if cortex else _mem_fold
    compact_fn = _cortex_compact if cortex else _mem_compact

    rid = f"{ns}:{_slug(key)}" if key else f"{ns}:{_slug(text[:20])}-{secrets.token_hex(3)}"
    now = _now_iso()
    expires_at = None
    if expires_in_days and expires_in_days > 0:
        expires_at = (datetime.now(timezone.utc).replace(microsecond=0)
                      + timedelta(days=int(expires_in_days))).isoformat()

    record = {"id": rid, "ns": ns, "key": key or None, "text": text,
              "tags": tags, "source": "session", "created_at": now,
              "updated_at": now,
              "expires_at": expires_at, "pinned": bool(pinned), "deleted": False}

    similar = _find_similar(ns, rid, key or "", text)
    try:
        append_fn(record)
        live = fold_fn()
        if len(live) > MEMORY_MAX_ENTRIES:
            compact_fn(live)
    except Exception as e:
        return {"success": False, "error": f"gagal tulis memory: {e}"}
    scope = "cortex (global)" if cortex else f"project ({PROJECT_NAME or 'local'})"
    result: dict = {"success": True, "id": rid, "scope": scope, "entry": record}
    if similar:
        result["_similar_existing"] = similar
        result["_hint"] = ("Ada entry mirip di namespace yang sama. "
                           "Pertimbangkan memory_forget pada yang sudah tidak relevan.")
    return result


@mcp.tool()
def memory_recall(ns: str = "", query: str = "", tag: str = "", limit: int = 50) -> dict:
    """Ambil/cari memory persisten dengan semantic search (TF-IDF).
    Tanpa query -> semua entry hidup (pinned dulu, terbaru dulu).
    Dengan query -> ranking berdasarkan relevansi semantik + substring match.

    Args:
        ns: batasi ke namespace ("server"|"instruction"|"profile"|"cross"). Kosong = semua
            (cortex + project). Pencarian otomatis mencakup cortex dan project memory.
        query: pencarian semantik — keyword atau kalimat natural, mis. "cara deploy" atau "versi php".
        tag: filter berdasarkan satu tag.
        limit: maksimum entry dikembalikan.
    """
    if ns:
        err = _validate_ns(ns)
        if err:
            return {"success": False, "error": err}
    try:
        if not ns:
            items = list(_cortex_fold().values()) + list(_mem_fold().values())
        elif _is_cortex_ns(ns):
            items = list(_cortex_fold().values())
            items += [r for r in _mem_fold().values() if r.get("ns") == ns]
        else:
            items = list(_mem_fold().values())
    except Exception as e:
        return {"success": False, "error": f"gagal baca memory: {e}"}

    t = tag.strip().lower()
    candidates = []
    for r in items:
        if ns and r.get("ns") != ns:
            continue
        if t and t not in [str(x).lower() for x in (r.get("tags") or [])]:
            continue
        candidates.append(r)

    q = query.strip()
    if q:
        q_lower = q.lower()
        q_tokens = _tokenize(q)
        doc_tokens_map = {}
        corpus = []
        for r in candidates:
            tokens = _tokenize(_entry_text(r))
            doc_tokens_map[id(r)] = tokens
            corpus.append(tokens)
        if q_tokens:
            corpus.append(q_tokens)
        idf = _compute_idf(corpus)

        scored = []
        for r in candidates:
            tfidf = _tfidf_score(q_tokens, doc_tokens_map[id(r)], idf)
            full_text = (r.get("text", "") + " " + (r.get("key") or "")).lower()
            substr_hit = q_lower in full_text
            if substr_hit:
                tfidf = max(tfidf, 0.01)
            if tfidf > 0:
                scored.append((tfidf, r))
        scored.sort(key=lambda x: (-x[0], 0 if x[1].get("pinned") else 1))
        out = [r for _, r in scored]
    else:
        out = candidates
        out.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or "", reverse=True)
        out.sort(key=lambda r: 0 if r.get("pinned") else 1)

    out = out[:max(1, min(int(limit), 500))]
    result: dict = {"success": True, "count": len(out), "entries": out}
    if q:
        result["_search_mode"] = "semantic (TF-IDF)"
    return result


@mcp.tool()
def memory_forget(id: str = "", ns: str = "", key: str = "") -> dict:
    """Hapus (logis) sebuah memory dengan menulis tombstone. Sebut `id`, ATAU pasangan (ns,key).

    Args:
        id: id entry (mis. "server:fpm-service").
        ns: namespace, dipakai bersama key.
        key: kunci entry, dipakai bersama ns.
    """
    rid = id.strip()
    if not rid:
        if ns and key:
            err = _validate_ns(ns)
            if err:
                return {"success": False, "error": err}
            rid = f"{ns}:{_slug(key)}"
        else:
            return {"success": False, "error": "Sebutkan `id`, atau pasangan `ns` + `key`."}
    detect_ns = ns or (rid.split(":")[0] if ":" in rid else "")
    cortex = _is_cortex_ns(detect_ns)
    fold_fn = _cortex_fold if cortex else _mem_fold
    append_fn = _cortex_append if cortex else _mem_append
    try:
        live = fold_fn()
        existed = rid in live
        if not existed and not cortex:
            existed = rid in _cortex_fold()
            if existed:
                append_fn = _cortex_append
        append_fn({"id": rid, "deleted": True, "created_at": _now_iso()})
    except Exception as e:
        return {"success": False, "error": f"gagal hapus memory: {e}"}
    return {"success": True, "id": rid, "existed": existed}


@mcp.tool()
def memory_digest() -> dict:
    """Kembalikan ringkasan memory yang sama dengan yang disuntik ke konteks saat startup
    (profil user + instruksi durable + fakta server ter-pin). Berguna untuk menyegarkan
    ingatan di tengah sesi tanpa membaca seluruh entry."""
    cortex_active = _cortex_fold()
    project_active = _mem_fold()
    return {"success": True, "digest": _build_memory_digest(),
            "cortex_active": len(cortex_active),
            "project_active": len(project_active),
            "total_active": len(cortex_active) + len(project_active),
            "namespaces": list(MEMORY_NAMESPACES),
            "cortex_file": GLOBAL_MEMORY_FILE,
            "project_file": MEMORY_FILE}


@mcp.tool()
def memory_health() -> dict:
    """Diagnostik kesehatan memory (cortex + project): jumlah per namespace, entry basi,
    potensi duplikat, event count, dan ukuran file."""
    try:
        cortex_live = _cortex_fold()
        project_live = _mem_fold()
    except Exception as e:
        return {"success": False, "error": str(e)}

    now = datetime.now(timezone.utc)
    by_ns: dict[str, int] = {ns: 0 for ns in MEMORY_NAMESPACES}
    stale_entries: list[dict] = []
    all_items = list(cortex_live.values()) + list(project_live.values())

    for r in all_items:
        ns = r.get("ns")
        if ns in by_ns:
            by_ns[ns] += 1
        if _is_stale(r, now):
            age_days = 0
            updated = r.get("updated_at") or r.get("created_at") or ""
            if updated:
                try:
                    age_days = (now - datetime.fromisoformat(updated)).days
                except ValueError:
                    pass
            stale_entries.append({
                "id": r.get("id"), "key": r.get("key"),
                "text": (r.get("text") or "")[:80],
                "age_days": age_days,
            })

    duplicates: list[dict] = []
    seen_pairs: set[tuple] = set()
    for i, a in enumerate(all_items):
        for b in all_items[i + 1:]:
            if a.get("ns") != b.get("ns") or a.get("id") == b.get("id"):
                continue
            pair = tuple(sorted((a["id"], b["id"])))
            if pair in seen_pairs:
                continue
            text_a = f"{a.get('key') or ''} {a.get('text') or ''}"
            text_b = f"{b.get('key') or ''} {b.get('text') or ''}"
            overlap = _word_overlap(text_a, text_b)
            if overlap >= 0.4:
                seen_pairs.add(pair)
                duplicates.append({
                    "entry_a": {"id": a.get("id"), "text": (a.get("text") or "")[:60]},
                    "entry_b": {"id": b.get("id"), "text": (b.get("text") or "")[:60]},
                    "overlap": round(overlap, 2),
                })
    duplicates.sort(key=lambda x: x["overlap"], reverse=True)

    project_size = 0
    cortex_size = 0
    events_count = 0
    try:
        project_size = os.path.getsize(MEMORY_FILE)
    except OSError:
        pass
    try:
        cortex_size = os.path.getsize(GLOBAL_MEMORY_FILE)
    except OSError:
        pass
    try:
        events_count = len(_events_read(hours=EVENTS_DIGEST_HOURS))
    except Exception:
        pass

    return {
        "success": True,
        "cortex_active": len(cortex_live),
        "project_active": len(project_live),
        "total_active": len(all_items),
        "by_namespace": by_ns,
        "stale_count": len(stale_entries),
        "stale_threshold_days": STALE_DAYS,
        "stale_entries": stale_entries[:20],
        "potential_duplicates": duplicates[:10],
        "cortex_file_size": cortex_size,
        "project_file_size": project_size,
        "events_last_24h": events_count,
        "digest_budget": DIGEST_BUDGET,
    }


# ---------------------------------------------------------------------------
# CORTEX TOOLS: event journal lintas-project
# ---------------------------------------------------------------------------
@mcp.tool()
def cortex_log(event: str, detail: str = "", severity: str = "info") -> dict:
    """Log event lintas-project ke cortex event journal. Otomatis dipanggil ODIN saat
    deploy/restart service. Bisa juga dipanggil manual untuk event penting lainnya.

    Args:
        event: tipe event, mis. "deploy", "config_change", "incident", "maintenance".
        detail: deskripsi singkat (maks 500 karakter).
        severity: "info" (default), "warn", atau "error".
    """
    if severity not in ("info", "warn", "error"):
        severity = "info"
    project = PROJECT_NAME or "unknown"
    try:
        _event_append(project, event.strip()[:50], (detail or "").strip(), severity)
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "project": project, "event": event, "severity": severity}


@mcp.tool()
def cortex_events(hours: int = 24, project: str = "", limit: int = 50) -> dict:
    """Baca event journal lintas-project. Default: 24 jam terakhir dari semua project.

    Args:
        hours: rentang waktu ke belakang (jam). Default 24.
        project: filter ke satu project saja. Kosong = semua project.
        limit: maksimum event dikembalikan.
    """
    try:
        events = _events_read(hours=max(1, hours), project_filter=project.strip())
    except Exception as e:
        return {"success": False, "error": str(e)}
    events = events[-max(1, min(limit, 200)):]
    return {
        "success": True,
        "count": len(events),
        "hours": hours,
        "current_project": PROJECT_NAME,
        "events": events,
    }


# ---------------------------------------------------------------------------
# RESOURCE: intip memory per-namespace secara read-only (mis. memory://server)
# ---------------------------------------------------------------------------
@mcp.resource("memory://{ns}")
def memory_resource(ns: str) -> str:
    err = _validate_ns(ns)
    if err:
        return err
    store = _cortex_fold() if _is_cortex_ns(ns) else _mem_fold()
    items = [r for r in store.values() if r.get("ns") == ns]
    items = sorted(items, key=lambda r: (0 if r.get("pinned") else 1, r.get("created_at", "")))
    if not items:
        return f"(memory '{ns}' kosong)"
    lines = []
    for r in items:
        head = f"{r['key']}: " if r.get("key") else "- "
        tags = f"  [{', '.join(r['tags'])}]" if r.get("tags") else ""
        pin = " *" if r.get("pinned") else ""
        lines.append(f"- {head}{r.get('text', '')}{tags}{pin}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RESOURCE: watchdog — health check ringkas untuk polling via /loop
# ---------------------------------------------------------------------------
_WATCHDOG_THRESHOLDS = {"disk_pct": 85, "memory_pct": 90}


@mcp.resource("health://live")
def health_live() -> str:
    """Cek kesehatan ringkas: disk, memory, service status. Untuk polling via /loop."""
    issues: list[str] = []
    r = _run(
        "echo '@@DISK@@'; df --output=pcent / 2>/dev/null | tail -1 || df -h / | awk 'NR==2{print $5}'; "
        "echo '@@MEM@@'; free 2>/dev/null | awk '/^Mem:/{printf \"%.0f\", $3/$2*100}' || echo 0; "
        "echo '@@LOAD@@'; cat /proc/loadavg 2>/dev/null | awk '{print $1}' || echo 0; "
        "echo '@@NGINX@@'; systemctl is-active nginx 2>/dev/null || echo unknown; "
        "echo '@@MYSQL@@'; systemctl is-active mysql 2>/dev/null || echo unknown; "
        "echo '@@FPM@@'; systemctl list-units --type=service --state=running 2>/dev/null "
        "| grep -o 'php[0-9.]*-fpm' | head -1 || echo none",
        None, 15)
    s = _parse_sections(r.get("stdout", ""))
    dp = s.get("DISK", "0").strip().rstrip("%").strip()
    disk_pct = int(dp) if dp.isdigit() else 0
    mp = s.get("MEM", "0").strip()
    mem_pct = int(mp) if mp.isdigit() else 0
    load = s.get("LOAD", "0").strip()
    nginx = s.get("NGINX", "unknown").strip()
    mysql = s.get("MYSQL", "unknown").strip()
    fpm = s.get("FPM", "none").strip()
    if disk_pct >= _WATCHDOG_THRESHOLDS["disk_pct"]:
        issues.append(f"DISK {disk_pct}% >= {_WATCHDOG_THRESHOLDS['disk_pct']}%")
    if mem_pct >= _WATCHDOG_THRESHOLDS["memory_pct"]:
        issues.append(f"MEMORY {mem_pct}% >= {_WATCHDOG_THRESHOLDS['memory_pct']}%")
    for svc, status in [("nginx", nginx), ("mysql", mysql)]:
        if status not in ("active", "unknown"):
            issues.append(f"SERVICE {svc}: {status}")
    status_str = "ANOMALI" if issues else "OK"
    lines = [
        f"status: {status_str}",
        f"disk: {disk_pct}%",
        f"memory: {mem_pct}%",
        f"load: {load}",
        f"nginx: {nginx}",
        f"mysql: {mysql}",
        f"php-fpm: {fpm}",
    ]
    if issues:
        lines.append(f"issues: {'; '.join(issues)}")
    return "\n".join(lines)


if __name__ == "__main__":
    # ── Singleton: bunuh instance lama agar tidak menumpuk ───────────────
    # Setiap sesi MCP (SSH baru) men-spawn odin_agent.py baru. Tanpa guard,
    # instance lama yang koneksinya sudah putus tetap hidup dan menumpuk.
    _PIDFILE = os.path.join(MEMORY_DIR, "odin_agent.pid")
    os.makedirs(MEMORY_DIR, exist_ok=True)

    def _pid_release(*_):
        try:
            with open(_PIDFILE) as _f:
                if int(_f.read().strip()) == os.getpid():
                    os.unlink(_PIDFILE)
        except Exception:
            pass

    try:
        with open(_PIDFILE) as _f:
            _old_pid = int(_f.read().strip())
        if _old_pid != os.getpid():
            try:
                os.kill(_old_pid, signal.SIGTERM)
                log.info("singleton: SIGTERM → PID %d", _old_pid)
                time.sleep(1.5)
                os.kill(_old_pid, signal.SIGKILL)   # paksa jika belum mati
                log.info("singleton: SIGKILL → PID %d", _old_pid)
            except ProcessLookupError:
                pass  # sudah mati duluan — lanjut
    except (FileNotFoundError, ValueError):
        pass  # belum ada PID file — first run

    with open(_PIDFILE, "w") as _f:
        _f.write(str(os.getpid()))
    atexit.register(_pid_release)
    signal.signal(signal.SIGTERM, lambda s, f: (_pid_release(), sys.exit(0)))

    # ── Watchdog: exit otomatis saat koneksi SSH induk mati ──────────────
    # Saat SSH drop mendadak (network putus), stdin TIDAK selalu mengirim EOF.
    # Watchdog memantau parent PID (sshd); jika mati → proses ini ikut exit.
    _parent_pid = os.getppid()

    def _watchdog():
        while True:
            time.sleep(10)
            try:
                os.kill(_parent_pid, 0)   # signal 0 = probe, tidak mengirim signal
            except ProcessLookupError:
                log.info("watchdog: parent PID %d gone — exit", _parent_pid)
                _pid_release()
                os._exit(0)
            except PermissionError:
                pass  # proses masih hidup, tidak punya izin probe

    threading.Thread(target=_watchdog, daemon=True, name="odin-watchdog").start()

    log.info("ODIN v%s START | pid=%d ppid=%d deploy_mode=%s type=%s op_mode=%s target=%s project=%s root=%s",
             __version__, os.getpid(), _parent_pid,
             MODE, _PROFILE.get("type", "?"), _CURRENT_MODE,
             SSH_TARGET or "local", PROJECT_NAME or "-", PROJECT_ROOT or "-")
    mcp.run(transport="stdio")
