#!/usr/bin/env python3
"""
deploy_agent.py — MCP server "tangan" untuk Claude Code.

Otak  = Claude Code (CLI).
Tangan = server ini. Claude Code memanggil tool di sini lewat protokol MCP (stdio),
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
  MEMORY_DIR         /home/deploy/agent/memory        (folder simpanan memory)
  MEMORY_MAX_TEXT    4000                             (panjang maks teks satu entry)
  MEMORY_MAX_ENTRIES 2000                             (batas entry hidup; lebih -> compaction)
=========================================================================

Pasang:  pip install "mcp[cli]"
Jalan :  python3 deploy_agent.py     (dijalankan otomatis oleh Claude Code via MCP)
"""

from __future__ import annotations

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
    format="%(asctime)s [deploy-agent] %(levelname)s: %(message)s",
)
log = logging.getLogger("deploy-agent")

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
    """Cek prefix berbasis string (tanpa sentuh filesystem -> aman utk mode ssh)."""
    norm = os.path.normpath(path)
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


def _mem_fold() -> dict[str, dict]:
    """Baca seluruh log, lipat jadi state terkini per id (last-write-wins).
    Buang record ber-deleted=True dan yang sudah kedaluwarsa."""
    if not os.path.exists(MEMORY_FILE):
        return {}
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
    return {rid: r for rid, r in state.items()
            if not r.get("deleted") and not _is_expired(r, now)}


def _mem_compact(live: dict[str, dict]) -> None:
    """Tulis ulang log hanya berisi record hidup (atomic via temp + os.replace)."""
    _ensure_store()
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
    return ("## MEMORY deploy-agent (otomatis dari simpanan; perbarui via memory_write/forget)\n"
            f"{body}\n\n"
            "Pakai memory_recall untuk detail lebih, memory_write untuk menyimpan fakta/arahan baru.")


def _validate_ns(ns: str) -> str | None:
    if ns not in MEMORY_NAMESPACES:
        return f"ns '{ns}' tidak dikenal. Pilih: {list(MEMORY_NAMESPACES)}"
    return None


# Bangun server MCP. instructions= disuntik dari memory SEKARANG, sehingga profil
# user + instruksi durable + fakta server ter-pin otomatis termuat tiap sesi baru
# (server di-spawn fresh per sesi). Harus dibuat SEBELUM dekorator @mcp.tool().
mcp = FastMCP("deploy-agent", instructions=_build_memory_digest())


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
    return _run(command, cwd or None, timeout, allow_dangerous)


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
    return _run(cmd, None, 60)


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
    return _run(f"{sudo}systemctl {action} {shlex.quote(service)}{pager}", None, 60)


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
    steps: list[dict] = []

    def do(label: str, command: str, t: int = DEFAULT_TIMEOUT) -> bool:
        r = _run(command, app_path, t)
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
    return {"success": len(failed) == 0, "failed_steps": failed,
            "app_path": app_path, "branch": branch, "steps": steps}


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
    return _run(cmd, app_path, 900)


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
    return r


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
    return {"success": True, "digest": _build_memory_digest(),
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
    log.info("deploy-agent START | mode=%s target=%s project_root=%s memory=%s",
             MODE, SSH_TARGET or "local", PROJECT_ROOT or "-", MEMORY_FILE)
    mcp.run(transport="stdio")
