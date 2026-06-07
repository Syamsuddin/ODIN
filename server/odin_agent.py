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

__version__ = "1.1.0"

import fcntl
import json
import logging
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
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

PROJECT_ROOT = os.environ.get("PROJECT_ROOT", "").strip().rstrip("/")
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
MEMORY_NAMESPACES = ("server", "instruction", "profile")

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
# Inti: bangun & jalankan perintah (local atau ssh)
# ---------------------------------------------------------------------------
def _truncate(text: str) -> str:
    if not text or len(text) <= OUTPUT_LIMIT:
        return text or ""
    half = OUTPUT_LIMIT // 2
    return f"{text[:half]}\n...[{len(text) - OUTPUT_LIMIT} karakter dipotong]...\n{text[-half:]}"


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
    (r"SQLSTATE\[HY000\] \[1045\]|Access denied for user", "db_auth",
     "Autentikasi DB gagal. Cek: cat .env | grep DB_PASSWORD, lalu mysql -u<user> -p secara manual."),
    (r"SQLSTATE\[HY000\] \[2002\]|Can't connect to .* MySQL", "db_conn",
     "Tidak bisa konek ke database. Cek: systemctl status mysql && cat .env | grep DB_"),
    (r"SQLSTATE\[42S02\]|Table .+ doesn't exist|Base table .+ not found", "db_table_missing",
     "Tabel DB tidak ada. Jalankan: php artisan migrate --force."),
    (r"SQLSTATE\[42S22\]|Unknown column", "db_column_missing",
     "Kolom DB tidak ada. Cek migrasi terbaru; mungkin perlu php artisan migrate."),
    (r"SQLSTATE\[23000\]|Duplicate entry|Integrity constraint", "db_constraint",
     "Pelanggaran constraint DB (duplikat/FK). Cek data konflik sebelum retry."),
    (r"Too many connections|max_connections", "db_max_conn",
     "Koneksi DB penuh. Cek: mysql -e 'SHOW PROCESSLIST'."),
    (r"lock wait timeout|Deadlock found", "db_lock",
     "Deadlock/lock timeout. Cek: mysql -e 'SHOW ENGINE INNODB STATUS' | grep -A5 DEADLOCK."),
    (r"SQLSTATE", "db_error",
     "Error database. Periksa pesan SQLSTATE lengkap untuk diagnosis."),
    # --- PHP / Laravel ---
    (r"Class ['\"]?[\w\\]+['\"]? not found|ReflectionException", "class_not_found",
     "Class PHP tidak ditemukan. Jalankan: composer dump-autoload atau composer install."),
    (r"PHP Fatal error|PHP Parse error|Uncaught (?:Error|Exception)", "php_fatal",
     "Error fatal PHP. Periksa file & baris yang disebutkan di pesan error."),
    (r"Allowed memory size .+ exhausted", "php_oom",
     "PHP kehabisan memori. Naikkan memory_limit di php.ini atau jalankan di CLI."),
    (r"max_execution_time|Maximum execution time .+ exceeded", "php_timeout",
     "Timeout PHP. Di CLI tidak ada batas; atau naikkan max_execution_time di php.ini."),
    (r"Your lock file does not contain a compatible set|Composer detected issues", "composer_lock",
     "composer.lock tidak sinkron. Jalankan: composer update (bukan install)."),
    # --- Sistem ---
    (r"No space left on device", "disk_full",
     "Disk penuh. Jalankan: df -h && du -sh /var/log/* /tmp/* | sort -rh | head"),
    (r"Out of memory|oom-kill|Cannot allocate memory|SIGKILL|signal 9|Killed", "killed",
     "Proses di-kill (kemungkinan OOM). Cek: dmesg | tail -20 && free -h."),
    (r"Permission denied", "permission",
     "Akses ditolak. Periksa kepemilikan (ls -la) dan izin user."),
    (r"command not found", "missing_cmd",
     "Perintah tidak ditemukan. Cek: which <cmd> atau apt list --installed | grep <pkg>."),
    (r"Connection refused", "conn_refused",
     "Koneksi ditolak. Cek apakah service jalan: systemctl status <service>."),
    (r"failed to open stream|No such file or directory", "file_not_found",
     "File/directory tidak ditemukan. Verifikasi path: ls -la <path>."),
    (r"Address already in use|port .+ already in use", "port_in_use",
     "Port sudah terpakai. Cek: ss -tlnp | grep <port>."),
    # --- Tool-specific ---
    (r"nginx: \[emerg\]|nginx:.*test failed|nginx:.*configuration.*failed", "nginx_config",
     "Config Nginx error. Jalankan: nginx -t untuk detail. JANGAN reload sebelum config valid."),
    (r"SSL.+error|certificate.+expired|SSL_ERROR", "ssl",
     "Masalah SSL. Cek: certbot certificates."),
    (r"npm ERR!", "npm_error",
     "Error npm. Coba: rm -rf node_modules && npm ci."),
]


def _analyze_output(result: dict) -> dict:
    """Scan stdout+stderr untuk pola error umum. Kembalikan hints untuk Claude."""
    if result.get("success"):
        return {}
    hints: list[str] = []
    error_type = None
    if result.get("timeout"):
        hints.append("Perintah timeout. Naikkan timeout atau pecah jadi langkah lebih kecil.")
        error_type = "timeout"
    combined = ((result.get("stdout") or "") + " " + (result.get("stderr") or "")).strip()
    if combined:
        for pattern, etype, hint in _ERROR_PATTERNS:
            if re.search(pattern, combined, re.IGNORECASE):
                if not error_type:
                    error_type = etype
                hints.append(hint)
    if not hints and result.get("exit_code") and result["exit_code"] > 0:
        hints.append(f"Exit code {result['exit_code']}. Baca stderr untuk diagnosis.")
        error_type = error_type or "generic_failure"
    if not hints:
        return {}
    return {"error_type": error_type, "hints": hints}


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


def _build_memory_digest() -> str:
    """Ringkasan padat untuk disuntik ke FastMCP(instructions=...) saat startup,
    sehingga memory otomatis termuat di konteks tiap sesi baru."""
    try:
        live = list(_mem_fold().values())
    except Exception as e:  # jangan biarkan memory rusak menjatuhkan server
        log.warning("gagal baca memory utk digest: %s", e)
        return ""
    if not live:
        return ""

    def fmt(rec: dict) -> str:
        key = rec.get("key")
        tags = rec.get("tags") or []
        tagstr = f"  [{', '.join(tags)}]" if tags else ""
        head = f"{key}: " if key else "- "
        return f"- {head}{rec.get('text', '')}{tagstr}"

    by_ns = {ns: [] for ns in MEMORY_NAMESPACES}
    for r in live:
        ns = r.get("ns")
        if ns in by_ns:
            by_ns[ns].append(r)

    blocks: list[str] = []
    titles = {
        "profile": "PROFIL USER",
        "instruction": "INSTRUKSI DURABLE DARI USER (patuhi)",
        "server": "FAKTA SERVER (yang di-pin)",
    }
    for ns in ("profile", "instruction", "server"):
        items = by_ns[ns]
        if ns == "server":
            items = [r for r in items if r.get("pinned")]  # server: hanya yang di-pin
        if not items:
            continue
        items = sorted(items, key=lambda r: (0 if r.get("pinned") else 1, r.get("created_at", "")))
        lines = "\n".join(fmt(r) for r in items)
        blocks.append(f"### {titles[ns]}\n{lines}")

    if not blocks:
        return ""
    body = "\n\n".join(blocks)
    return ("## MEMORY ODIN (otomatis dari simpanan; perbarui via memory_write/forget)\n"
            f"{body}\n\n"
            "Pakai memory_recall untuk detail lebih, memory_write untuk menyimpan fakta/arahan baru.")


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
    pre = _capture_pre_state(command, cwd or None)
    result = _run(command, cwd or None, timeout, allow_dangerous)
    analysis = _analyze_output(result)
    if analysis:
        result["_analysis"] = analysis
    rb = _suggest_rollback(command, pre) if pre else []
    if rb:
        result["_rollback_hint"] = rb
    result["_summary"] = _build_summary(result)
    _audit("run_command", command, result)
    _session_log("run_command", command, result, pre_state=pre or None, rollback=rb or None)
    return result


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
    _audit("tail_log", f"{path} lines={n} grep={grep!r}", result)
    _session_log("tail_log", f"{path} grep={grep!r}", result)
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
    return result


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
    _audit("laravel_deploy", f"{branch} -> {app_path} failed={failed}", result)
    _session_log("laravel_deploy", f"deploy {branch} -> {app_path}", result)
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
# TOOLS: Memory management (server | instruction | profile)
# ---------------------------------------------------------------------------
@mcp.tool()
def memory_write(ns: str, text: str, key: str = "", tags: list[str] | None = None,
                 pinned: bool = False, expires_in_days: int = 0,
                 allow_secret: bool = False) -> dict:
    """Simpan SATU fakta/arahan ke memory persisten agent. Upsert: jika (ns,key) sudah ada,
    nilainya ditimpa. Memory bertahan lintas sesi & otomatis muncul di konteks sesi baru.

    Pedomani: simpan KESIMPULAN/ATURAN yang padat, BUKAN transkrip percakapan.

    Args:
        ns: namespace -> "server" (fakta infrastruktur), "instruction" (arahan durable user),
            "profile" (identitas user). Hanya 3 ini yang diterima.
        text: isi memory, ringkas dan mandiri (<= MEMORY_MAX_TEXT karakter).
        key: kunci stabil untuk upsert, mis. "fpm_service". Kosong = entry baru tiap kali.
        tags: label opsional untuk pencarian, mis. ["deploy","mysql"].
        pinned: True -> ikut ringkasan auto-load tiap sesi (untuk fakta penting).
                Catatan: profile & instruction selalu ikut auto-load; pin terutama untuk ns=server.
        expires_in_days: >0 -> fakta sementara yang otomatis kedaluwarsa (mis. jadwal malam ini).
        allow_secret: True hanya jika sengaja menyimpan nilai yang terlihat seperti rahasia
                      (default: nilai mirip password/token/kunci DITOLAK).
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

    rid = f"{ns}:{_slug(key)}" if key else f"{ns}:{_slug(text[:20])}-{secrets.token_hex(3)}"
    now = _now_iso()
    expires_at = None
    if expires_in_days and expires_in_days > 0:
        expires_at = (datetime.now(timezone.utc).replace(microsecond=0)
                      + timedelta(days=int(expires_in_days))).isoformat()

    record = {"id": rid, "ns": ns, "key": key or None, "text": text,
              "tags": tags, "source": "session", "created_at": now,
              "expires_at": expires_at, "pinned": bool(pinned), "deleted": False}
    try:
        _mem_append(record)
        # compaction lembut bila log membengkak
        live = _mem_fold()
        if len(live) > MEMORY_MAX_ENTRIES:
            _mem_compact(live)
    except Exception as e:
        return {"success": False, "error": f"gagal tulis memory: {e}"}
    return {"success": True, "id": rid, "entry": record}


@mcp.tool()
def memory_recall(ns: str = "", query: str = "", tag: str = "", limit: int = 50) -> dict:
    """Ambil/cari memory persisten. Tanpa argumen -> semua entry hidup (pinned dulu, terbaru dulu).

    Args:
        ns: batasi ke namespace ("server"|"instruction"|"profile"). Kosong = semua.
        query: filter substring case-insensitive pada text/key.
        tag: filter berdasarkan satu tag.
        limit: maksimum entry dikembalikan.
    """
    if ns:
        err = _validate_ns(ns)
        if err:
            return {"success": False, "error": err}
    try:
        items = list(_mem_fold().values())
    except Exception as e:
        return {"success": False, "error": f"gagal baca memory: {e}"}
    q = query.strip().lower()
    t = tag.strip().lower()
    out = []
    for r in items:
        if ns and r.get("ns") != ns:
            continue
        if q and q not in (r.get("text", "") + " " + (r.get("key") or "")).lower():
            continue
        if t and t not in [str(x).lower() for x in (r.get("tags") or [])]:
            continue
        out.append(r)
    # urutan: pinned dulu, lalu terbaru dulu. sort stabil -> dua tahap.
    out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    out.sort(key=lambda r: 0 if r.get("pinned") else 1)
    out = out[:max(1, min(int(limit), 500))]
    return {"success": True, "count": len(out), "entries": out}


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
    try:
        live = _mem_fold()
        existed = rid in live
        _mem_append({"id": rid, "deleted": True, "created_at": _now_iso()})
    except Exception as e:
        return {"success": False, "error": f"gagal hapus memory: {e}"}
    return {"success": True, "id": rid, "existed": existed}


@mcp.tool()
def memory_digest() -> dict:
    """Kembalikan ringkasan memory yang sama dengan yang disuntik ke konteks saat startup
    (profil user + instruksi durable + fakta server ter-pin). Berguna untuk menyegarkan
    ingatan di tengah sesi tanpa membaca seluruh entry."""
    active = _mem_fold()
    return {"success": True, "digest": _build_memory_digest(),
            "total_active": len(active),
            "namespaces": list(MEMORY_NAMESPACES), "file": MEMORY_FILE}


# ---------------------------------------------------------------------------
# RESOURCE: intip memory per-namespace secara read-only (mis. memory://server)
# ---------------------------------------------------------------------------
@mcp.resource("memory://{ns}")
def memory_resource(ns: str) -> str:
    err = _validate_ns(ns)
    if err:
        return err
    items = [r for r in _mem_fold().values() if r.get("ns") == ns]
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


if __name__ == "__main__":
    log.info("ODIN v%s START | deploy_mode=%s type=%s op_mode=%s target=%s project=%s",
             __version__,
             MODE, _PROFILE.get("type", "?"), _CURRENT_MODE,
             SSH_TARGET or "local", PROJECT_ROOT or "-")
    mcp.run(transport="stdio")
