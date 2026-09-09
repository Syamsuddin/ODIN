#!/usr/bin/env bash
# ODIN v2.4 — Multi-project MCP launcher + kanal kontrol sempit
# Usage: run.sh [--project <name>]
#        run.sh --diagnose
#        run.sh --provision <name> --root <path>
#
# Mode:
#   --project <name>  →  source projects/<name>.conf, memory di memory/<name>/
#   (tanpa flag)      →  backward-compatible: single .conf atau env vars lama
#   --diagnose        →  cetak laporan keadaan server, lalu keluar (TIDAK exec agent)
#   --provision       →  buat projects/<name>.conf + memory/<name>/, lalu keluar
#
# KENAPA ADA --diagnose/--provision (K4):
#   Kunci SSH ODIN dipasang dengan forced-command (odin-dispatch.sh), jadi sesi
#   ber-kunci itu TIDAK punya shell: `ssh.run("test -f ...")` tak pernah jalan.
#   Dulu CLI tetap mengirim perintah shell lewat kunci itu — hasilnya diagnostik
#   melapor palsu dan `project add` "sukses" tanpa pernah menulis apa pun.
#   Dua mode di bawah adalah kanal resmi & tervalidasi untuk dua kebutuhan itu,
#   sehingga alur bebas-password tetap utuh tanpa membuka shell.

ODIN_HOME="$(cd "$(dirname "$0")" && pwd)"
PROJECTS_DIR="$ODIN_HOME/projects"
RUN_SH_VERSION="2.4.0"

_fatal() { echo "FATAL: $*" >&2; exit 1; }

_valid_name() {
    [[ -n "$1" && ! "$1" =~ [^A-Za-z0-9._-] && "$1" != "." && "$1" != ".." ]]
}

# Path remote: absolut, tanpa '..', tanpa metakarakter. Sama ketatnya dengan
# _validate_remote_root() di sisi laptop — validasi sisi server tetap wajib
# karena sisi laptop tidak pernah jadi batas keamanan.
_valid_root() {
    [[ "$1" == /* && ! "$1" =~ [^A-Za-z0-9._/-] && "$1" != *".."* ]]
}

# --- Parse argumen ---
PROJECT=""
MODE="serve"
PROV_NAME=""
PROV_ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT="${2:-}"
      _valid_name "$PROJECT" || _fatal "nama project tidak valid: '${PROJECT}'"
      shift 2 ;;
    --diagnose)
      MODE="diagnose"
      shift ;;
    --provision)
      MODE="provision"
      PROV_NAME="${2:-}"
      _valid_name "$PROV_NAME" || _fatal "nama project tidak valid: '${PROV_NAME}'"
      shift 2 ;;
    --root)
      PROV_ROOT="${2:-}"
      _valid_root "$PROV_ROOT" || _fatal "path root tidak valid: '${PROV_ROOT}'"
      shift 2 ;;
    *)
      _fatal "argumen tak dikenal: '$1' (hanya --project/--diagnose/--provision/--root)" ;;
  esac
done

_machine_id() {
    cat /etc/machine-id 2>/dev/null || cat /var/lib/dbus/machine-id 2>/dev/null || echo ''
}

# ── Mode: --diagnose ────────────────────────────────────────────────────────
# Format baris-per-fakta (bukan JSON) supaya aman diproduksi bash tanpa escaping,
# dan tetap gampang di-parse. Bagian yang TIDAK terbaca ditandai eksplisit
# @@UNREADABLE@@ / @@ABSENT@@ — sisi laptop wajib membedakan "aman" dari "buta".
odin_diagnose() {
    local venv_py="$ODIN_HOME/.venv/bin/python"
    local flag

    echo "@@ODIN-DIAGNOSE@@ v=1"
    printf 'run_sh_version=%s\n' "$RUN_SH_VERSION"
    printf 'machine_id=%s\n' "$(_machine_id)"

    for spec in "agent_file:-f:$ODIN_HOME/odin_agent.py" \
                "run_sh_exec:-x:$ODIN_HOME/run.sh" \
                "dispatch_exec:-x:$ODIN_HOME/odin-dispatch.sh" \
                "venv_python:-x:$venv_py" \
                "projects_dir:-d:$PROJECTS_DIR" \
                "memory_dir:-d:$ODIN_HOME/memory"; do
        local key="${spec%%:*}"; local rest="${spec#*:}"
        local test_op="${rest%%:*}"; local target="${rest#*:}"
        if test "$test_op" "$target"; then flag=1; else flag=0; fi
        printf '%s=%s\n' "$key" "$flag"
    done

    printf 'agent_version=%s\n' \
        "$(grep -m1 '^__version__' "$ODIN_HOME/odin_agent.py" 2>/dev/null | cut -d'"' -f2)"

    if [[ -x "$venv_py" ]] && "$venv_py" -c 'from mcp.server.fastmcp import FastMCP' 2>/dev/null; then
        echo "mcp_module=1"
    else
        echo "mcp_module=0"
    fi

    printf 'disk=%s\n' "$(df -h / 2>/dev/null | tail -1 | awk '{print $5" "$4}')"
    printf 'mem=%s\n'  "$(free -h 2>/dev/null | awk '/^Mem/{print $3"/"$2}')"

    local c n m
    for c in "$PROJECTS_DIR"/*.conf; do
        [[ -e "$c" ]] || continue
        n="$(basename "$c" .conf)"
        if [[ -d "$ODIN_HOME/memory/$n" ]]; then m=1; else m=0; fi
        printf 'project=%s memory=%s\n' "$n" "$m"
    done

    # Sudoers: butuh root. Rule NOPASSWD berargumen TERTUTUP `cat /etc/sudoers.d/odin`
    # (tanpa wildcard → tak bisa dipakai baca file lain) dipasang oleh `server add`.
    # Server lama tanpa rule itu akan jatuh ke @@UNREADABLE@@ — bukan ke "aman".
    echo "@@SECTION:sudoers@@"
    local s_out s_rc
    s_out="$(sudo -n cat /etc/sudoers.d/odin 2>&1)"; s_rc=$?
    if [[ $s_rc -eq 0 ]]; then
        printf '%s\n' "$s_out"
    elif [[ "$s_out" == *"No such file"* ]]; then
        echo "@@ABSENT@@"
    else
        echo "@@UNREADABLE@@"
    fi

    echo "@@SECTION:authorized_keys@@"
    local ak="$HOME/.ssh/authorized_keys"
    if [[ -r "$ak" ]]; then
        cat "$ak"
    elif [[ -e "$ak" ]]; then
        echo "@@UNREADABLE@@"
    else
        echo "@@ABSENT@@"
    fi

    echo "@@END@@"
}

# ── Mode: --provision ───────────────────────────────────────────────────────
# Menulis conf project + memory dir. Sengaja MENOLAK menimpa conf yang sudah ada:
# menimpa berarti membajak PROJECT_ROOT project lain yang sudah jalan.
odin_provision() {
    [[ -n "$PROV_NAME" ]] || _fatal "--provision butuh nama project"
    [[ -n "$PROV_ROOT" ]] || _fatal "--provision butuh --root <path>"

    mkdir -p "$PROJECTS_DIR" || _fatal "tak bisa membuat $PROJECTS_DIR"
    chmod 700 "$PROJECTS_DIR" 2>/dev/null

    local conf="$PROJECTS_DIR/$PROV_NAME.conf"
    [[ -e "$conf" ]] && _fatal "project '$PROV_NAME' sudah ada di server ($conf)"

    umask 077
    {
        printf 'PROJECT_NAME=%s\n' "$PROV_NAME"
        printf 'PROJECT_ROOT=%s\n' "$PROV_ROOT"
        printf 'ALLOWED_LOG_DIRS=/var/log,%s\n' "$PROV_ROOT"
    } > "$conf" || _fatal "gagal menulis $conf"
    chmod 600 "$conf" || _fatal "gagal chmod $conf"

    local mem="$ODIN_HOME/memory/$PROV_NAME"
    mkdir -p "$mem" || _fatal "gagal membuat $mem"
    chmod 700 "$mem" || _fatal "gagal chmod $mem"

    echo "@@ODIN-PROVISION@@ ok"
    printf 'conf=%s\n' "$conf"
    printf 'memory=%s\n' "$mem"
    if [[ -d "$PROV_ROOT" ]]; then
        echo "root_exists=1"
    else
        echo "root_exists=0"
    fi
    exit 0
}

case "$MODE" in
    diagnose)  odin_diagnose; exit 0 ;;
    provision) odin_provision ;;
esac

# --- Resolve config ---
if [[ -n "$PROJECT" ]]; then
    CONF="$PROJECTS_DIR/$PROJECT.conf"
    if [[ ! -f "$CONF" ]]; then
        echo "FATAL: project '$PROJECT' tidak ditemukan ($CONF)" >&2
        if [[ -d "$PROJECTS_DIR" ]]; then
            echo "  Projects tersedia:" >&2
            ls "$PROJECTS_DIR"/*.conf 2>/dev/null | xargs -I{} basename {} .conf | sed 's/^/    /' >&2
        fi
        exit 1
    fi
    source "$CONF"
    export MEMORY_DIR="${MEMORY_DIR:-$ODIN_HOME/memory/$PROJECT}"
else
    CONF_COUNT=0
    if [[ -d "$PROJECTS_DIR" ]]; then
        CONF_COUNT=$(find "$PROJECTS_DIR" -maxdepth 1 -name "*.conf" 2>/dev/null | wc -l | tr -d ' ')
    fi
    if [[ "$CONF_COUNT" -eq 1 ]]; then
        source "$PROJECTS_DIR"/*.conf
        PROJECT="${PROJECT_NAME:-legacy}"
        export MEMORY_DIR="${MEMORY_DIR:-$ODIN_HOME/memory/$PROJECT}"
    elif [[ "$CONF_COUNT" -gt 1 ]]; then
        # >=2 project terdaftar tapi tak ada --project: dulu diam-diam jatuh ke
        # /var/www/html (project SALAH, memory SALAH). Sekarang gagal keras.
        echo "FATAL: ada $CONF_COUNT project di $PROJECTS_DIR — wajib pakai --project <nama>." >&2
        echo "  Projects tersedia:" >&2
        ls "$PROJECTS_DIR"/*.conf 2>/dev/null | xargs -I{} basename {} .conf | sed 's/^/    /' >&2
        exit 1
    else
        # Jalur legacy v1.x murni (belum ada projects/*.conf sama sekali).
        : "${PROJECT_ROOT:=/var/www/html}"
        : "${ALLOWED_LOG_DIRS:=/var/log}"
        : "${MEMORY_DIR:=$ODIN_HOME/memory}"
    fi
fi

export DEPLOY_MODE="${DEPLOY_MODE:-local}"
export PROJECT_NAME="${PROJECT_NAME:-}"
export PROJECT_ROOT
export ALLOWED_LOG_DIRS
export MEMORY_DIR
export GLOBAL_MEMORY_DIR="${GLOBAL_MEMORY_DIR:-$ODIN_HOME/memory/_cortex}"

# --- P1: bind conf ke IDENTITAS server (anti mis-route) ---
# machine-id = ID unik & stabil per mesin Linux (tak berubah saat hostname/IP ganti).
# Pertama kali (SERVER_ID belum ada di conf) → auto-seed ke conf. Run berikutnya,
# odin_agent.py membandingkan SERVER_ID vs machine-id aktual & menolak bila beda —
# sehingga koneksi MCP yang nyasar ke server salah GAGAL LOUD, bukan diam-diam.
_MACHINE_ID="$(_machine_id)"
if [[ -n "$_MACHINE_ID" && -z "${SERVER_ID:-}" && -n "${CONF:-}" && -f "${CONF:-}" ]]; then
    printf '\n# auto-seed identitas server (P1)\nSERVER_ID=%s\n' "$_MACHINE_ID" >> "$CONF"
    SERVER_ID="$_MACHINE_ID"
    echo "INFO: SERVER_ID di-seed ke $CONF ($_MACHINE_ID)" >&2
fi
export SERVER_ID="${SERVER_ID:-}"
export ODIN_MACHINE_ID="$_MACHINE_ID"

# ODIN dijalankan dari ODIN_HOME, BUKAN dari app dir. Alasan (isolasi konteks P0):
#   - Proses ODIN tak boleh mewarisi konteks project. FastMCP/pydantic-settings
#     otomatis membaca ./.env dari CWD; app dir (mis. /var/www/<proj>) kerap punya
#     .env milik www-data (mode 640) yang tak terbaca user odin → crash. Idem untuk
#     .git/composer.json/vendor yang bisa salah ke-pickup.
#   - Menghapus chicken-and-egg: PROJECT_ROOT tak harus sudah ada saat fase SETUP LEMP.
# PROJECT_ROOT tetap di-export sebagai PARAMETER; run_command yang men-`cd` ke sana
# per-perintah (lihat _resolve_default_cwd di odin_agent.py).
cd "$ODIN_HOME" || { echo "FATAL: ODIN_HOME '$ODIN_HOME' tidak bisa diakses" >&2; exit 1; }
exec "$ODIN_HOME/.venv/bin/python" "$ODIN_HOME/odin_agent.py"
