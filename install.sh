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
    ║               ⚡  O D I N  Installer  ⚡               ║
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
        git -C "$INSTALL_DIR" fetch origin "$BRANCH" --quiet
        git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" --quiet
        ok "ODIN diperbarui ke versi terbaru"
    else
        info "Mengunduh ODIN dari GitHub..."
        if [ -d "$INSTALL_DIR" ]; then
            warn "$INSTALL_DIR sudah ada (bukan git repo) — backup & replace"
            mv "$INSTALL_DIR" "${INSTALL_DIR}.bak.$(date +%s)"
        fi
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" --quiet
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
    "$PYTHON" -m venv "$venv_dir"
    "$venv_dir/bin/pip" install --quiet --upgrade pip
    "$venv_dir/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
    ok "Dependensi terinstall (mcp[cli])"
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
            printf "${BOLD}  %s${NC} [${CYAN}%s${NC}]: " "$prompt_text" "$default_val"
        else
            printf "${BOLD}  %s${NC}: " "$prompt_text"
        fi
        read -r result
        result="${result:-$default_val}"
        if [ -n "$result" ]; then
            echo "$result"
            return
        fi
        warn "Nilai tidak boleh kosong."
    done
}

ask_choice() {
    local prompt_text="$1" max="$2" default="$3" choice=""
    while true; do
        printf "${BOLD}  %s${NC} [${CYAN}%s${NC}]: " "$prompt_text" "$default"
        read -r choice
        choice="${choice:-$default}"
        if [[ "$choice" =~ ^[1-9][0-9]*$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "$max" ]; then
            echo "$choice"
            return
        fi
        warn "Pilih 1-${max}."
    done
}

test_ssh() {
    local host="$1" run_path="$2"
    printf "  ${BLUE}▸${NC} Menguji koneksi SSH ke ${CYAN}%s${NC}... " "$host"
    if ssh -o ConnectTimeout=5 -o BatchMode=yes "$host" "test -f '$run_path'" 2>/dev/null; then
        printf "${GREEN}✓ OK${NC}\n"
        return 0
    else
        printf "${YELLOW}⚠${NC}\n"
        warn "Koneksi gagal atau file belum ada — lanjutkan setup, perbaiki nanti."
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
    d = json.load(open('$claude_json'))
    mcp = d.get('mcpServers', {})
    entry = mcp.get('odin')
    if entry and entry.get('args'):
        args = entry['args']
        host = args[0] if len(args) > 0 else ''
        path = args[1] if len(args) > 1 else ''
        print(f'odin|{host}|{path}')
        sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null) || return 1

    EXISTING_MCP_NAME="${detect_result%%|*}"
    local rest="${detect_result#*|}"
    EXISTING_SSH_HOST="${rest%%|*}"
    EXISTING_RUN_PATH="${rest#*|}"
    return 0
}

# ── Write MCP config ───────────────────────────────────────────────────────
write_mcp_config() {
    local ssh_host="$1" run_path="$2"
    local claude_json="$HOME/.claude.json"

    if [ -f "$claude_json" ]; then
        cp "$claude_json" "${claude_json}.bak"
    fi

    "$PYTHON" -c "
import json, os

claude_json = os.path.expanduser('~/.claude.json')
try:
    with open(claude_json) as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}

mcp = data.setdefault('mcpServers', {})

mcp['odin'] = {
    'type': 'stdio',
    'command': 'ssh',
    'args': ['$ssh_host', '$run_path']
}

with open(claude_json, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
"
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
import json, os

settings_path = '$settings_path'
guard_path = '$guard_path'

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
        'command': f\"python3 '{guard_path}'\",
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
        'command': f\"python3 '{guard_path}'\",
        'timeout': 10
    }]
}
post = hooks.setdefault('PostToolUse', [])
post[:] = [h for h in post if not (isinstance(h, dict) and 'mcp__odin__' in h.get('matcher', ''))]
post.append(POST_HOOK)

with open(settings_path, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
"
}

# ── Setup Wizard ───────────────────────────────────────────────────────────
setup_wizard() {
    local ssh_host="" run_path="" guard_scope="" project_path="" settings_path=""
    local guard_path="$INSTALL_DIR/client/odin_guard.py"
    local wizard_skipped=false

    printf "\n${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${CYAN}  ⚙  Setup Wizard — Konfigurasi Claude Code${NC}\n"
    printf "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"

    # ── Detect existing ────────────────────────────────────────────────
    if detect_existing_config; then
        printf "  ${GREEN}✓${NC} Konfigurasi ODIN terdeteksi:\n"
        printf "      MCP name  : ${CYAN}%s${NC}\n" "$EXISTING_MCP_NAME"
        printf "      SSH host  : ${CYAN}%s${NC}\n" "$EXISTING_SSH_HOST"
        printf "      Path      : ${CYAN}%s${NC}\n\n" "$EXISTING_RUN_PATH"
        printf "${BOLD}  Konfigurasi ulang? [y/N]${NC}: "
        read -r reconfig
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

    printf "  Wizard ini mengkonfigurasi Claude Code agar terhubung\n"
    printf "  dengan ODIN di server Anda. Tekan Enter untuk pakai default.\n\n"

    # ── Koneksi Server ─────────────────────────────────────────────────
    printf "┌─ ${BOLD}Koneksi Server${NC} ─────────────────────────────────────\n│\n"
    printf "│  SSH host/alias server (sesuai ~/.ssh/config atau user@host)\n"
    printf "│\n"
    ssh_host=$(ask_input "SSH host" "$ssh_host")
    printf "│\n"
    printf "│  Path run.sh di server\n"
    run_path=$(ask_input "Path run.sh" "${run_path:-/home/odin/run.sh}")
    printf "│\n"
    test_ssh "$ssh_host" "$run_path"
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Scope Guard ────────────────────────────────────────────────────
    printf "┌─ ${BOLD}Scope Guard Hook${NC} ────────────────────────────────────\n│\n"
    printf "│  Guard hook bisa dipasang global atau per-project.\n│\n"
    printf "│  ${CYAN}1)${NC} Global  — berlaku untuk semua project Claude Code\n"
    printf "│  ${CYAN}2)${NC} Project — hanya untuk satu project directory\n│\n"
    guard_scope=$(ask_choice "Pilihan" 2 "1")

    if [ "$guard_scope" = "2" ]; then
        printf "│\n"
        while true; do
            project_path=$(ask_input "Path project" "")
            if [ -d "$project_path" ]; then
                printf "│  ${GREEN}✓${NC} Direktori valid\n"
                break
            fi
            warn "Direktori '$project_path' tidak ditemukan."
        done
        settings_path="${project_path}/.claude/settings.json"
    else
        settings_path="$HOME/.claude/settings.json"
    fi
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Konfirmasi ─────────────────────────────────────────────────────
    printf "┌─ ${BOLD}Konfirmasi${NC} ─────────────────────────────────────────\n│\n"
    printf "│  SSH host        : ${CYAN}%s${NC}\n" "$ssh_host"
    printf "│  Path run.sh     : ${CYAN}%s${NC}\n" "$run_path"
    if [ "$guard_scope" = "2" ]; then
        printf "│  Guard scope     : ${CYAN}Project${NC} (%s)\n" "$project_path"
    else
        printf "│  Guard scope     : ${CYAN}Global${NC}\n"
    fi
    printf "│\n"
    printf "│  File yang akan ditulis:\n"
    printf "│    • ${CYAN}~/.claude.json${NC}          (mcpServers.odin)\n"
    printf "│    • ${CYAN}%s${NC}\n" "$settings_path"
    printf "│\n"
    printf "${BOLD}│  Tulis konfigurasi? [Y/n]${NC}: "
    read -r confirm
    if [[ "$confirm" =~ ^[Nn]$ ]]; then
        info "Dibatalkan — konfigurasi tidak ditulis."
        WIZARD_DONE=false
        printf "│\n└──────────────────────────────────────────────────────\n\n"
        return
    fi
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Tulis config ───────────────────────────────────────────────────
    info "Menulis MCP config ke ~/.claude.json..."
    write_mcp_config "$ssh_host" "$run_path"
    ok "mcpServers.odin ditambahkan (existing config dipertahankan)"

    info "Menulis guard hook ke $settings_path..."
    write_guard_config "$settings_path" "$guard_path"
    ok "PreToolUse hook + permissions ditambahkan"

    WIZARD_DONE=true
}

# ── SSH ControlMaster ──────────────────────────────────────────────────────
SSH_CTRL_SOCK=""
SSH_CTRL_HOST=""

ssh_open() {
    local host="$1"
    SSH_CTRL_HOST="$host"
    SSH_CTRL_SOCK="/tmp/odin-ssh-$$"
    printf "  ${BLUE}▸${NC} Membuka koneksi SSH ke ${CYAN}%s${NC}...\n" "$host"
    printf "    ${YELLOW}(Masukkan password jika diminta — hanya 1x untuk seluruh proses)${NC}\n"
    ssh -o ControlMaster=yes -o ControlPath="$SSH_CTRL_SOCK" \
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
    scp -q -o ControlPath="$SSH_CTRL_SOCK" "$1" "${SSH_CTRL_HOST}:$2"
}

ssh_close() {
    if [ -n "$SSH_CTRL_SOCK" ]; then
        ssh -o ControlPath="$SSH_CTRL_SOCK" -O exit "$SSH_CTRL_HOST" 2>/dev/null || true
        SSH_CTRL_SOCK=""
        SSH_CTRL_HOST=""
    fi
}

# ── Server Setup ───────────────────────────────────────────────────────────
setup_server() {
    local admin_host="" project_root="" log_dirs="" deploy_mode=""

    printf "\n${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${CYAN}  Setup Server — Push via SSH${NC}\n"
    printf "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"

    printf "  Installer akan men-setup ODIN di server via SSH:\n"
    printf "    ${CYAN}•${NC} Membuat user ${BOLD}odin${NC} (jika belum ada)\n"
    printf "    ${CYAN}•${NC} Upload odin_agent.py & run.sh\n"
    printf "    ${CYAN}•${NC} Membuat venv & install mcp[cli]\n"
    printf "    ${CYAN}•${NC} Set permissions & direktori memory\n\n"

    # ── Input: Koneksi ──────────────────────────────────────────────────
    printf "┌─ ${BOLD}Koneksi Admin${NC} ──────────────────────────────────────\n│\n"
    printf "│  SSH user dengan akses root/sudo di server\n│\n"
    admin_host=$(ask_input "SSH user@host" "root@")
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Input: Konfigurasi ──────────────────────────────────────────────
    printf "┌─ ${BOLD}Konfigurasi Aplikasi${NC} ──────────────────────────────\n│\n"
    printf "│  Path root aplikasi web di server\n│\n"
    project_root=$(ask_input "PROJECT_ROOT" "/var/www/html")
    printf "│\n│  Direktori log yang boleh dibaca (pisah koma)\n│\n"
    log_dirs=$(ask_input "ALLOWED_LOG_DIRS" "/var/log,/var/www")
    printf "│\n│  Mode operasi:\n│\n"
    printf "│  ${CYAN}1)${NC} local — project sudah ada di server\n"
    printf "│  ${CYAN}2)${NC} git   — deploy via git pull\n│\n"
    local mode_choice
    mode_choice=$(ask_choice "Pilihan" 2 "1")
    [ "$mode_choice" = "2" ] && deploy_mode="git" || deploy_mode="local"
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Konfirmasi ──────────────────────────────────────────────────────
    printf "┌─ ${BOLD}Ringkasan${NC} ──────────────────────────────────────────\n│\n"
    printf "│  Admin SSH      : ${CYAN}%s${NC}\n" "$admin_host"
    printf "│  PROJECT_ROOT   : ${CYAN}%s${NC}\n" "$project_root"
    printf "│  LOG_DIRS       : ${CYAN}%s${NC}\n" "$log_dirs"
    printf "│  DEPLOY_MODE    : ${CYAN}%s${NC}\n" "$deploy_mode"
    printf "│\n"
    printf "│  Akan dibuat di server:\n"
    printf "│    ${CYAN}/home/odin/odin_agent.py${NC}  (600)\n"
    printf "│    ${CYAN}/home/odin/run.sh${NC}         (755)\n"
    printf "│    ${CYAN}/home/odin/.venv/${NC}           (Python venv)\n"
    printf "│    ${CYAN}/home/odin/memory/${NC}          (700)\n"
    printf "│\n"
    printf "${BOLD}│  Lanjutkan? [Y/n]${NC}: "
    local confirm
    read -r confirm
    if [[ "$confirm" =~ ^[Nn]$ ]]; then
        info "Setup server dibatalkan."
        printf "│\n└──────────────────────────────────────────────────────\n\n"
        SERVER_DONE=false
        return
    fi
    printf "│\n└──────────────────────────────────────────────────────\n\n"

    # ── Buka koneksi SSH (satu kali password) ───────────────────────────
    if ! ssh_open "$admin_host"; then
        err "Gagal koneksi SSH — periksa host, user, dan password."
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

    ssh_run "cat > /tmp/odin_venv.sh && chmod +x /tmp/odin_venv.sh" <<'VENVEOF'
#!/bin/bash
set -e
python3 -m venv /home/odin/.venv
/home/odin/.venv/bin/pip install --quiet --upgrade pip
/home/odin/.venv/bin/pip install --quiet "mcp[cli]"
VENVEOF
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

    # ── 8. Password odin ────────────────────────────────────────────────
    printf "\n  ${BOLD}Password user odin${NC}\n"
    printf "  Claude Code terhubung ke server sebagai user odin via SSH.\n"
    printf "  User odin perlu password untuk login SSH.\n\n"
    printf "  ${CYAN}1)${NC} Set password sekarang (interaktif)\n"
    printf "  ${CYAN}2)${NC} Lewati (set nanti: ${CYAN}sudo passwd odin${NC})\n\n"
    local pw_choice
    pw_choice=$(ask_choice "Pilihan" 2 "1")
    if [ "$pw_choice" = "1" ]; then
        printf "\n"
        if ssh_run_tty "${pp}passwd odin"; then
            ok "  Password odin di-set"
        else
            warn "  Gagal set password — jalankan manual di server: ${CYAN}sudo passwd odin${NC}"
        fi
    fi

    # ── 9. Verifikasi ───────────────────────────────────────────────────
    printf "\n  ${BLUE}▸${NC} Verifikasi... "
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
    printf "   Tambahkan ke ${CYAN}~/.claude.json${NC} (atau project .claude.json):\n\n"
    cat <<EOF
   {
     "mcpServers": {
       "odin": {
         "type": "stdio",
         "command": "ssh",
         "args": ["your-server-alias", "/home/odin/run.sh"]
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
    printf "  ${BOLD}Setup ODIN di server juga via SSH? [Y/n]${NC}: "
    local do_server
    read -r do_server
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
