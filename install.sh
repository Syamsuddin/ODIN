#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Installer — macOS & Linux
# Usage: curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.sh | bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

REPO="Syamsuddin/ODIN"
REPO_URL="https://github.com/${REPO}.git"
INSTALL_DIR="${ODIN_INSTALL_DIR:-$HOME/.odin}"
BIN_LINK="/usr/local/bin/odin-update"
BRANCH="main"
MIN_PYTHON="3.10"

# ── TTY untuk input interaktif (penting saat curl | bash) ──────────────────
if [ -t 0 ]; then
    TTY_FD=0
else
    exec 3</dev/tty 2>/dev/null || { echo "ERROR: Tidak bisa membuka /dev/tty untuk input interaktif." >&2; exit 1; }
    TTY_FD=3
fi

# ── Warna & simbol ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()   { printf "${RED}✗${NC} %s\n" "$*" >&2; }
fatal() { err "$*"; exit 1; }

banner() {
    printf "\n${BOLD}${CYAN}"
    cat <<'ART'
    ╔══════════════════════════════════════════════════════════╗
    ║                                                          ║
    ║               ⚡  O D I N  Installer  ⚡                ║
    ║               MCP Agent AI for Claude Code               ║
    ║                                                          ║
    ║  created by @syams_ideris (syamsuddin.ideris@gmail.com)  ║
    ║                                                          ║
    ╚══════════════════════════════════════════════════════════╝
ART
    printf "${NC}\n"
}

# ── Deteksi OS ──────────────────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Darwin) OS="macos" ;;
        Linux)  OS="linux" ;;
        *)      fatal "OS tidak didukung: $(uname -s). Gunakan macOS atau Linux." ;;
    esac
}

# ── Cek prasyarat ───────────────────────────────────────────────────────────
check_command() {
    command -v "$1" >/dev/null 2>&1 || fatal "'$1' tidak ditemukan. Install terlebih dahulu."
}

check_python() {
    local py=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            py="$candidate"
            break
        fi
    done
    [ -z "$py" ] && fatal "Python 3 tidak ditemukan. Install Python >= ${MIN_PYTHON}."

    local ver
    ver=$($py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    if $py -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
        ok "Python $ver ditemukan ($py)"
        PYTHON="$py"
    else
        fatal "Python >= ${MIN_PYTHON} diperlukan (ditemukan: $ver)."
    fi
}

check_prereqs() {
    info "Memeriksa prasyarat..."
    check_command git
    check_python

    if [ "$OS" = "macos" ]; then
        check_command ssh
    fi
    ok "Semua prasyarat terpenuhi"
}

# ── Install / Update ────────────────────────────────────────────────────────
install_odin() {
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Instalasi ODIN sudah ada di $INSTALL_DIR — memperbarui..."
        if ! git -C "$INSTALL_DIR" fetch origin "$BRANCH" --quiet 2>&1; then
            fatal "Gagal fetch dari GitHub. Cek koneksi internet."
        fi
        git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" --quiet
        ok "ODIN diperbarui ke versi terbaru"
    else
        info "Mengunduh ODIN dari GitHub..."
        if [ -d "$INSTALL_DIR" ]; then
            warn "$INSTALL_DIR sudah ada (bukan git repo) — backup & replace"
            mv "$INSTALL_DIR" "${INSTALL_DIR}.bak.$(date +%s)"
        fi
        if ! git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" --quiet 2>&1; then
            fatal "Gagal clone dari GitHub. Cek koneksi internet."
        fi
        ok "ODIN berhasil diunduh"
    fi
}

setup_venv() {
    local venv_dir="$INSTALL_DIR/.venv"
    if [ -d "$venv_dir" ] && "$venv_dir/bin/python" -c "import mcp" 2>/dev/null; then
        ok "Virtual environment sudah ada & valid"
        return
    fi
    info "Membuat virtual environment..."
    if ! "$PYTHON" -m venv "$venv_dir"; then
        fatal "Gagal membuat venv. Pastikan python3-venv terinstall."
    fi
    info "Menginstall dependensi (mcp[cli])..."
    if ! "$venv_dir/bin/pip" install --upgrade pip >/dev/null 2>&1; then
        warn "Gagal upgrade pip — melanjutkan..."
    fi
    if "$venv_dir/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"; then
        ok "Dependensi terinstall (mcp[cli])"
    else
        fatal "Gagal install mcp[cli]. Cek koneksi internet."
    fi
}

install_guard_hook() {
    local hooks_dir="$INSTALL_DIR/client"
    local guard="$hooks_dir/odin_guard.py"

    if [ ! -f "$guard" ]; then
        fatal "Guard tidak ditemukan di $guard"
    fi
    chmod +x "$guard"
    ok "Guard hook siap: $guard"
}

# ── Symlink update command ──────────────────────────────────────────────────
install_updater() {
    local updater="$INSTALL_DIR/odin-update.sh"
    cat > "$updater" <<'UPDATER'
#!/usr/bin/env bash
set -euo pipefail
ODIN_DIR="$(cd "$(dirname "$0")" && pwd)"
printf '\033[0;34m▸\033[0m Memeriksa update ODIN...\n'
cd "$ODIN_DIR"
git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" = "$REMOTE" ]; then
    printf '\033[0;32m✓\033[0m ODIN sudah versi terbaru (%s)\n' "$(git describe --tags 2>/dev/null || echo "$LOCAL" | cut -c1-7)"
    exit 0
fi
printf '\033[1;33m⚠\033[0m Update tersedia! Memperbarui...\n'
git reset --hard origin/main --quiet
if [ -d ".venv" ]; then
    .venv/bin/pip install --quiet -r requirements.txt
fi
NEW_VER=$(grep -m1 '__version__' server/odin_agent.py | cut -d'"' -f2)
printf '\033[0;32m✓\033[0m ODIN diperbarui ke v%s\n' "$NEW_VER"
UPDATER
    chmod +x "$updater"

    if [ -w "$(dirname "$BIN_LINK")" ] 2>/dev/null; then
        ln -sf "$updater" "$BIN_LINK" 2>/dev/null && \
            ok "Perintah 'odin-update' tersedia di PATH" || true
    else
        if command -v sudo >/dev/null 2>&1; then
            info "Perlu sudo untuk symlink ke $BIN_LINK"
            sudo ln -sf "$updater" "$BIN_LINK" 2>/dev/null && \
                ok "Perintah 'odin-update' tersedia di PATH" || \
                warn "Gagal buat symlink — jalankan manual: $updater"
        else
            warn "Tidak bisa buat symlink — jalankan manual: $updater"
        fi
    fi
}

# ── Wizard helpers ──────────────────────────────────────────────────────────
ask_input() {
    local prompt_text="$1" default_val="$2" result=""
    while true; do
        if [ -n "$default_val" ]; then
            printf "${BOLD}  %s${NC} [Enter = ${CYAN}%s${NC}]: " "$prompt_text" "$default_val" >/dev/tty
        else
            printf "${BOLD}  %s${NC} (wajib diisi): " "$prompt_text" >/dev/tty
        fi
        read -r result <&"$TTY_FD"
        result="${result:-$default_val}"
        if [ -n "$result" ]; then
            echo "$result"
            return
        fi
        printf "${YELLOW}⚠${NC} Nilai tidak boleh kosong.\n" >/dev/tty
    done
}

ask_choice() {
    local prompt_text="$1" max="$2" default="$3" choice=""
    while true; do
        printf "${BOLD}  %s${NC} [Enter = ${CYAN}%s${NC}]: " "$prompt_text" "$default" >/dev/tty
        read -r choice <&"$TTY_FD"
        choice="${choice:-$default}"
        if [[ "$choice" =~ ^[1-9][0-9]*$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "$max" ]; then
            echo "$choice"
            return
        fi
        printf "${YELLOW}⚠${NC} Pilih 1-%s.\n" "$max" >/dev/tty
    done
}

# Deteksi apakah input adalah SSH alias dari ~/.ssh/config
is_ssh_alias() {
    local host="$1"
    [ -f "$HOME/.ssh/config" ] || return 1
    awk -v h="$host" '
        /^[[:space:]]*[Hh]ost[[:space:]]/ {
            for (i=2; i<=NF; i++) if ($i == h) { found=1; exit }
        }
        END { exit !found }
    ' "$HOME/.ssh/config" 2>/dev/null
}

test_ssh() {
    local host="$1" run_path="$2" port="${3:-}" user="${4:-}"
    local ssh_args=(-o ConnectTimeout=5 -o BatchMode=yes)
    [ -n "$port" ] && [ "$port" != "22" ] && ssh_args+=(-p "$port")
    local target="$host"
    [ -n "$user" ] && target="${user}@${host}"

    printf "│  ${BLUE}▸${NC} Test SSH ke ${CYAN}%s${NC}" "$target"
    [ -n "$port" ] && [ "$port" != "22" ] && printf " port ${CYAN}%s${NC}" "$port"
    printf "... "

    local ssh_err
    ssh_err=$(ssh "${ssh_args[@]}" "$target" "echo ok" 2>&1)
    local ssh_rc=$?

    if [ $ssh_rc -eq 0 ]; then
        printf "${GREEN}✓ terhubung${NC}\n"
        if ssh "${ssh_args[@]}" "$target" "test -f '$run_path'" 2>/dev/null; then
            printf "│  ${GREEN}✓${NC} File ${CYAN}%s${NC} ditemukan di server\n" "$run_path"
        else
            printf "│  ${YELLOW}⚠${NC} File %s belum ada (akan dibuat saat setup server)\n" "$run_path"
        fi
        return 0
    else
        printf "${YELLOW}⚠ gagal${NC}\n"
        # Diagnosa spesifik berdasarkan error message
        if echo "$ssh_err" | grep -qi "permission denied"; then
            printf "│  ${YELLOW}⚠${NC} Server merespons, tapi login ditolak.\n"
            printf "│    → Port ${CYAN}%s${NC} benar, tapi user ${CYAN}%s${NC} belum bisa login.\n" "${port:-22}" "$target"
            printf "│    → Password/SSH key perlu di-set (akan diatur di Setup Server).\n"
            printf "│  ${BLUE}ℹ${NC}  Ini normal jika user odin belum di-setup — wizard tetap lanjut.\n"
        elif echo "$ssh_err" | grep -qi "timed out\|no route\|network is unreachable"; then
            printf "│  ${RED}✗${NC} Server tidak bisa dihubungi.\n"
            printf "│    → Periksa: IP/hostname benar? Port SSH benar? Server online?\n"
            printf "│    → Jika port SSH bukan %s, ubah di input sebelumnya.\n" "${port:-22}"
        elif echo "$ssh_err" | grep -qi "refused"; then
            printf "│  ${RED}✗${NC} Koneksi ditolak (port ${CYAN}%s${NC} tidak menerima SSH).\n" "${port:-22}"
            printf "│    → Kemungkinan port SSH berbeda. Cek: ${CYAN}ssh -p PORT %s${NC}\n" "$target"
        else
            printf "│  ${YELLOW}⚠${NC} Koneksi gagal: %s\n" "${ssh_err:0:120}"
        fi
        printf "│  ${BLUE}ℹ${NC}  Wizard tetap lanjut — perbaiki SSH sebelum pakai ODIN.\n"
        return 1
    fi
}

# ── Detect existing config ─────────────────────────────────────────────────
detect_existing_config() {
    EXISTING_SSH_HOST=""
    EXISTING_RUN_PATH=""
    EXISTING_MCP_NAME=""
    EXISTING_SETTINGS_PATH=""

    local claude_json="$HOME/.claude.json"
    if [ ! -f "$claude_json" ]; then
        return 1
    fi

    local detect_result
    detect_result=$("$PYTHON" -c "
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    mcp = d.get('mcpServers', {})
    entry = mcp.get('odin')
    if entry and entry.get('args'):
        args = entry['args']
        # run_path is always the last arg
        path = args[-1] if args else ''
        # SSH target is the second-to-last arg (host or user@host)
        # Skip flags: -p, -o, and their values
        host = args[-2] if len(args) >= 2 else args[0] if args else ''
        print(f'odin|{host}|{path}')
        sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" "$claude_json" 2>/dev/null) || return 1

    EXISTING_MCP_NAME="${detect_result%%|*}"
    local rest="${detect_result#*|}"
    EXISTING_SSH_HOST="${rest%%|*}"
    EXISTING_RUN_PATH="${rest#*|}"
    return 0
}

# ── Write MCP config ───────────────────────────────────────────────────────
write_mcp_config() {
    local ssh_target="$1" run_path="$2" ssh_port="${3:-22}" is_alias="${4:-false}"
    local claude_json="$HOME/.claude.json"

    if [ -f "$claude_json" ]; then
        cp "$claude_json" "${claude_json}.bak"
    fi

    "$PYTHON" -c "
import json, os, sys

claude_json = os.path.expanduser('~/.claude.json')
try:
    with open(claude_json) as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}

mcp = data.setdefault('mcpServers', {})

ssh_target = sys.argv[1]
run_path = sys.argv[2]
ssh_port = sys.argv[3]
is_alias = sys.argv[4] == 'true'

args = []
if is_alias:
    args = [ssh_target, run_path]
else:
    if ssh_port != '22':
        args += ['-p', ssh_port]
    args += ['-o', 'StrictHostKeyChecking=accept-new']
    args += [ssh_target, run_path]

mcp['odin'] = {
    'type': 'stdio',
    'command': 'ssh',
    'args': args
}

with open(claude_json, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
" "$ssh_target" "$run_path" "$ssh_port" "$is_alias"
}

# ── Write guard config ─────────────────────────────────────────────────────
write_guard_config() {
    local settings_path="$1" guard_path="$2"

    local settings_dir
    settings_dir="$(dirname "$settings_path")"
    mkdir -p "$settings_dir"

    if [ -f "$settings_path" ]; then
        cp "$settings_path" "${settings_path}.bak"
    fi

    "$PYTHON" -c "
import json, os, sys

settings_path = sys.argv[1]
guard_path = sys.argv[2]

ODIN_ALLOW = [
    'mcp__odin__server_info',
    'mcp__odin__tail_log',
    'mcp__odin__http_health_check',
    'mcp__odin__memory_recall',
    'mcp__odin__memory_digest',
    'mcp__odin__session_history',
    'mcp__odin__rollback_plan',
    'mcp__odin__inspect_server',
]

ODIN_HOOK = {
    'matcher': 'mcp__odin__(run_command|service_action|laravel_deploy|run_tests|runbook|inspect_server|memory_write|memory_forget)',
    'hooks': [{
        'type': 'command',
        'command': 'python3 ' + repr(guard_path),
        'timeout': 10
    }]
}

try:
    with open(settings_path) as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}

# Merge permissions.allow (deduplicate)
perms = data.setdefault('permissions', {})
allow = perms.setdefault('allow', [])
for tool in ODIN_ALLOW:
    if tool not in allow:
        allow.append(tool)

# Replace/add odin hook in PreToolUse
hooks = data.setdefault('hooks', {})
pre = hooks.setdefault('PreToolUse', [])

pre[:] = [h for h in pre if not (isinstance(h, dict) and 'mcp__odin__' in h.get('matcher', ''))]
pre.append(ODIN_HOOK)

# PostToolUse: auto-sync mode dari inspect_server
POST_HOOK = {
    'matcher': 'mcp__odin__inspect_server',
    'hooks': [{
        'type': 'command',
        'command': 'python3 ' + repr(guard_path),
        'timeout': 10
    }]
}
post = hooks.setdefault('PostToolUse', [])
post[:] = [h for h in post if not (isinstance(h, dict) and 'mcp__odin__' in h.get('matcher', ''))]
post.append(POST_HOOK)

with open(settings_path, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
" "$settings_path" "$guard_path"
}

# ── Setup Wizard ───────────────────────────────────────────────────────────
# Variabel global untuk sharing antara wizard dan server setup
WIZ_SSH_HOST=""
WIZ_SSH_PORT=""
WIZ_SSH_USER=""
WIZ_SSH_IS_ALIAS=false

setup_wizard() {
    local ssh_host="" ssh_port="22" ssh_user="odin" run_path="" guard_scope=""
    local project_path="" settings_path="" use_alias=false
    local guard_path="$INSTALL_DIR/client/odin_guard.py"
    local wizard_skipped=false

    printf "\n${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${CYAN}  ⚙  Setup Wizard — Konfigurasi Claude Code${NC}\n"
    printf "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"

    printf "  Wizard ini mengkonfigurasi laptop agar Claude Code bisa\n"
    printf "  terhubung ke ODIN di server Anda via SSH.\n\n"
    printf "  ${BLUE}ℹ${NC}  Tekan Enter untuk memakai nilai default ${CYAN}[dalam kurung]${NC}.\n\n"

    # ── Detect existing ────────────────────────────────────────────────
    if detect_existing_config; then
        printf "  ${GREEN}✓${NC} Konfigurasi ODIN sudah ada di laptop ini:\n"
        printf "      SSH target : ${CYAN}%s${NC}\n" "$EXISTING_SSH_HOST"
        printf "      Path       : ${CYAN}%s${NC}\n\n" "$EXISTING_RUN_PATH"
        printf "${BOLD}  Konfigurasi ulang? [y/N] (Enter = tidak)${NC}: "
        read -r reconfig <&"$TTY_FD"
        if [[ ! "$reconfig" =~ ^[Yy]$ ]]; then
            info "Config tidak diubah."
            wizard_skipped=true
            WIZARD_DONE=false
            return
        fi
        printf "\n"
        ssh_host="$EXISTING_SSH_HOST"
        run_path="$EXISTING_RUN_PATH"
    fi

    # ── Koneksi Server ─────────────────────────────────────────────────
    printf "┌─ ${BOLD}LANGKAH 1: Koneksi ke Server${NC} ──────────────────────\n│\n"
    printf "│  ODIN berjalan di server (VPS). Claude Code di laptop\n"
    printf "│  terhubung ke sana lewat SSH.\n│\n"

    printf "│  Cara koneksi:\n"
    printf "│  ${CYAN}1)${NC} SSH alias  — pakai nama alias dari ~/.ssh/config\n"
    printf "│               (port, user, key sudah diatur di sana)\n"
    printf "│  ${CYAN}2)${NC} IP manual  — masukkan IP/hostname, port, dan user\n│\n"

    local conn_method
    conn_method=$(ask_choice "Pilihan" 2 "1")

    if [ "$conn_method" = "1" ]; then
        use_alias=true
        printf "│\n"
        printf "│  Masukkan nama alias SSH (contoh: ${CYAN}vps-odin${NC}, ${CYAN}my-server${NC})\n"
        printf "│  Alias harus sudah ada di ${CYAN}~/.ssh/config${NC}\n│\n"
        ssh_host=$(ask_input "SSH alias" "${ssh_host:-}")
        printf "│\n"
        if is_ssh_alias "$ssh_host"; then
            printf "│  ${GREEN}✓${NC} Alias ${CYAN}%s${NC} ditemukan di ~/.ssh/config\n" "$ssh_host"
        else
            printf "│  ${YELLOW}⚠${NC} Alias ${CYAN}%s${NC} tidak ditemukan di ~/.ssh/config\n" "$ssh_host"
            printf "│    Pastikan alias sudah diatur sebelum pakai ODIN.\n"
        fi
    else
        use_alias=false
        printf "│\n"
        printf "│  IP atau hostname server (contoh: ${CYAN}192.168.1.100${NC}, ${CYAN}server.com${NC})\n│\n"
        ssh_host=$(ask_input "Host/IP server" "${ssh_host:-}")
        printf "│\n"
        printf "│  Port SSH server (banyak server pakai port non-standard)\n│\n"
        ssh_port=$(ask_input "Port SSH" "22")
        printf "│\n"
        printf "│  User SSH untuk ODIN di server\n"
        printf "│  (user ini dibuat khusus untuk ODIN, bukan user admin)\n│\n"
        ssh_user=$(ask_input "User SSH" "odin")
    fi

    printf "│\n"
    printf "│  Path launcher ODIN (run.sh) di server\n│\n"
    run_path=$(ask_input "Path run.sh di server" "${run_path:-/home/odin/run.sh}")
    printf "│\n"

    # Test koneksi
    if [ "$use_alias" = true ]; then
        test_ssh "$ssh_host" "$run_path" "" "" || true
    else
        test_ssh "$ssh_host" "$run_path" "$ssh_port" "$ssh_user" || true
    fi
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # Simpan ke global untuk server setup
    WIZ_SSH_HOST="$ssh_host"
    WIZ_SSH_PORT="$ssh_port"
    WIZ_SSH_USER="$ssh_user"
    WIZ_SSH_IS_ALIAS="$use_alias"

    # ── Scope Guard ────────────────────────────────────────────────────
    printf "┌─ ${BOLD}LANGKAH 2: Proteksi Guard${NC} ─────────────────────────\n│\n"
    printf "│  Guard adalah lapisan keamanan yang menampilkan peringatan\n"
    printf "│  sebelum perintah berbahaya dijalankan di server.\n│\n"
    printf "│  ${CYAN}1)${NC} Global (disarankan)  — guard aktif di semua project\n"
    printf "│     Artinya: kapanpun Claude Code dipakai, perintah ke\n"
    printf "│     server selalu melalui guard. Paling aman.\n│\n"
    printf "│  ${CYAN}2)${NC} Per-project           — guard hanya aktif di satu\n"
    printf "│     direktori project tertentu.\n"
    printf "│     ${YELLOW}⚠ Peringatan: di project lain ODIN tetap bisa diakses${NC}\n"
    printf "│     ${YELLOW}  tapi TANPA proteksi guard.${NC}\n│\n"
    guard_scope=$(ask_choice "Pilihan" 2 "1")

    if [ "$guard_scope" = "2" ]; then
        printf "│\n"
        printf "│  Masukkan path direktori project yang ingin diproteksi.\n"
        printf "│  Contoh: ${CYAN}/Users/anda/Projects/webapp${NC}\n│\n"
        while true; do
            project_path=$(ask_input "Path project" "")
            if [ -d "$project_path" ]; then
                printf "│  ${GREEN}✓${NC} Direktori ${CYAN}%s${NC} valid\n" "$project_path"
                break
            fi
            printf "${YELLOW}⚠${NC} Direktori '%s' tidak ditemukan. Coba lagi.\n" "$project_path" >/dev/tty
        done
        settings_path="${project_path}/.claude/settings.json"
    else
        settings_path="$HOME/.claude/settings.json"
    fi
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Konfirmasi ─────────────────────────────────────────────────────
    printf "┌─ ${BOLD}LANGKAH 3: Konfirmasi${NC} ─────────────────────────────\n│\n"
    printf "│  ${BOLD}Ringkasan konfigurasi:${NC}\n│\n"
    if [ "$use_alias" = true ]; then
        printf "│  SSH koneksi    : alias ${CYAN}%s${NC}\n" "$ssh_host"
    else
        printf "│  SSH koneksi    : ${CYAN}%s@%s${NC} port ${CYAN}%s${NC}\n" "$ssh_user" "$ssh_host" "$ssh_port"
    fi
    printf "│  Path run.sh    : ${CYAN}%s${NC}\n" "$run_path"
    if [ "$guard_scope" = "2" ]; then
        printf "│  Guard scope    : ${CYAN}Project${NC} → %s\n" "$project_path"
    else
        printf "│  Guard scope    : ${CYAN}Global${NC} (semua project)\n"
    fi
    printf "│\n"
    printf "│  File yang akan ditulis (backup .bak dibuat otomatis):\n"
    printf "│    • ${CYAN}~/.claude.json${NC}  → koneksi MCP ke server\n"
    printf "│    • ${CYAN}%s${NC}  → guard hook\n" "$settings_path"
    printf "│\n"
    printf "${BOLD}│  Tulis konfigurasi? [Y/n] (Enter = ya)${NC}: "
    read -r confirm <&"$TTY_FD"
    if [[ "$confirm" =~ ^[Nn]$ ]]; then
        info "Dibatalkan — konfigurasi tidak ditulis."
        WIZARD_DONE=false
        printf "│\n└──────────────────────────────────────────────────────\n\n"
        return
    fi
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Tulis config ───────────────────────────────────────────────────
    info "Menulis MCP config ke ~/.claude.json..."
    local mcp_target="$ssh_host"
    if [ "$use_alias" = false ]; then
        mcp_target="${ssh_user}@${ssh_host}"
    fi
    if write_mcp_config "$mcp_target" "$run_path" "$ssh_port" "$use_alias"; then
        ok "mcpServers.odin ditambahkan (config lama di-backup ke .bak)"
    else
        err "Gagal menulis ~/.claude.json — periksa file format dan permissions"
        WIZARD_DONE=false
        return
    fi

    info "Menulis guard hook ke $settings_path..."
    if write_guard_config "$settings_path" "$guard_path"; then
        ok "PreToolUse hook + permissions ditambahkan"
    else
        err "Gagal menulis $settings_path — periksa permissions"
        WIZARD_DONE=false
        return
    fi

    # ── Verifikasi koneksi sebagai user odin ───────────────────────────
    printf "\n"
    info "Menguji koneksi SSH sebagai user odin (MCP runtime)..."
    local verify_args=(-o ConnectTimeout=5)
    if [ "$use_alias" = true ]; then
        verify_args+=("$ssh_host")
    else
        [ "$ssh_port" != "22" ] && verify_args+=(-p "$ssh_port")
        verify_args+=("${ssh_user}@${ssh_host}")
    fi

    printf "  ${BLUE}▸${NC} Perintah: ${CYAN}ssh %s echo ok${NC}\n" "${verify_args[*]}"
    if ssh "${verify_args[@]}" "echo ok" >/dev/null 2>&1; then
        ok "Koneksi SSH sebagai odin berhasil — MCP siap digunakan"
    else
        warn "Koneksi SSH sebagai odin belum bisa."
        printf "  ${YELLOW}⚠${NC}  MCP akan ${BOLD}gagal${NC} sampai user odin bisa login via SSH.\n"
        printf "  ${BLUE}ℹ${NC}  Penyebab umum:\n"
        printf "     • User odin belum ada di server (jalankan Setup Server)\n"
        printf "     • Password/SSH key belum di-set untuk user odin\n"
        printf "     • Port SSH salah\n"
        printf "  ${BLUE}ℹ${NC}  Setelah setup server, coba manual:\n"
        printf "     ${CYAN}ssh %s echo ok${NC}\n" "${verify_args[*]}"
    fi

    WIZARD_DONE=true
}

# ── SSH ControlMaster ──────────────────────────────────────────────────────
SSH_CTRL_SOCK=""
SSH_CTRL_HOST=""
SSH_CTRL_PORT=""

ssh_open() {
    local host="$1" port="${2:-}"
    SSH_CTRL_HOST="$host"
    SSH_CTRL_PORT="$port"
    SSH_CTRL_SOCK="/tmp/odin-ssh-$$"

    local port_args=()
    local display_str="$host"
    if [ -n "$port" ] && [ "$port" != "22" ]; then
        port_args=(-p "$port")
        display_str="$host port $port"
    fi

    printf "  ${BLUE}▸${NC} Membuka koneksi SSH ke ${CYAN}%s${NC}...\n" "$display_str"
    printf "    ${YELLOW}(Masukkan password jika diminta — hanya 1x untuk seluruh proses)${NC}\n"
    ssh "${port_args[@]}" \
        -o ControlMaster=yes -o ControlPath="$SSH_CTRL_SOCK" \
        -o ControlPersist=300 -o ConnectTimeout=10 \
        -o ServerAliveInterval=30 \
        -fN "$host" || return 1
    ok "  Koneksi SSH berhasil (ControlMaster aktif)"
    return 0
}

ssh_run() {
    ssh -o ControlPath="$SSH_CTRL_SOCK" "$SSH_CTRL_HOST" "$@"
}

ssh_run_tty() {
    ssh -t -o ControlPath="$SSH_CTRL_SOCK" "$SSH_CTRL_HOST" "$@"
}

ssh_upload() {
    local port_args=()
    if [ -n "$SSH_CTRL_PORT" ] && [ "$SSH_CTRL_PORT" != "22" ]; then
        port_args=(-P "$SSH_CTRL_PORT")
    fi
    scp -q "${port_args[@]}" -o ControlPath="$SSH_CTRL_SOCK" "$1" "${SSH_CTRL_HOST}:$2"
}

ssh_close() {
    if [ -n "$SSH_CTRL_SOCK" ]; then
        ssh -o ControlPath="$SSH_CTRL_SOCK" -O exit "$SSH_CTRL_HOST" 2>/dev/null || true
        SSH_CTRL_SOCK=""
        SSH_CTRL_HOST=""
        SSH_CTRL_PORT=""
    fi
}

# ── Server Setup ───────────────────────────────────────────────────────────
setup_server() {
    local admin_host="" admin_port="" project_root="" log_dirs="" deploy_mode=""

    printf "\n${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${CYAN}  Setup Server — Install ODIN di Server via SSH${NC}\n"
    printf "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"

    printf "  Proses ini akan melakukan hal berikut di server:\n"
    printf "    ${CYAN}1.${NC} Membuat user ${BOLD}odin${NC} (user khusus, bukan admin)\n"
    printf "    ${CYAN}2.${NC} Upload odin_agent.py & generate run.sh\n"
    printf "    ${CYAN}3.${NC} Membuat Python venv & install dependensi mcp[cli]\n"
    printf "    ${CYAN}4.${NC} Set permissions & direktori memory\n"
    printf "    ${CYAN}5.${NC} Set password/SSH key untuk user odin\n\n"
    printf "  ${BLUE}ℹ${NC}  Tekan Enter untuk memakai nilai default ${CYAN}[dalam kurung]${NC}.\n\n"

    # ── Input: Koneksi ──────────────────────────────────────────────────
    printf "┌─ ${BOLD}Koneksi Admin ke Server${NC} ─────────────────────────\n│\n"
    printf "│  Untuk install ODIN, perlu login ke server sebagai user\n"
    printf "│  yang punya akses ${BOLD}root${NC} atau ${BOLD}sudo${NC}.\n│\n"

    # Pre-fill dari wizard jika ada
    local default_admin="" default_port="22"
    if [ -n "$WIZ_SSH_HOST" ]; then
        default_admin="root@${WIZ_SSH_HOST}"
        default_port="$WIZ_SSH_PORT"
        printf "│  ${BLUE}ℹ${NC}  Dari wizard: host ${CYAN}%s${NC}, port ${CYAN}%s${NC}\n│\n" "$WIZ_SSH_HOST" "$WIZ_SSH_PORT"
    fi

    printf "│  Format: ${CYAN}user@host${NC} (contoh: ${CYAN}root@192.168.1.100${NC})\n"
    printf "│  Atau SSH alias jika ada (contoh: ${CYAN}vps-admin${NC})\n│\n"
    admin_host=$(ask_input "SSH admin" "${default_admin:-root@}")
    printf "│\n"
    printf "│  Port SSH server (sama dengan port di wizard)\n│\n"
    admin_port=$(ask_input "Port SSH" "$default_port")
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Input: Konfigurasi ──────────────────────────────────────────────
    printf "┌─ ${BOLD}Konfigurasi Aplikasi di Server${NC} ────────────────────\n│\n"

    printf "│  ${BOLD}PROJECT_ROOT${NC} — direktori utama aplikasi web Anda.\n"
    printf "│  Tempat file aplikasi berada (artisan, manage.py, dll).\n"
    printf "│  Contoh: ${CYAN}/var/www/simuru${NC}, ${CYAN}/var/www/html${NC}\n│\n"
    project_root=$(ask_input "PROJECT_ROOT" "/var/www/html")

    printf "│\n│  ${BOLD}ALLOWED_LOG_DIRS${NC} — direktori log yang boleh dibaca ODIN.\n"
    printf "│  Pisahkan dengan koma jika lebih dari satu.\n"
    printf "│  Contoh umum: ${CYAN}/var/log/nginx${NC}, ${CYAN}/var/log/mysql${NC},\n"
    printf "│               ${CYAN}/var/www/app/storage/logs${NC}\n│\n"
    log_dirs=$(ask_input "ALLOWED_LOG_DIRS" "/var/log,${project_root}")

    printf "│\n│  ${BOLD}DEPLOY_MODE${NC} — cara ODIN mengelola deploy aplikasi:\n│\n"
    printf "│  ${CYAN}1)${NC} local — Aplikasi di-deploy manual atau oleh CI/CD lain.\n"
    printf "│           ODIN hanya memantau, restart, dan maintain.\n│\n"
    printf "│  ${CYAN}2)${NC} git   — ODIN bisa menjalankan deploy otomatis:\n"
    printf "│           git pull → migrate → cache clear → restart.\n"
    printf "│           Cocok jika repo sudah di-clone di server.\n│\n"
    local mode_choice
    mode_choice=$(ask_choice "Pilihan" 2 "1")
    [ "$mode_choice" = "2" ] && deploy_mode="git" || deploy_mode="local"
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Konfirmasi ──────────────────────────────────────────────────────
    printf "┌─ ${BOLD}Ringkasan Setup Server${NC} ────────────────────────────\n│\n"
    printf "│  Admin SSH      : ${CYAN}%s${NC}\n" "$admin_host"
    printf "│  PROJECT_ROOT   : ${CYAN}%s${NC}\n" "$project_root"
    printf "│  LOG_DIRS       : ${CYAN}%s${NC}\n" "$log_dirs"
    printf "│  DEPLOY_MODE    : ${CYAN}%s${NC}\n" "$deploy_mode"
    printf "│\n"
    printf "│  Akan dibuat di server:\n"
    printf "│    ${CYAN}/home/odin/odin_agent.py${NC}  (600) — MCP agent\n"
    printf "│    ${CYAN}/home/odin/run.sh${NC}         (755) — launcher + env vars\n"
    printf "│    ${CYAN}/home/odin/.venv/${NC}                — Python virtualenv\n"
    printf "│    ${CYAN}/home/odin/memory/${NC}          (700) — persistent memory\n"
    printf "│\n"
    printf "${BOLD}│  Lanjutkan? [Y/n] (Enter = ya)${NC}: "
    local confirm
    read -r confirm <&"$TTY_FD"
    if [[ "$confirm" =~ ^[Nn]$ ]]; then
        info "Setup server dibatalkan."
        printf "│\n└──────────────────────────────────────────────────────\n\n"
        SERVER_DONE=false
        return
    fi
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Buka koneksi SSH (satu kali password) ───────────────────────────
    if ! ssh_open "$admin_host" "$admin_port"; then
        err "Gagal koneksi SSH — periksa host, port, user, dan password."
        SERVER_DONE=false
        return
    fi
    trap 'ssh_close' EXIT

    # ── Detect privileges ───────────────────────────────────────────────
    local pp=""
    local remote_user
    remote_user=$(ssh_run "whoami" 2>/dev/null) || remote_user="unknown"
    if [ "$remote_user" = "root" ]; then
        ok "  Terhubung sebagai root"
    else
        pp="sudo "
        info "  Terhubung sebagai $remote_user — menggunakan sudo"
    fi

    # ── 1. Python ───────────────────────────────────────────────────────
    printf "  ${BLUE}▸${NC} Memeriksa Python... "
    local srv_py
    if srv_py=$(ssh_run "python3 --version" 2>&1); then
        printf "${GREEN}✓${NC} %s\n" "$srv_py"
    else
        printf "${RED}✗${NC}\n"
        err "  Python 3 tidak ditemukan. Install: ${CYAN}apt install python3 python3-venv${NC}"
        ssh_close; trap - EXIT; SERVER_DONE=false; return
    fi

    # ── 2. python3-venv ─────────────────────────────────────────────────
    if ! ssh_run "python3 -c 'import venv'" >/dev/null 2>&1; then
        printf "  ${BLUE}▸${NC} Installing python3-venv... "
        if ssh_run "${pp}apt-get install -y -qq python3-venv" >/dev/null 2>&1; then
            printf "${GREEN}✓${NC}\n"
        else
            printf "${YELLOW}⚠${NC}\n"
            warn "  Install manual: ${CYAN}apt install python3-venv${NC}"
        fi
    fi

    # ── 3. User odin ────────────────────────────────────────────────────
    printf "  ${BLUE}▸${NC} Memeriksa user odin... "
    if ssh_run "id odin" >/dev/null 2>&1; then
        printf "${GREEN}✓${NC} sudah ada\n"
    else
        printf "${YELLOW}membuat${NC}... "
        if ssh_run "${pp}useradd -m -s /bin/bash odin" 2>/dev/null; then
            printf "${GREEN}✓${NC}\n"
        else
            printf "${RED}✗${NC}\n"
            err "  Gagal membuat user odin"
            ssh_close; trap - EXIT; SERVER_DONE=false; return
        fi
    fi

    # ── 4. Direktori ────────────────────────────────────────────────────
    printf "  ${BLUE}▸${NC} Membuat direktori... "
    ssh_run "${pp}mkdir -p /home/odin/memory" 2>/dev/null || true
    ssh_run "${pp}chown -R odin:odin /home/odin" 2>/dev/null || true
    ssh_run "${pp}chmod 700 /home/odin/memory" 2>/dev/null || true
    printf "${GREEN}✓${NC}\n"

    # ── 5. Venv & mcp[cli] ──────────────────────────────────────────────
    printf "  ${BLUE}▸${NC} Membuat venv & install mcp[cli]...\n"
    printf "    ${YELLOW}(bisa memakan waktu 1-2 menit)${NC}\n"

    if ! ssh_run "cat > /tmp/odin_venv.sh && chmod +x /tmp/odin_venv.sh" <<'VENVEOF'
#!/bin/bash
set -e
python3 -m venv /home/odin/.venv
/home/odin/.venv/bin/pip install --quiet --upgrade pip
/home/odin/.venv/bin/pip install --quiet "mcp[cli]"
VENVEOF
    then
        err "  Gagal mengirim script venv ke server"
        ssh_close; trap - EXIT; SERVER_DONE=false; return
    fi
    ssh_run "${pp}chown odin:odin /tmp/odin_venv.sh" 2>/dev/null || true
    local venv_out
    if venv_out=$(ssh_run "${pp}su - odin -c '/tmp/odin_venv.sh'" 2>&1); then
        ok "    Venv & mcp[cli] terinstall"
    else
        err "    Gagal install venv/mcp[cli]"
        [ -n "$venv_out" ] && printf "    %s\n" "$venv_out"
        warn "  Cek koneksi internet server, lalu coba manual:"
        printf "    ${CYAN}su - odin -c 'python3 -m venv /home/odin/.venv'${NC}\n"
        printf "    ${CYAN}su - odin -c '/home/odin/.venv/bin/pip install \"mcp[cli]\"'${NC}\n"
        ssh_run "rm -f /tmp/odin_venv.sh" 2>/dev/null || true
        ssh_close; trap - EXIT; SERVER_DONE=false; return
    fi
    ssh_run "rm -f /tmp/odin_venv.sh" 2>/dev/null || true

    # ── 6. Upload odin_agent.py ──────────────────────────────────────────
    printf "  ${BLUE}▸${NC} Mengunggah odin_agent.py... "
    if ssh_upload "$INSTALL_DIR/server/odin_agent.py" "/tmp/odin_agent.py"; then
        ssh_run "${pp}mv /tmp/odin_agent.py /home/odin/odin_agent.py" 2>/dev/null || true
        ssh_run "${pp}chown odin:odin /home/odin/odin_agent.py" 2>/dev/null || true
        ssh_run "${pp}chmod 600 /home/odin/odin_agent.py" 2>/dev/null || true
        printf "${GREEN}✓${NC}\n"
    else
        printf "${RED}✗${NC}\n"
        err "  Gagal upload odin_agent.py"
        ssh_close; trap - EXIT; SERVER_DONE=false; return
    fi

    # ── 7. Generate & upload run.sh ─────────────────────────────────────
    printf "  ${BLUE}▸${NC} Menulis run.sh... "
    local tmp_run
    tmp_run=$(mktemp)
    cat > "$tmp_run" <<RUNEOF
#!/usr/bin/env bash
export DEPLOY_MODE=${deploy_mode}
export PROJECT_ROOT=${project_root}
export ALLOWED_LOG_DIRS=${log_dirs}
export MEMORY_DIR=/home/odin/memory
cd "\$PROJECT_ROOT" || { echo "FATAL: \$PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }
exec /home/odin/.venv/bin/python /home/odin/odin_agent.py
RUNEOF
    if ssh_upload "$tmp_run" "/tmp/odin_run.sh"; then
        ssh_run "${pp}mv /tmp/odin_run.sh /home/odin/run.sh" 2>/dev/null || true
        ssh_run "${pp}chown odin:odin /home/odin/run.sh" 2>/dev/null || true
        ssh_run "${pp}chmod 755 /home/odin/run.sh" 2>/dev/null || true
        printf "${GREEN}✓${NC}\n"
    else
        printf "${RED}✗${NC}\n"
        err "  Gagal upload run.sh"
    fi
    rm -f "$tmp_run"

    # ── 8. Autentikasi SSH user odin ───────────────────────────────────
    printf "\n  ${BOLD}Autentikasi SSH user odin${NC}\n"
    printf "  Claude Code akan terhubung ke server sebagai user ${CYAN}odin${NC}.\n"
    printf "  User ini perlu cara login — pilih salah satu:\n\n"
    printf "  ${CYAN}1)${NC} Set password  — sederhana, langsung bisa dipakai\n"
    printf "  ${CYAN}2)${NC} Setup SSH key — lebih aman, tanpa ketik password\n"
    printf "                    (copy public key dari laptop ke server)\n"
    printf "  ${CYAN}3)${NC} Lewati        — atur nanti secara manual\n"
    printf "                    (${CYAN}sudo passwd odin${NC} atau copy SSH key)\n\n"
    local auth_choice
    auth_choice=$(ask_choice "Pilihan" 3 "1")
    if [ "$auth_choice" = "1" ]; then
        printf "\n"
        if ssh_run_tty "${pp}passwd odin"; then
            ok "  Password odin di-set"
        else
            warn "  Gagal set password — jalankan manual di server: ${CYAN}sudo passwd odin${NC}"
        fi
    elif [ "$auth_choice" = "2" ]; then
        printf "\n"
        local pubkey_path="$HOME/.ssh/id_rsa.pub"
        if [ ! -f "$pubkey_path" ]; then
            pubkey_path="$HOME/.ssh/id_ed25519.pub"
        fi
        if [ -f "$pubkey_path" ]; then
            local pubkey
            pubkey=$(cat "$pubkey_path")
            printf "  ${BLUE}▸${NC} Meng-copy public key ke server...\n"
            if ssh_run "${pp}mkdir -p /home/odin/.ssh && chmod 700 /home/odin/.ssh && echo '$pubkey' >> /home/odin/.ssh/authorized_keys && chmod 600 /home/odin/.ssh/authorized_keys && chown -R odin:odin /home/odin/.ssh" 2>/dev/null; then
                ok "  SSH key berhasil di-copy (${pubkey_path})"
            else
                warn "  Gagal copy SSH key — coba manual:"
                printf "    ${CYAN}ssh-copy-id -i %s odin@server${NC}\n" "$pubkey_path"
            fi
        else
            warn "  SSH key tidak ditemukan di laptop (~/.ssh/id_*.pub)"
            printf "    Buat dulu: ${CYAN}ssh-keygen -t ed25519${NC}\n"
            printf "    Lalu copy: ${CYAN}ssh-copy-id odin@server${NC}\n"
        fi
    fi

    # ── 9. Verifikasi file server ──────────────────────────────────────
    printf "\n  ${BLUE}▸${NC} Verifikasi file di server... "
    local all_ok=true
    ssh_run "test -f /home/odin/odin_agent.py" 2>/dev/null || all_ok=false
    ssh_run "test -f /home/odin/run.sh" 2>/dev/null || all_ok=false
    ssh_run "test -d /home/odin/.venv" 2>/dev/null || all_ok=false
    ssh_run "test -d /home/odin/memory" 2>/dev/null || all_ok=false
    if [ "$all_ok" = true ]; then
        printf "${GREEN}✓ Semua file & direktori OK${NC}\n"
    else
        printf "${YELLOW}⚠ Ada yang kurang — periksa manual${NC}\n"
    fi

    # ── 10. E2E: Test koneksi sebagai user odin dari laptop ─────────────
    printf "\n  ${BOLD}Verifikasi MCP (End-to-End)${NC}\n"
    printf "  Test apakah laptop bisa SSH ke server sebagai user odin.\n"
    printf "  (Ini yang akan dilakukan Claude Code setiap session)\n\n"

    # Bangun SSH command sama seperti yang akan dipakai Claude Code
    local e2e_args=(-o ConnectTimeout=10 -o BatchMode=yes)
    if [ -n "$WIZ_SSH_HOST" ] && [ "$WIZ_SSH_IS_ALIAS" = "true" ]; then
        e2e_args+=("$WIZ_SSH_HOST")
    elif [ -n "$WIZ_SSH_HOST" ]; then
        [ "$WIZ_SSH_PORT" != "22" ] && e2e_args+=(-p "$WIZ_SSH_PORT")
        e2e_args+=("${WIZ_SSH_USER:-odin}@${WIZ_SSH_HOST}")
    else
        local odin_host="${admin_host#*@}"
        [ "$admin_port" != "22" ] && e2e_args+=(-p "$admin_port")
        e2e_args+=("odin@${odin_host}")
    fi

    printf "  ${BLUE}▸${NC} Test: ${CYAN}ssh %s echo ok${NC}\n" "${e2e_args[*]}"

    local e2e_result
    e2e_result=$(ssh "${e2e_args[@]}" "echo MCP_OK && test -x /home/odin/run.sh && echo RUN_OK && python3 --version" 2>&1)
    local e2e_rc=$?

    if [ $e2e_rc -eq 0 ] && echo "$e2e_result" | grep -q "MCP_OK"; then
        ok "  SSH sebagai odin berhasil!"
        if echo "$e2e_result" | grep -q "RUN_OK"; then
            printf "  ${GREEN}✓${NC} run.sh ditemukan dan executable\n"
        fi
        local py_ver
        py_ver=$(echo "$e2e_result" | grep -i "python" | head -1)
        [ -n "$py_ver" ] && printf "  ${GREEN}✓${NC} %s\n" "$py_ver"
        printf "\n  ${GREEN}✓${NC} ${BOLD}MCP siap digunakan!${NC} Buka Claude Code dan coba:\n"
        printf "    ${CYAN}\"cek status server\"${NC}\n"
    else
        warn "  SSH sebagai odin gagal — MCP belum bisa berjalan."
        if echo "$e2e_result" | grep -qi "permission denied"; then
            printf "  ${YELLOW}⚠${NC}  Login ditolak — password/key user odin belum benar.\n"
        else
            printf "  ${YELLOW}⚠${NC}  Error: %s\n" "${e2e_result:0:150}"
        fi
        printf "\n  ${BLUE}ℹ${NC}  Cara debug (tanpa BatchMode, agar bisa ketik password):\n"
        # Rebuild args tanpa BatchMode
        local dbg_args=(-o ConnectTimeout=10)
        if [ -n "$WIZ_SSH_HOST" ] && [ "$WIZ_SSH_IS_ALIAS" = "true" ]; then
            dbg_args+=("$WIZ_SSH_HOST")
        elif [ -n "$WIZ_SSH_HOST" ]; then
            [ "$WIZ_SSH_PORT" != "22" ] && dbg_args+=(-p "$WIZ_SSH_PORT")
            dbg_args+=("${WIZ_SSH_USER:-odin}@${WIZ_SSH_HOST}")
        else
            local dbg_host="${admin_host#*@}"
            [ "$admin_port" != "22" ] && dbg_args+=(-p "$admin_port")
            dbg_args+=("odin@${dbg_host}")
        fi
        printf "     ${CYAN}ssh %s echo ok${NC}\n" "${dbg_args[*]}"
    fi

    # ── Selesai ─────────────────────────────────────────────────────────
    ssh_close
    trap - EXIT

    local version
    version=$(grep -m1 '__version__' "$INSTALL_DIR/server/odin_agent.py" | cut -d'"' -f2)

    printf "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${GREEN}  ✓ ODIN v${version} berhasil di-setup di server!${NC}\n"
    printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"
    printf "  File di server:\n"
    printf "    ${CYAN}/home/odin/odin_agent.py${NC}  (600)\n"
    printf "    ${CYAN}/home/odin/run.sh${NC}         (755)\n"
    printf "    ${CYAN}/home/odin/.venv/${NC}\n"
    printf "    ${CYAN}/home/odin/memory/${NC}        (700)\n\n"

    SERVER_DONE=true
}

# ── Tampilkan konfigurasi yang perlu user pasang ────────────────────────────
show_config_guide() {
    local guard_path="$INSTALL_DIR/client/odin_guard.py"
    local version
    version=$(grep -m1 '__version__' "$INSTALL_DIR/server/odin_agent.py" | cut -d'"' -f2)

    printf "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${GREEN}  ✓ ODIN v${version} berhasil diinstall!${NC}\n"
    printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

    printf "\n${BOLD}📁 Lokasi:${NC}\n"
    printf "   Instalasi : ${CYAN}%s${NC}\n" "$INSTALL_DIR"
    printf "   Guard     : ${CYAN}%s${NC}\n" "$guard_path"
    printf "   Server    : ${CYAN}%s${NC}\n" "$INSTALL_DIR/server/odin_agent.py"

    printf "\n${BOLD}📋 Langkah selanjutnya:${NC}\n"
    printf "\n${YELLOW}1.${NC} ${BOLD}Setup server (di VPS):${NC}\n"
    cat <<EOF
   Salin file ke server:
     scp ${INSTALL_DIR}/server/odin_agent.py  odin@server:/home/odin/
     scp ${INSTALL_DIR}/server/run.sh            odin@server:/home/odin/

   Di server, buat venv & install dependensi:
     python3 -m venv /home/odin/.venv
     /home/odin/.venv/bin/pip install "mcp[cli]"
EOF

    printf "\n${YELLOW}2.${NC} ${BOLD}Konfigurasi MCP (Claude Code):${NC}\n"
    printf "   Tambahkan ke ${CYAN}~/.claude.json${NC}:\n\n"
    printf "   ${BOLD}Jika pakai SSH alias:${NC}\n"
    cat <<EOF
   {
     "mcpServers": {
       "odin": {
         "type": "stdio",
         "command": "ssh",
         "args": ["ssh-alias-anda", "/home/odin/run.sh"]
       }
     }
   }
EOF
    printf "\n   ${BOLD}Jika pakai IP + port non-standard:${NC}\n"
    cat <<EOF
   {
     "mcpServers": {
       "odin": {
         "type": "stdio",
         "command": "ssh",
         "args": ["-p", "2409", "-o", "StrictHostKeyChecking=accept-new",
                  "odin@192.168.1.100", "/home/odin/run.sh"]
       }
     }
   }
EOF

    printf "\n${YELLOW}3.${NC} ${BOLD}Pasang guard hook:${NC}\n"
    printf "   Tambahkan ke ${CYAN}.claude/settings.json${NC} project:\n\n"
    cat <<HOOKEOF
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
               "command": "python3 '${guard_path}'",
               "timeout": 10
             }
           ]
         }
       ],
       "PostToolUse": [
         {
           "matcher": "mcp__odin__inspect_server",
           "hooks": [
             {
               "type": "command",
               "command": "python3 '${guard_path}'",
               "timeout": 10
             }
           ]
         }
       ]
     }
   }
HOOKEOF

    printf "\n${YELLOW}4.${NC} ${BOLD}Update ODIN:${NC}\n"
    printf "   Jalankan kapan saja: ${CYAN}odin-update${NC}\n"
    printf "   Atau: ${CYAN}%s/odin-update.sh${NC}\n" "$INSTALL_DIR"

    printf "\n${BOLD}📖 Dokumentasi:${NC} ${CYAN}https://github.com/${REPO}${NC}\n"
    printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    banner
    detect_os
    info "Terdeteksi: $OS ($(uname -m))"
    check_prereqs
    install_odin
    setup_venv
    install_guard_hook
    install_updater

    WIZARD_DONE=false
    setup_wizard

    SERVER_DONE=false
    printf "\n"
    printf "┌─ ${BOLD}Setup Server${NC} ─────────────────────────────────────\n│\n"
    printf "│  Selain konfigurasi laptop, ODIN juga perlu di-install\n"
    printf "│  di server (upload agent, buat user odin, install venv).\n│\n"
    printf "│  ${CYAN}•${NC} Pilih ${BOLD}Ya${NC} jika server belum pernah di-setup untuk ODIN\n"
    printf "│  ${CYAN}•${NC} Pilih ${BOLD}Tidak${NC} jika server sudah di-setup sebelumnya\n│\n"
    printf "${BOLD}│  Setup ODIN di server via SSH? [Y/n] (Enter = ya)${NC}: "
    local do_server
    read -r do_server <&"$TTY_FD"
    printf "│\n└──────────────────────────────────────────────────────\n"
    if [[ ! "$do_server" =~ ^[Nn]$ ]]; then
        setup_server
    fi

    local version
    version=$(grep -m1 '__version__' "$INSTALL_DIR/server/odin_agent.py" | cut -d'"' -f2)

    if [ "$WIZARD_DONE" = true ] && [ "$SERVER_DONE" = true ]; then
        printf "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
        printf "${BOLD}${GREEN}  ✓ ODIN v${version} — Laptop & Server siap!${NC}\n"
        printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"
        printf "  Buka Claude Code dan coba:\n"
        printf "    ${CYAN}\"cek status server\"${NC}\n\n"
        printf "  Update:  ${CYAN}odin-update${NC}\n"
        printf "  Docs:    ${CYAN}https://github.com/${REPO}${NC}\n\n"
    elif [ "$WIZARD_DONE" = true ]; then
        printf "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
        printf "${BOLD}${GREEN}  ✓ ODIN v${version} — Laptop siap!${NC}\n"
        printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"
        printf "  Setup server nanti: jalankan ${CYAN}install.sh${NC} lagi\n"
        printf "  atau manual: ${CYAN}scp server/* odin@server:/home/odin/${NC}\n\n"
        printf "  Update:  ${CYAN}odin-update${NC}\n"
        printf "  Docs:    ${CYAN}https://github.com/${REPO}${NC}\n\n"
    elif [ "$SERVER_DONE" = true ]; then
        printf "\n  Laptop belum dikonfigurasi.\n"
        show_config_guide
    else
        show_config_guide
    fi
}

main "$@"
[ "$TTY_FD" = 3 ] && exec 3<&- 2>/dev/null || true
