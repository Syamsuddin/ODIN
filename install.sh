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

    if command -v claude >/dev/null 2>&1; then
        ok "Claude Code CLI ditemukan"
    else
        fatal "Claude Code CLI belum terinstall. Install dulu: https://docs.anthropic.com/en/docs/claude-code/getting-started"
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

install_guard_hook() {
    local hooks_dir="$INSTALL_DIR/client"
    local guard="$hooks_dir/odin_guard.py"

    if [ ! -f "$guard" ]; then
        fatal "Guard tidak ditemukan di $guard"
    fi
    chmod +x "$guard"
    ok "Guard hook siap: $guard"
}

# ── Install CLI dependencies (paramiko, pyyaml) ───────────────────────────
install_cli_deps() {
    local req="$INSTALL_DIR/requirements-cli.txt"
    if [ ! -f "$req" ]; then
        return
    fi
    info "Menginstall dependensi CLI (paramiko, pyyaml)..."
    if $PYTHON -m pip install --quiet -r "$req" 2>/dev/null; then
        ok "Dependensi CLI terinstall"
    elif pip3 install --quiet -r "$req" 2>/dev/null; then
        ok "Dependensi CLI terinstall"
    else
        warn "Gagal install dependensi CLI — 'odin server add' butuh: pip install paramiko pyyaml"
    fi
}

# ── Install odin CLI command ──────────────────────────────────────────────
install_cli_command() {
    local cli="$INSTALL_DIR/client/odin_cli.py"
    if [ ! -f "$cli" ]; then
        return
    fi
    chmod +x "$cli"
    local bin_link="/usr/local/bin/odin"
    if [ -w "$(dirname "$bin_link")" ] 2>/dev/null; then
        ln -sf "$cli" "$bin_link" 2>/dev/null && \
            ok "Perintah 'odin' tersedia di PATH" || true
    else
        if command -v sudo >/dev/null 2>&1; then
            sudo ln -sf "$cli" "$bin_link" 2>/dev/null && \
                ok "Perintah 'odin' tersedia di PATH" || \
                warn "Jalankan manual: python3 $cli"
        else
            warn "Tidak bisa buat symlink — jalankan manual: python3 $cli"
        fi
    fi
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

# ── Detect existing config ─────────────────────────────────────────────────
detect_existing_config() {
    EXISTING_SSH_HOST=""
    EXISTING_RUN_PATH=""
    EXISTING_MCP_NAME=""

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
        path = args[-1] if args else ''
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
    local ssh_target="$1" run_path="$2" ssh_port="${3:-22}" is_alias="${4:-false}" ssh_key="${5:-}"
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
ssh_key = sys.argv[5] if len(sys.argv) > 5 else ''

args = []
if is_alias:
    args = [ssh_target, run_path]
else:
    if ssh_port != '22':
        args += ['-p', ssh_port]
    args += ['-o', 'StrictHostKeyChecking=accept-new']
    if ssh_key:
        args += ['-o', 'IdentitiesOnly=yes']
        args += ['-o', 'BatchMode=yes']
        args += ['-i', ssh_key]
    else:
        args += ['-o', 'BatchMode=yes']
    args += [ssh_target, run_path]

mcp['odin'] = {
    'type': 'stdio',
    'command': 'ssh',
    'args': args
}

with open(claude_json, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
" "$ssh_target" "$run_path" "$ssh_port" "$is_alias" "$ssh_key"
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

# ── Setup ODIN (3 input → laptop → server → test MCP) ───────────────────
setup_full() {
    local server_host="" admin_port="22" admin_user="root"
    local guard_path="$INSTALL_DIR/client/odin_guard.py"
    local settings_path="$HOME/.claude/settings.json"

    printf "\n${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${CYAN}  ⚙  Setup ODIN${NC}\n"
    printf "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"

    printf "  Installer akan:\n"
    printf "    ${CYAN}1.${NC} Siapkan laptop  — SSH key + Claude Code config\n"
    printf "    ${CYAN}2.${NC} Setup server    — buat user odin, upload agent\n"
    printf "    ${CYAN}3.${NC} Test koneksi    — verifikasi MCP end-to-end\n\n"
    printf "  ${BLUE}ℹ${NC}  Cukup masukkan 3 data — sisanya otomatis.\n\n"

    # ── 3 input ─────────────────────────────────────────────────────────
    printf "┌─ ${BOLD}Data Server${NC} ──────────────────────────────────────\n│\n"
    server_host=$(ask_input "IP / hostname server" "")
    printf "│\n"
    admin_port=$(ask_input "Port SSH" "22")
    printf "│\n"
    admin_user=$(ask_input "User admin (root/sudo)" "root")
    printf "│\n└──────────────────────────────────────────────────────\n"

    # ═════════════════════════════════════════════════════════════════════
    # FASE 1: PERSIAPAN LAPTOP
    # ═════════════════════════════════════════════════════════════════════
    printf "\n${BOLD}${CYAN}═══ Fase 1: Persiapan Laptop ═══${NC}\n\n"

    # SSH key
    local odin_key_path="$HOME/.ssh/odin_agent"
    printf "  ${BLUE}▸${NC} SSH key... "
    if [ -f "$odin_key_path" ]; then
        printf "${GREEN}✓${NC} sudah ada: ${CYAN}%s${NC}\n" "$odin_key_path"
    else
        mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
        if ssh-keygen -t ed25519 -f "$odin_key_path" -N "" -C "odin-mcp-agent" >/dev/null 2>&1; then
            ok "Key pair di-generate: ${odin_key_path}"
        else
            warn "Gagal generate SSH key"
            odin_key_path=""
        fi
    fi

    # MCP config
    local mcp_target="odin@${server_host}"
    printf "  ${BLUE}▸${NC} MCP config... "
    if write_mcp_config "$mcp_target" "/home/odin/run.sh" "$admin_port" "false" "$odin_key_path"; then
        ok "~/.claude.json → mcpServers.odin"
    else
        err "Gagal menulis ~/.claude.json"
        FULL_DONE=false; return
    fi

    # Guard hook (always global)
    printf "  ${BLUE}▸${NC} Guard hook... "
    if write_guard_config "$settings_path" "$guard_path"; then
        ok "~/.claude/settings.json → PreToolUse + PostToolUse"
    else
        err "Gagal menulis guard config"
        FULL_DONE=false; return
    fi

    ok "Laptop siap"

    # ═════════════════════════════════════════════════════════════════════
    # FASE 2: SETUP SERVER
    # ═════════════════════════════════════════════════════════════════════
    printf "\n${BOLD}${CYAN}═══ Fase 2: Setup Server ═══${NC}\n\n"

    local admin_target="${admin_user}@${server_host}"
    if ! ssh_open "$admin_target" "$admin_port"; then
        err "Gagal koneksi SSH — periksa IP, port, user, dan password."
        FULL_DONE=false; return
    fi
    trap 'ssh_close' EXIT

    local pp=""
    local remote_user
    remote_user=$(ssh_run "whoami" 2>/dev/null) || remote_user="unknown"
    if [ "$remote_user" = "root" ]; then
        ok "Terhubung sebagai root"
    else
        pp="sudo "
        info "Terhubung sebagai $remote_user — menggunakan sudo"
    fi

    # 1. Python
    printf "  ${BLUE}▸${NC} Python... "
    local srv_py
    if srv_py=$(ssh_run "python3 --version" 2>&1); then
        printf "${GREEN}✓${NC} %s\n" "$srv_py"
    else
        printf "${YELLOW}belum ada${NC} — menginstall...\n"
        if ssh_run "${pp}apt-get update -qq && ${pp}apt-get install -y -qq python3 python3-venv" >/dev/null 2>&1; then
            srv_py=$(ssh_run "python3 --version" 2>&1)
            ok "  Python terinstall: $srv_py"
        else
            err "  Gagal install Python 3"
            ssh_close; trap - EXIT; FULL_DONE=false; return
        fi
    fi

    # 2. python3-venv
    if ! ssh_run "python3 -c 'import venv'" >/dev/null 2>&1; then
        printf "  ${BLUE}▸${NC} python3-venv... "
        if ssh_run "${pp}apt-get install -y -qq python3-venv" >/dev/null 2>&1; then
            printf "${GREEN}✓${NC}\n"
        else
            printf "${YELLOW}⚠${NC} gagal\n"
        fi
    fi

    # 3. User odin
    printf "  ${BLUE}▸${NC} User odin... "
    if ssh_run "id odin" >/dev/null 2>&1; then
        printf "${GREEN}✓${NC} sudah ada\n"
    else
        printf "membuat... "
        if ssh_run "${pp}useradd -m -s /bin/bash odin" 2>/dev/null; then
            printf "${GREEN}✓${NC}\n"
        else
            printf "${RED}✗${NC}\n"
            err "  Gagal membuat user odin"
            ssh_close; trap - EXIT; FULL_DONE=false; return
        fi
    fi

    # 3b. Sudoers
    printf "  ${BLUE}▸${NC} Sudoers odin... "
    if ssh_run "${pp}test -f /etc/sudoers.d/odin" >/dev/null 2>&1; then
        printf "${GREEN}✓${NC} sudah ada\n"
    else
        ssh_run "cat | ${pp}tee /etc/sudoers.d/odin > /dev/null" <<'SUDOEOF'
# ODIN MCP Agent — limited privileges
odin ALL=(root) NOPASSWD: /usr/bin/systemctl status *
odin ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx, \
                          /usr/bin/systemctl reload nginx, \
                          /usr/bin/systemctl restart php*-fpm, \
                          /usr/bin/systemctl reload php*-fpm, \
                          /usr/bin/systemctl restart mysql, \
                          /usr/bin/systemctl restart supervisor, \
                          /usr/bin/systemctl restart redis-server
odin ALL=(root) NOPASSWD: /usr/bin/tail -n * /var/log/*
odin ALL=(root) NOPASSWD: /usr/bin/journalctl *
odin ALL=(root) NOPASSWD: /usr/bin/certbot renew *
odin ALL=(root) NOPASSWD: /usr/bin/df *, /usr/bin/free *, /usr/sbin/nginx -t, /usr/bin/ufw status *
SUDOEOF
        if ssh_run "${pp}chmod 440 /etc/sudoers.d/odin && ${pp}visudo -cf /etc/sudoers.d/odin" >/dev/null 2>&1; then
            printf "${GREEN}✓${NC}\n"
        else
            printf "${YELLOW}⚠${NC}\n"
            warn "  Gagal validasi sudoers"
            ssh_run "${pp}rm -f /etc/sudoers.d/odin" 2>/dev/null || true
        fi
    fi

    # 4. Direktori
    printf "  ${BLUE}▸${NC} Direktori... "
    ssh_run "${pp}mkdir -p /home/odin/memory" 2>/dev/null || true
    ssh_run "${pp}chown -R odin:odin /home/odin" 2>/dev/null || true
    ssh_run "${pp}chmod 700 /home/odin/memory" 2>/dev/null || true
    printf "${GREEN}✓${NC}\n"

    # 5. Auto-detect PROJECT_ROOT
    printf "  ${BLUE}▸${NC} Deteksi aplikasi... "
    local project_root="" detected_app="" app_type=""
    detected_app=$(ssh_run "for d in /var/www/*/; do [ -f \"\${d}artisan\" ] && echo \"\${d%/}|Laravel\" && exit 0; [ -f \"\${d}manage.py\" ] && echo \"\${d%/}|Django\" && exit 0; [ -f \"\${d}package.json\" ] && echo \"\${d%/}|Node\" && exit 0; done; echo '/var/www/html|default'" 2>/dev/null) || detected_app="/var/www/html|default"
    project_root="${detected_app%%|*}"
    app_type="${detected_app##*|}"
    if [ "$app_type" != "default" ]; then
        printf "${GREEN}✓${NC} ${CYAN}%s${NC} (%s)\n" "$project_root" "$app_type"
    else
        printf "${CYAN}%s${NC} (default)\n" "$project_root"
    fi
    local log_dirs="/var/log,${project_root}"

    # 6. Venv + mcp[cli]
    printf "  ${BLUE}▸${NC} Venv & mcp[cli]... "
    if ssh_run "/home/odin/.venv/bin/python -c 'import mcp'" >/dev/null 2>&1; then
        printf "${GREEN}✓${NC} sudah ada\n"
    else
        printf "${YELLOW}install${NC}\n"
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
            ssh_close; trap - EXIT; FULL_DONE=false; return
        fi
        ssh_run "${pp}chown odin:odin /tmp/odin_venv.sh" 2>/dev/null || true
        local venv_out
        if venv_out=$(ssh_run "${pp}su - odin -c '/tmp/odin_venv.sh'" 2>&1); then
            ok "    Venv & mcp[cli] terinstall"
        else
            err "    Gagal install venv/mcp[cli]"
            [ -n "$venv_out" ] && printf "    %s\n" "${venv_out:0:200}"
            ssh_run "rm -f /tmp/odin_venv.sh" 2>/dev/null || true
            ssh_close; trap - EXIT; FULL_DONE=false; return
        fi
        ssh_run "rm -f /tmp/odin_venv.sh" 2>/dev/null || true
    fi

    # 7. Upload odin_agent.py
    printf "  ${BLUE}▸${NC} Upload odin_agent.py... "
    if ssh_upload "$INSTALL_DIR/server/odin_agent.py" "/tmp/odin_agent.py"; then
        ssh_run "${pp}mv /tmp/odin_agent.py /home/odin/odin_agent.py && ${pp}chown odin:odin /home/odin/odin_agent.py && ${pp}chmod 600 /home/odin/odin_agent.py" 2>/dev/null
        printf "${GREEN}✓${NC}\n"
    else
        printf "${RED}✗${NC}\n"
        err "  Gagal upload odin_agent.py"
        ssh_close; trap - EXIT; FULL_DONE=false; return
    fi

    # 8. Generate & upload run.sh
    printf "  ${BLUE}▸${NC} Generate run.sh... "
    local tmp_run
    tmp_run=$(mktemp)
    cat > "$tmp_run" <<RUNEOF
#!/usr/bin/env bash
export DEPLOY_MODE=local
export PROJECT_ROOT=${project_root}
export ALLOWED_LOG_DIRS=${log_dirs}
export MEMORY_DIR=/home/odin/memory
cd "\$PROJECT_ROOT" || { echo "FATAL: \$PROJECT_ROOT tidak bisa diakses" >&2; exit 1; }
exec /home/odin/.venv/bin/python /home/odin/odin_agent.py
RUNEOF
    if ssh_upload "$tmp_run" "/tmp/odin_run.sh"; then
        ssh_run "${pp}mv /tmp/odin_run.sh /home/odin/run.sh && ${pp}chown odin:odin /home/odin/run.sh && ${pp}chmod 755 /home/odin/run.sh" 2>/dev/null
        printf "${GREEN}✓${NC}\n"
    else
        printf "${RED}✗${NC}\n"
        err "  Gagal upload run.sh"
    fi
    rm -f "$tmp_run"

    # 9. Install SSH public key
    if [ -n "$odin_key_path" ] && [ -f "${odin_key_path}.pub" ]; then
        printf "  ${BLUE}▸${NC} SSH key ke server... "
        local pubkey
        pubkey=$(cat "${odin_key_path}.pub")
        if ssh_run "${pp}mkdir -p /home/odin/.ssh && echo '$pubkey' | ${pp}tee /home/odin/.ssh/authorized_keys > /dev/null && ${pp}chmod 700 /home/odin/.ssh && ${pp}chmod 600 /home/odin/.ssh/authorized_keys && ${pp}chown -R odin:odin /home/odin/.ssh" 2>/dev/null; then
            printf "${GREEN}✓${NC}\n"
        else
            printf "${YELLOW}⚠${NC} gagal install key\n"
            odin_key_path=""
        fi
    fi

    # 10. Verifikasi file
    printf "  ${BLUE}▸${NC} Verifikasi file... "
    local all_ok=true
    ssh_run "test -f /home/odin/odin_agent.py" 2>/dev/null || all_ok=false
    ssh_run "test -f /home/odin/run.sh" 2>/dev/null || all_ok=false
    ssh_run "test -d /home/odin/.venv" 2>/dev/null || all_ok=false
    ssh_run "test -d /home/odin/memory" 2>/dev/null || all_ok=false
    if [ "$all_ok" = true ]; then
        printf "${GREEN}✓${NC} semua OK\n"
    else
        printf "${YELLOW}⚠${NC} ada yang kurang\n"
    fi

    # Tutup SSH admin
    ssh_close
    trap - EXIT

    ok "Server siap"

    # ═════════════════════════════════════════════════════════════════════
    # FASE 3: TEST MCP
    # ═════════════════════════════════════════════════════════════════════
    printf "\n${BOLD}${CYAN}═══ Fase 3: Test MCP ═══${NC}\n\n"

    local e2e_args=(-o ConnectTimeout=10 -o BatchMode=yes)
    if [ -n "$odin_key_path" ]; then
        e2e_args+=(-o IdentitiesOnly=yes -i "$odin_key_path")
    fi
    [ "$admin_port" != "22" ] && e2e_args+=(-p "$admin_port")
    e2e_args+=("odin@${server_host}")

    printf "  ${BLUE}▸${NC} ${CYAN}ssh %s${NC}\n" "${e2e_args[*]}"

    local e2e_result
    e2e_result=$(ssh "${e2e_args[@]}" "echo MCP_OK && test -x /home/odin/run.sh && echo RUN_OK && python3 --version && /home/odin/.venv/bin/python -c 'import mcp; print(\"MCP_MODULE_OK\")'" 2>&1)
    local e2e_rc=$?

    if [ $e2e_rc -eq 0 ] && echo "$e2e_result" | grep -q "MCP_OK"; then
        ok "SSH sebagai odin berhasil"
        echo "$e2e_result" | grep -q "RUN_OK" && ok "run.sh executable"
        local py_ver
        py_ver=$(echo "$e2e_result" | grep -i "python" | head -1)
        [ -n "$py_ver" ] && ok "$py_ver"
        echo "$e2e_result" | grep -q "MCP_MODULE_OK" && ok "mcp module OK"
        printf "\n  ${GREEN}${BOLD}MCP siap!${NC} Buka Claude Code dan coba:\n"
        printf "    ${CYAN}\"cek status server\"${NC}\n"
    else
        warn "SSH sebagai odin gagal — MCP belum bisa berjalan."
        if echo "$e2e_result" | grep -qi "permission denied"; then
            printf "  ${YELLOW}⚠${NC}  SSH key belum terinstall dengan benar.\n"
        else
            printf "  ${YELLOW}⚠${NC}  Error: %s\n" "${e2e_result:0:150}"
        fi
        printf "\n  ${BLUE}ℹ${NC}  Debug:\n"
        local dbg_args=(-v -o ConnectTimeout=10)
        [ -n "$odin_key_path" ] && dbg_args+=(-i "$odin_key_path")
        [ "$admin_port" != "22" ] && dbg_args+=(-p "$admin_port")
        dbg_args+=("odin@${server_host}")
        printf "     ${CYAN}ssh %s echo ok${NC}\n" "${dbg_args[*]}"
    fi

    FULL_DONE=true
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    banner
    detect_os
    info "Terdeteksi: $OS ($(uname -m))"
    check_prereqs
    install_odin
    install_cli_deps
    install_cli_command
    install_guard_hook
    install_updater

    local version
    version=$(grep -m1 '__version__' "$INSTALL_DIR/server/odin_agent.py" | cut -d'"' -f2)

    printf "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${GREEN}  ✓ ODIN v${version} terinstall${NC}\n"
    printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"

    # Cek apakah sudah ada server terdaftar (v2.0 style)
    local has_servers=false
    if [ -d "$HOME/.odin/servers" ] && ls "$HOME/.odin/servers"/*.yaml >/dev/null 2>&1; then
        has_servers=true
    fi

    # Cek apakah ada config legacy v1.x
    local has_legacy=false
    if detect_existing_config; then
        has_legacy=true
    fi

    if [ "$has_servers" = true ]; then
        printf "  ${GREEN}✓${NC} Server sudah terdaftar.\n\n"
        printf "  Kelola server & project:\n"
        printf "    ${CYAN}odin server list${NC}        — daftar server\n"
        printf "    ${CYAN}odin project list${NC}       — daftar project\n"
        printf "    ${CYAN}odin server add${NC}         — tambah server baru\n"
        printf "    ${CYAN}odin project add${NC}        — tambah project baru\n\n"
    elif [ "$has_legacy" = true ]; then
        printf "  ${YELLOW}⚠${NC}  Konfigurasi ODIN v1.x terdeteksi:\n"
        printf "      Target : ${CYAN}%s${NC}\n" "$EXISTING_SSH_HOST"
        printf "      Path   : ${CYAN}%s${NC}\n\n" "$EXISTING_RUN_PATH"
        printf "  ODIN v2.0 menggunakan 'odin server add' & 'odin project add'.\n"
        printf "  Konfigurasi lama tetap berjalan. Migrasi ke v2.0:\n\n"
        printf "    ${CYAN}odin server add${NC}         — register server baru\n"
        printf "    ${CYAN}odin project add${NC}        — link workdir ke server\n\n"
    else
        printf "  Langkah selanjutnya:\n\n"
        printf "    ${CYAN}1.${NC} Setup server       ${CYAN}odin server add${NC}\n"
        printf "    ${CYAN}2.${NC} Tambah project     ${CYAN}odin project add${NC}\n"
        printf "    ${CYAN}3.${NC} Mulai bekerja      ${CYAN}cd ~/project && claude${NC}\n\n"

        printf "${BOLD}  Setup server sekarang? [Y/n]${NC}: "
        local do_setup
        read -r do_setup <&"$TTY_FD"
        if [[ ! "$do_setup" =~ ^[Nn]$ ]]; then
            printf "\n"
            local odin_cmd="$INSTALL_DIR/client/odin_cli.py"
            if command -v odin >/dev/null 2>&1; then
                odin server add
            elif [ -x "$odin_cmd" ]; then
                "$PYTHON" "$odin_cmd" server add
            else
                warn "CLI odin tidak ditemukan — jalankan manual: python3 $odin_cmd server add"
            fi
        fi
    fi

    printf "\n  Update:  ${CYAN}odin-update${NC}\n"
    printf "  Docs:    ${CYAN}https://github.com/${REPO}${NC}\n\n"
}

main "$@"
[ "$TTY_FD" = 3 ] && exec 3<&- 2>/dev/null || true
