#!/usr/bin/env python3
"""
ODIN v1.0.0 — PreToolUse guard + RISK ENGINE untuk MCP ODIN.

Model keamanan (sesuai niat user — akses penuh ke server live, keputusan akhir di user):
  - Perintah READ  (inspeksi file/sistem & kueri baca DB: SELECT/SHOW/DESCRIBE) -> allow
  - Perintah WRITE (mengubah state, termasuk DML/DDL database)  -> ask (KARTU RISIKO)
  - Perintah KATASTROFIK                         -> ask   (kartu risiko + rem darurat
                                                   allow_dangerous di server)

KARTU RISIKO yang dikembalikan ke prompt konfirmasi berisi:
  RISIKO (tier) · Aksi (apa yang dilakukan) · Efek (blast-radius) · Saran (rekomendasi).
Tujuannya: user dapat menilai cepat lalu memutuskan. Hook = gerbang UX; batas keamanan
sebenarnya tetap hak OS user `odin` + sudoers + hard-block `_DANGER_RE` di server.

Baca JSON dari stdin, klasifikasikan run_command / service_action, kembalikan
permissionDecision + reason (kartu risiko). Tool lain: tak berpendapat (fall-through).
Pada error apa pun -> exit 0 tanpa output (jangan memblokir karena bug guard).
"""
import json
import os

__version__ = "1.1.0"
import re
import subprocess
import sys


def emit(decision: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


# Pola katastrofik / mengganggu sistem (selaras _DANGER_RE server + tambahan DB drop).
DANGER = re.compile("|".join([
    r"\brm\s+-rf\s+/(\s|$|\*)", r"\brm\s+-rf\s+~", r"\bmkfs\b",
    r"\bdd\b.*\bof=/dev/", r">\s*/dev/sd[a-z]", r":\(\)\s*\{",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\binit\s+0\b",
    r"\bchmod\s+-R\s+777\s+/", r"\bchown\s+-R\b.*\s/\s*$",
    r"\bdrop\s+database\b", r"\bmysqladmin\b.*\bdrop\b",
    r"\bkill(all)?\b", r"\bpkill\b",
]), re.IGNORECASE)

# Perintah inspeksi murni (read-only).
READ_CMDS = {
    "ls", "ll", "cat", "bat", "head", "tail", "less", "more", "nl", "tac",
    "grep", "egrep", "fgrep", "rg", "ag", "zgrep",
    "find", "fd", "stat", "file", "wc", "df", "du", "free", "uptime", "uname",
    "hostname", "whoami", "id", "who", "w", "groups", "ps", "pgrep", "pstree",
    "date", "cal", "echo", "printf", "pwd", "env", "printenv", "which",
    "command", "type", "whereis", "basename", "dirname", "readlink", "realpath",
    "tree", "sort", "uniq", "cut", "tr", "column", "paste", "comm", "join",
    "diff", "cmp", "md5sum", "sha1sum", "sha256sum", "cksum", "xxd", "od",
    "hexdump", "strings", "awk", "jq", "yq", "fold", "expand", "unexpand",
    "getent", "lsblk", "lscpu", "lsusb", "lspci", "lsof", "ss", "netstat",
    "ping", "dig", "nslookup", "host", "vmstat", "iostat", "mpstat", "sar",
    "dmesg", "journalctl", "true", "false", "test", "seq",
}
GIT_READ = {
    "status", "log", "diff", "show", "rev-parse", "describe", "ls-files",
    "ls-tree", "blame", "shortlog", "cat-file", "reflog", "grep", "name-rev",
    "count-objects", "var", "whatchanged",
}
PHP_READ = {"-v", "--version", "-m", "-i", "-l", "--ini", "--rf", "--ri"}
COMPOSER_READ = {"show", "--version", "-V", "diagnose", "validate", "licenses",
                 "outdated", "status", "about", "depends", "prohibits", "why"}
SYSTEMCTL_READ = {"status", "is-active", "is-enabled", "is-failed", "list-units",
                  "list-unit-files", "show", "cat", "get-default"}
DOCKER_READ = {"ps", "images", "logs", "inspect", "version", "info", "stats",
               "top", "port", "diff", "history"}
# Klien DB read-only -> SELECT/SHOW/DESCRIBE/EXPLAIN aman dibaca.
DB_READ_VERBS = re.compile(r"^\s*(select|show|describe|desc|explain|use|pragma)\b", re.I)
# Aksi `find` yang sebenarnya MENULIS/menghapus -> bukan read.
FIND_WRITE = re.compile(r"^-(delete|exec|execdir|ok|okdir|fprint|fls|fprintf)$")

# --- Package manager & system tool: sub-perintah inspeksi (read-only) ---------
APT_READ = {"list", "show", "search", "depends", "rdepends", "policy",
            "showsrc", "changelog", "madison", "info"}
DPKG_READ = {"-l", "--list", "-L", "--listfiles", "-s", "--status",
             "-S", "--search", "-p", "--print-avail", "--info", "-I",
             "--contents", "-c", "--audit"}
PIP_READ = {"list", "show", "freeze", "check", "config", "--version", "-V"}
NPM_READ = {"list", "ls", "view", "info", "outdated", "why", "audit",
            "explain", "--version", "-v"}
IP_READ = {"addr", "a", "address", "route", "r", "link", "l", "neigh", "n",
           "neighbour", "rule", "tunnel", "maddress", "mroute", "monitor"}
IP_WRITE_ACTIONS = {"add", "del", "delete", "change", "replace", "set",
                    "flush", "append"}
UFW_READ = {"status", "show", "version"}
NGINX_READ = {"-t", "-T", "-V", "-v"}
FAIL2BAN_READ = {"status", "get", "ping", "banned", "version"}
CERTBOT_READ = {"certificates", "--help", "-h"}
TIMEDATECTL_READ = {"status", "show", "timesync-status", "list-timezones"}
LOGINCTL_READ = {"list-sessions", "list-users", "show-session", "show-user",
                 "session-status", "user-status"}
CURL_WRITE_FLAGS = {"--request", "--data", "--data-raw", "--data-binary",
                    "--data-urlencode", "--form", "--upload-file",
                    "--output", "--remote-name"}

# --- Klien database: kueri baca (SELECT/SHOW/DESCRIBE) boleh auto-jalan -------
DB_CLIENTS = {"mysql", "mariadb", "psql", "sqlite3", "mysqldump", "mysqladmin"}
_SQL_WRITE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|grant|revoke|"
    r"rename|lock|unlock|call|load\s+data|outfile|dumpfile|copy)\b", re.I)
_SQL_READ = re.compile(r"\b(select|show|describe|desc|explain|use|pragma)\b", re.I)
# meta-command read-only: sqlite (.tables) & psql (\dt \l \d ...)
_DB_META_READ = re.compile(r"\.(tables|schema|databases|indexes|dbinfo)\b|"
                           r"\\d[a-z]*\b|\\l\b|\\z\b", re.I)


def _strip_quotes(s: str) -> str:
    """Kosongkan isi string ber-quote agar operator SQL (>,<,|) di dalamnya tak
    dikira metakarakter shell saat deteksi redirect."""
    s = re.sub(r"'[^']*'", "''", s)
    s = re.sub(r'"[^"]*"', '""', s)
    return s


def _db_seg_is_read(cmd: str, seg: str) -> bool:
    """True hanya bila segmen klien DB ini JELAS read-only (boleh auto-jalan)."""
    if cmd in ("mysqldump", "mysqladmin"):
        return False                       # backup/admin → tetap konfirmasi
    if re.search(r"<\s*\S", seg):          # input dari file/heredoc → isi tak diketahui
        return False
    if _SQL_WRITE.search(seg):
        return False
    if _SQL_READ.search(seg) or _DB_META_READ.search(seg):
        return True
    return False                           # tak ada verb baca jelas (mis. shell interaktif)


def seg_is_read(seg: str) -> bool:
    toks = seg.split()
    i = 0
    while i < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i]):
        i += 1  # lewati env assignment di depan (VAR=val cmd ...)
    if i >= len(toks):
        return True
    cmd = toks[i].split("/")[-1]
    args = toks[i + 1:]
    if cmd in ("sudo", "su", "doas"):
        return False
    if cmd == "git":
        return bool(args) and args[0] in GIT_READ
    if cmd == "php":
        return bool(args) and args[0] in PHP_READ
    if cmd == "composer":
        return bool(args) and args[0] in COMPOSER_READ
    if cmd == "systemctl":
        return bool(args) and args[0] in SYSTEMCTL_READ
    if cmd == "docker":
        return bool(args) and args[0] in DOCKER_READ
    if cmd == "sed":
        return not any(a == "-i" or a.startswith("-i") for a in args)
    if cmd == "find":  # find membaca KECUALI ada -delete/-exec/-fprint dst.
        return not any(FIND_WRITE.match(a) for a in args)
    if cmd in ("awk", "gawk", "mawk"):  # awk bisa menulis lewat system()/redirect
        return not re.search(r"system\s*\(|print(f)?\s*>|>>", seg)
    if cmd in DB_CLIENTS:
        return _db_seg_is_read(cmd, seg)
    if cmd in ("apt", "apt-get", "apt-cache"):
        return bool(args) and args[0] in APT_READ
    if cmd == "dpkg":
        return bool(args) and args[0] in DPKG_READ
    if cmd in ("pip", "pip3"):
        return bool(args) and args[0] in PIP_READ
    if cmd in ("npm", "yarn", "pnpm"):
        return bool(args) and args[0] in NPM_READ
    if cmd == "ip":
        if not args:
            return True
        return args[0] in IP_READ and not any(a in IP_WRITE_ACTIONS for a in args[1:])
    if cmd == "ufw":
        return bool(args) and args[0] in UFW_READ
    if cmd in ("nginx", "apache2ctl", "httpd"):
        return bool(args) and args[0] in NGINX_READ
    if cmd == "fail2ban-client":
        return bool(args) and args[0] in FAIL2BAN_READ
    if cmd == "certbot":
        return bool(args) and args[0] in CERTBOT_READ
    if cmd == "timedatectl":
        return not args or args[0] in TIMEDATECTL_READ
    if cmd == "loginctl":
        return bool(args) and args[0] in LOGINCTL_READ
    if cmd == "curl":
        for a in args:
            if a.startswith("--"):
                if a.split("=")[0] in CURL_WRITE_FLAGS:
                    return False
            elif a.startswith("-") and len(a) > 1:
                if any(c in "XdFToO" for c in a[1:]):
                    return False
        return True
    return cmd in READ_CMDS


def classify_command(command: str) -> str:
    """Kembalikan keputusan izin: 'allow' (read) | 'ask' (write/danger)."""
    if not command.strip():
        return "ask"
    if DANGER.search(command):
        return "ask"
    if re.search(r'\$\(|`', command):
        return "ask"
    # Abaikan redirect stderr/stdout ke /dev/null (lazim di perintah read).
    c = re.sub(r"\d?>\s*/dev/null", " ", command)
    c = re.sub(r"2>&1", " ", c)
    # Redirect ke file nyata (bukan didahului digit fd) atau tee => menulis.
    # Strip string ber-quote dulu agar operator SQL (>,<) dalam -e "..." tak
    # salah dikira redirect shell (mis. mysql -e "SELECT a>b").
    c_noq = _strip_quotes(c)
    if re.search(r"(^|[^0-9&])>>?\s*\S", c_noq) or re.search(r"\btee\b", c_noq):
        return "ask"
    segments = re.split(r"\|\||&&|[|;&\n]", c)
    return "allow" if all(seg_is_read(s) for s in segments) else "ask"


# ===========================================================================
# RISK ENGINE — tier + aksi + efek + saran
# tier: AMAN < RENDAH < SEDANG < TINGGI < KRITIS
# ===========================================================================
TIER_ORDER = {"AMAN": 0, "RENDAH": 1, "SEDANG": 2, "TINGGI": 3, "KRITIS": 4}
TIER_ICON = {"AMAN": "🟢", "RENDAH": "🟢", "SEDANG": "🟡", "TINGGI": "🟠", "KRITIS": "🔴"}

DB_CLIENT = re.compile(r"\b(mysql|mysqldump|mariadb|psql|sqlite3)\b", re.I)


def _assess_db(command: str):
    """(tier, aksi, efek, saran) jika ini perintah klien DB; selain itu None."""
    if not DB_CLIENT.search(command):
        return None
    low = command.lower()
    if re.search(r"\bmysql\w*\b[^|;]*<\s*\S+", low):
        return ("TINGGI", "Jalankan skrip SQL dari file ke database",
                "Isi file tak terlihat dari perintah — bisa memuat DROP/DELETE/ALTER massal",
                "Tinjau isi file dulu (cat). Backup DB: mysqldump --single-transaction --no-tablespaces")
    if re.search(r"\bmysqldump\b", low):
        return ("RENDAH", "Backup/dump database (operasi baca)",
                "Membaca seluruh DB lalu menulis berkas dump; data tak berubah",
                "Sertakan --single-transaction --no-tablespaces (privilege terbatas)")
    if re.search(r"\b(outfile|dumpfile)\b", low):
        return ("SEDANG", "SELECT ... INTO OUTFILE/DUMPFILE — tulis berkas di server DB",
                "Menulis berkas ke filesystem server database",
                "Pastikan path & izin benar; ini operasi tulis, bukan baca")
    if re.search(r"\bdrop\s+(database|schema)\b", low):
        return ("KRITIS", "DROP DATABASE — hapus seluruh database",
                "SEMUA tabel & data hilang permanen, tak bisa di-undo",
                "STOP. Backup penuh dulu; pastikan nama database benar")
    if re.search(r"\bdrop\s+(table|view|index|trigger|procedure|function)\b", low):
        return ("TINGGI", "DROP objek database (tabel/view/index)",
                "Objek beserta isinya hilang permanen",
                "Backup tabel terkait; verifikasi nama objek")
    if re.search(r"\btruncate\b", low):
        return ("TINGGI", "TRUNCATE — kosongkan tabel",
                "Semua baris tabel terhapus (DDL, tak ter-rollback di MySQL)",
                "Backup dulu; pastikan tabel benar")
    if re.search(r"\bdelete\b", low) and "from" in low:
        if re.search(r"\bwhere\b", low):
            return ("SEDANG", "DELETE baris terfilter",
                    "Menghapus baris yang cocok klausa WHERE",
                    "SELECT COUNT(*) dgn WHERE sama dulu, atau bungkus transaksi + ROLLBACK")
        return ("TINGGI", "DELETE TANPA WHERE",
                "SEMUA baris tabel terhapus",
                "Tambahkan WHERE bila tak disengaja; backup dulu")
    if re.search(r"\bupdate\b", low) and "set" in low:
        if re.search(r"\bwhere\b", low):
            return ("SEDANG", "UPDATE baris terfilter",
                    "Mengubah kolom pada baris yang cocok WHERE",
                    "Verifikasi WHERE; bungkus transaksi + ROLLBACK utk hitung dampak")
        return ("TINGGI", "UPDATE TANPA WHERE",
                "SEMUA baris tabel berubah",
                "Tambahkan WHERE; backup dulu")
    if re.search(r"\balter\s+table\b", low):
        return ("SEDANG", "ALTER TABLE — ubah skema",
                "Struktur tabel berubah; pada tabel besar bisa lock & lama",
                "Backup; uji di staging; pilih jam sepi")
    if re.search(r"\b(grant|revoke)\b", low):
        return ("SEDANG", "Ubah hak akses DB (GRANT/REVOKE)",
                "Privilege user database berubah",
                "Terapkan least-privilege")
    if re.search(r"\b(insert|replace)\s+into\b", low):
        return ("RENDAH", "INSERT/REPLACE baris",
                "Menambah (REPLACE bisa menimpa) baris",
                "REPLACE menghapus baris konflik — cek bila tak diinginkan")
    if re.search(r"\bcreate\s+(table|database|index|view)\b", low):
        return ("RENDAH", "CREATE objek database",
                "Membuat objek baru; tak menyentuh data lama", "—")
    if DB_READ_VERBS.search(re.sub(r"^.*?-e\s+['\"]?", "", command)) or \
            re.search(r"\b(select|show|describe|desc|explain)\b", low):
        return ("AMAN", "Kueri baca database (SELECT/SHOW/DESCRIBE)",
                "Hanya membaca; data tak berubah", "—")
    return ("SEDANG", "Perintah klien database (verb tak dikenali)",
            "Tak bisa dipastikan baca/tulis dari pola perintah",
            "Tinjau perintah; jalankan kueri baca dulu bila ragu")


# (regex, tier, aksi, efek, saran) — pilih tier tertinggi yang cocok.
_SHELL_RULES = [
    (r"\brm\s+-rf?\s+/(\s|$|\*)", "KRITIS", "rm -rf pada root filesystem",
     "Kerusakan total OS; server tak bisa boot", "JANGAN. Pastikan path tak mengarah ke /"),
    (r"\bmkfs\b", "KRITIS", "Format filesystem (mkfs)",
     "Seluruh data partisi hilang", "Pastikan device benar; destruktif total"),
    (r"\bdd\b.*\bof=/dev/", "KRITIS", "dd menimpa block device",
     "Disk/partisi tertimpa; data hilang", "Periksa of= dengan teliti"),
    (r"\b(shutdown|reboot|halt)\b|\binit\s+0\b", "KRITIS", "Matikan/restart server",
     "Server offline; sesi SSH & semua layanan terputus", "Pastikan memang ingin mematikan server live"),
    (r"\bchmod\s+-R\s+777\s+/", "KRITIS", "chmod 777 rekursif dari root",
     "Izin sistem rusak; risiko keamanan & gagal boot", "Jangan; batasi ke folder spesifik"),
    (r":\(\)\s*\{", "KRITIS", "Fork bomb",
     "Habiskan resource; server hang", "Jangan jalankan"),
    (r"\brm\s+-rf?\b", "TINGGI", "Hapus rekursif paksa (rm -rf)",
     "Folder & seluruh isinya terhapus permanen, tak bisa undo", "Cek path; backup bila berisi data"),
    (r"\bgit\s+reset\s+--hard\b", "TINGGI", "git reset --hard",
     "Semua perubahan lokal belum ter-commit hilang", "Pastikan tak ada kerja penting belum di-stash"),
    (r"\bgit\s+clean\s+-[a-z]*f", "TINGGI", "git clean -f",
     "File untracked terhapus permanen", "Jalankan 'git clean -n' dulu utk pratinjau"),
    (r"\b(killall|pkill)\b", "TINGGI", "Hentikan banyak proses (killall/pkill)",
     "Semua proses cocok nama dimatikan; layanan bisa tumbang", "Pastikan target; pertimbangkan systemctl"),
    (r"\bfind\b.*\s-delete\b", "TINGGI", "find -delete — hapus banyak file hasil pencarian",
     "Semua file yang cocok kriteria find terhapus permanen", "Jalankan tanpa -delete dulu utk lihat daftarnya"),
    (r"\bfind\b.*\s-(exec|execdir|ok)\b", "TINGGI", "find -exec — jalankan perintah pada tiap hasil",
     "Perintah dieksekusi utk setiap file cocok; bisa rm/chmod massal", "Tinjau -exec; jalankan tanpa -exec dulu"),
    (r"\b(apt|apt-get|dpkg|yum|dnf)\b.*\b(install|remove|purge|upgrade|autoremove)\b", "SEDANG",
     "Ubah paket sistem", "Paket terpasang/terhapus; dependensi & layanan bisa terpengaruh",
     "Catat paket; pastikan repo terpercaya"),
    (r"\bsystemctl\b.*\b(restart|stop)\b|\bservice\s+\S+\s+(restart|stop)\b", "SEDANG",
     "Restart/stop service", "Layanan mati sesaat → request gagal (downtime singkat)",
     "Saat trafik rendah; pakai reload bila cukup"),
    (r"\bphp\s+artisan\s+migrate\b", "SEDANG", "Migrasi skema DB (artisan migrate)",
     "Struktur DB berubah; rollback belum tentu mulus", "Backup DB dulu"),
    (r"\bchown\b", "SEDANG", "Ubah kepemilikan file (chown)",
     "Owner/group berubah; bisa pengaruhi akses layanan", "Periksa flag -R dan target path"),
    (r"\bchmod\b", "SEDANG", "Ubah izin file (chmod)",
     "Hak akses berubah; salah set → bocor/rusak akses", "Hindari 777; spesifik per file"),
    (r"\bmv\b", "SEDANG", "Pindah/rename file (mv)",
     "Lokasi file berubah; bisa menimpa tujuan", "Pastikan tujuan tak menimpa berkas penting"),
    (r"\bgit\s+(checkout|reset|revert|rebase|merge)\b", "SEDANG", "Ubah state git working tree",
     "Branch/commit/working tree berubah", "Pastikan tak menimpa kerja belum tersimpan"),
    (r"\bcrontab\b", "SEDANG", "Ubah jadwal cron",
     "Tugas terjadwal berubah", "Tinjau 'crontab -l' dulu"),
    (r"\b(npm|yarn|pnpm)\b.*\b(run\s+build|build|ci|install)\b", "SEDANG",
     "Build/instalasi aset frontend", "node_modules/aset ditulis ulang; berat I/O & lama",
     "Pastikan disk & memori cukup"),
    (r"\bcomposer\b.*\b(install|update|require|remove)\b", "SEDANG", "Ubah dependensi PHP (composer)",
     "vendor/ berubah; composer.lock bisa berubah", "Pakai --no-dev di produksi"),
    (r"\brm\b", "SEDANG", "Hapus file (rm)",
     "File terhapus; tak masuk trash", "Cek nama file"),
    (r"\bsystemctl\b.*\breload\b|\bnginx\b.*-s\s+reload", "RENDAH", "Reload konfigurasi service",
     "Muat ulang config tanpa memutus koneksi (tanpa downtime)", "Uji config dulu (mis. nginx -t)"),
    (r"\b(mkdir|touch|ln|cp)\b", "RENDAH", "Buat/salin file atau folder",
     "Menambah berkas baru; cp bisa menimpa tujuan", "Pakai cp -n bila tak ingin menimpa"),
    (r"\bgit\s+(add|commit|stash|tag|fetch|pull|push)\b", "RENDAH", "Operasi git",
     "Stage/commit/fetch/push — umumnya reversibel", "—"),
]


def assess_command(command: str):
    """Nilai SELURUH perintah; kembalikan bagian PALING berisiko (tier tertinggi),
    agar kartu tak menyesatkan pada perintah majemuk (mis. SELECT && rm -rf)."""
    candidates = []
    db = _assess_db(command)
    if db:
        candidates.append(db)
    for pat, tier, aksi, efek, saran in _SHELL_RULES:
        if re.search(pat, command, re.I):
            candidates.append((tier, aksi, efek, saran))
    # redirect/tee ke berkas nyata (di luar string ber-quote) = operasi tulis
    cc = _strip_quotes(re.sub(r"2>&1", " ", re.sub(r"\d?>\s*/dev/null", " ", command)))
    if re.search(r"(^|[^0-9&])>>?\s*\S", cc) or re.search(r"\btee\b", cc):
        candidates.append(("RENDAH", "Tulis output ke berkas (redirect/tee)",
                           "Membuat/menimpa berkas tujuan dengan output perintah",
                           "> menimpa, >> menambah — pastikan berkas tujuan benar"))
    if candidates:
        return max(candidates, key=lambda x: TIER_ORDER[x[0]])
    return ("SEDANG", "Mengubah state (perintah tak terklasifikasi)",
            "Dampak pasti tak terbaca dari pola; diasumsikan menulis",
            "Tinjau perintah sebelum menyetujui")


def _get_mode() -> str:
    """Baca mode operasi ODIN dari file lokal atau env var."""
    try:
        with open(os.path.expanduser("~/.odin_mode")) as f:
            m = f.read().strip().lower()
            if m in ("setup", "deploy", "production"):
                return m
    except (FileNotFoundError, PermissionError, OSError):
        pass
    m = os.environ.get("ODIN_MODE", "").strip().lower()
    return m if m in ("setup", "deploy", "production") else "deploy"


def _shift_tier(tier: str) -> str:
    """Naikkan tier satu level (untuk mode production)."""
    order = {"AMAN": 1, "RENDAH": 2, "SEDANG": 3, "TINGGI": 4, "KRITIS": 4}
    reverse = {0: "AMAN", 1: "RENDAH", 2: "SEDANG", 3: "TINGGI", 4: "KRITIS"}
    return reverse.get(order.get(tier, 2), tier)


def risk_card(command: str, extra: str = "", mode: str = "deploy",
              cwd: str = "") -> str:
    tier, aksi, efek, saran = assess_command(command)
    if mode == "production" and tier != "KRITIS":
        tier = _shift_tier(tier)
    sep = "━" * 40
    lines = [sep,
             f"{TIER_ICON.get(tier, '')} RISIKO: {tier}",
             f"Cmd   : {command}"]
    if cwd:
        lines.append(f"Dir   : {cwd}")
    lines += [f"Aksi  : {aksi}",
              f"Efek  : {efek}",
              f"Saran : {saran}"]
    if mode == "production":
        lines.append("⚠️  Mode PRODUCTION — tier dinaikkan 1 level.")
    if tier == "KRITIS":
        lines.append("⛔ Server akan MENOLAK kecuali allow_dangerous=True (rem darurat). "
                     "Setujui hanya bila benar-benar disengaja.")
    if extra:
        lines.append(extra)
    lines.append(sep)
    return "\n".join(lines)


_SVC_RISK = {
    "reload":  ("RENDAH", "Reload config tanpa memutus koneksi (tanpa downtime)", "Uji config dulu bila ada"),
    "start":   ("RENDAH", "Memulai service", "—"),
    "restart": ("SEDANG", "Service mati lalu start ulang → request sesaat gagal (~1-2s)", "Lakukan saat trafik rendah"),
    "stop":    ("TINGGI", "Service BERHENTI → tetap DOWN sampai di-start lagi", "Pastikan memang ingin mematikan layanan"),
}


def service_card(service: str, action: str, mode: str = "deploy") -> str:
    tier, efek, saran = _SVC_RISK.get(action, ("SEDANG", "Mengubah state service", "Tinjau dampak"))
    if mode == "production" and tier != "KRITIS":
        tier = _shift_tier(tier)
    sep = "━" * 40
    lines = [sep,
             f"{TIER_ICON.get(tier, '')} RISIKO: {tier}",
             f"Cmd   : systemctl {action} {service}",
             f"Aksi  : {efek}",
             f"Saran : {saran}"]
    if mode == "production":
        lines.append("⚠️  Mode PRODUCTION — tier dinaikkan 1 level.")
    lines.append(sep)
    return "\n".join(lines)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get("tool_name", "") or ""
    ti = data.get("tool_input", {}) or {}
    mode = _get_mode()

    if tool.endswith("inspect_server"):
        emit("allow", "🟢 AMAN — inspect_server hanya membaca state server (read-only).")

    if tool.endswith("run_command"):
        cmd = ti.get("command", "") or ""
        cwd = ti.get("cwd", "") or ""
        if ti.get("allow_dangerous"):
            emit("ask", risk_card(cmd, "Flag allow_dangerous=True aktif — rem darurat dilepas. "
                                       "Konfirmasi manual wajib.", mode, cwd))
        if classify_command(cmd) == "allow":
            emit("allow", "🟢 AMAN (read-only / inspeksi) — dijalankan otomatis.")
        emit("ask", risk_card(cmd, mode=mode, cwd=cwd))

    if tool.endswith("service_action"):
        action = ti.get("action", "status") or "status"
        service = ti.get("service", "") or "?"
        if action in ("status", "is-active", "is-enabled"):
            emit("allow", f"🟢 AMAN — service_action '{action}' read-only — dijalankan otomatis.")
        emit("ask", service_card(service, action, mode))

    if tool.endswith("laravel_deploy"):
        app = ti.get("app_path", "?")
        branch = ti.get("branch", "main")
        steps = ["git reset --hard (buang perubahan lokal)"]
        if ti.get("composer", True):
            steps.append("composer install --no-dev")
        if ti.get("migrate", True):
            steps.append("artisan migrate --force")
        steps.append("optimize + cache")
        if ti.get("npm_build"):
            steps.append("npm ci && npm run build")
        fpm = ti.get("fpm_service")
        if fpm:
            steps.append(f"reload {fpm}")
        sep = "━" * 40
        emit("ask", "\n".join([
            sep,
            "🟠 RISIKO: TINGGI",
            f"App   : {app}",
            f"Branch: {branch}",
            f"Aksi  : Deploy Laravel — {' → '.join(steps)}",
            "Efek  : Perubahan lokal server HILANG (git reset --hard); skema DB bisa berubah (migrate)",
            "Saran : Pastikan backup DB tersedia; deploy saat trafik rendah",
            sep,
        ]))

    if tool.endswith("run_tests"):
        emit("allow", "🟢 AMAN — run_tests hanya membaca & menjalankan test suite.")

    if tool.endswith("memory_write"):
        ns = ti.get("ns", "?")
        key = ti.get("key", "") or "(auto)"
        text = (ti.get("text") or "")[:100]
        sep = "━" * 40
        emit("ask", "\n".join([
            sep,
            "🟢 RISIKO: RENDAH",
            f"Aksi  : Simpan memory [{ns}] key={key}",
            "Efek  : Entry tersimpan/ditimpa; muncul di konteks sesi berikutnya",
            f"Isi   : {text}{'…' if len(ti.get('text', '')) > 100 else ''}",
            sep,
        ]))

    if tool.endswith("memory_forget"):
        rid = ti.get("id", "") or f"{ti.get('ns', '')}:{ti.get('key', '')}"
        sep = "━" * 40
        emit("ask", "\n".join([
            sep,
            "🟢 RISIKO: RENDAH",
            f"Aksi  : Hapus memory [{rid}]",
            "Efek  : Entry dihapus (logis); tak muncul di sesi berikutnya",
            sep,
        ]))

    if tool.endswith("runbook"):
        rb_name = ti.get("name", "?")
        rb_steps = ti.get("steps") or []
        labels = [s.get("label", "?") for s in rb_steps[:10]]
        max_tier = "AMAN"
        has_write = False
        for s in rb_steps:
            cmd = s.get("command", "")
            if cmd and classify_command(cmd) != "allow":
                has_write = True
                tier = assess_command(cmd)[0]
                if TIER_ORDER.get(tier, 0) > TIER_ORDER.get(max_tier, 0):
                    max_tier = tier
        if not has_write:
            emit("allow", f"🟢 AMAN — Runbook '{rb_name}' hanya berisi langkah read-only.")
        sep = "━" * 40
        emit("ask", "\n".join([
            sep,
            f"{TIER_ICON.get(max_tier, '🟡')} RISIKO: {max_tier}",
            f"Aksi  : Runbook '{rb_name}' — {len(rb_steps)} langkah",
            f"Langkah: {' → '.join(labels)}{'…' if len(rb_steps) > 10 else ''}",
            "Efek  : Semua langkah dijalankan berurutan; berhenti pada kegagalan pertama",
            "Saran : Tinjau perintah setiap langkah; risiko = tier langkah paling berisiko",
            sep,
        ]))

    if tool.endswith("rollback_plan"):
        emit("allow", "🟢 AMAN — rollback_plan hanya membaca riwayat sesi (read-only).")

    sys.exit(0)  # tool lain: tanpa pendapat


def _check_update_bg() -> None:
    """Jalankan update checker di background (fire-and-forget, tak blokir guard)."""
    checker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_checker.py")
    if not os.path.isfile(checker):
        return
    try:
        subprocess.Popen(
            [sys.executable, checker],
            stdout=subprocess.DEVNULL, stderr=open(os.devnull, "w"),
            start_new_session=True,
        )
    except OSError:
        pass


if __name__ == "__main__":
    _check_update_bg()
    main()
