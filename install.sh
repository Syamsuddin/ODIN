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
    ╔═══════════════════════════════════════╗
    ║                                       ║
    ║     ⚡  O D I N  Installer  ⚡       ║
    ║     MCP Deploy Agent for Claude Code  ║
    ║                                       ║
    ╚═══════════════════════════════════════╝
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
    local guard="$hooks_dir/deploy_agent_guard.py"

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
NEW_VER=$(grep -m1 '__version__' server/deploy_agent.py | cut -d'"' -f2)
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
    for name in ('odin', 'deploy-agent'):
        entry = mcp.get(name)
        if entry and entry.get('args'):
            args = entry['args']
            host = args[0] if len(args) > 0 else ''
            path = args[1] if len(args) > 1 else ''
            print(f'{name}|{host}|{path}')
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

# Hapus entry lama jika ada
mcp.pop('deploy-agent', None)

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

# Hapus hook odin/deploy-agent lama
pre[:] = [h for h in pre if not (isinstance(h, dict) and 'mcp__odin__' in h.get('matcher', '') or 'mcp__deploy-agent__' in h.get('matcher', ''))]
pre.append(ODIN_HOOK)

with open(settings_path, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
"
}

# ── Setup Wizard ───────────────────────────────────────────────────────────
setup_wizard() {
    local ssh_host="" run_path="" guard_scope="" project_path="" settings_path=""
    local guard_path="$INSTALL_DIR/client/deploy_agent_guard.py"
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
    run_path=$(ask_input "Path run.sh" "${run_path:-/home/deploy/agent/run.sh}")
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

# ── Tampilkan konfigurasi yang perlu user pasang ────────────────────────────
show_config_guide() {
    local guard_path="$INSTALL_DIR/client/deploy_agent_guard.py"
    local version
    version=$(grep -m1 '__version__' "$INSTALL_DIR/server/deploy_agent.py" | cut -d'"' -f2)

    printf "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${GREEN}  ✓ ODIN v${version} berhasil diinstall!${NC}\n"
    printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

    printf "\n${BOLD}📁 Lokasi:${NC}\n"
    printf "   Instalasi : ${CYAN}%s${NC}\n" "$INSTALL_DIR"
    printf "   Guard     : ${CYAN}%s${NC}\n" "$guard_path"
    printf "   Server    : ${CYAN}%s${NC}\n" "$INSTALL_DIR/server/deploy_agent.py"

    printf "\n${BOLD}📋 Langkah selanjutnya:${NC}\n"
    printf "\n${YELLOW}1.${NC} ${BOLD}Setup server (di VPS):${NC}\n"
    cat <<EOF
   Salin file ke server:
     scp ${INSTALL_DIR}/server/deploy_agent.py  user@server:/home/deploy/agent/
     scp ${INSTALL_DIR}/server/run.sh            user@server:/home/deploy/agent/

   Di server, buat venv & install dependensi:
     python3 -m venv /home/deploy/agent/.venv
     /home/deploy/agent/.venv/bin/pip install "mcp[cli]"
EOF

    printf "\n${YELLOW}2.${NC} ${BOLD}Konfigurasi MCP (Claude Code):${NC}\n"
    printf "   Tambahkan ke ${CYAN}~/.claude.json${NC} (atau project .claude.json):\n\n"
    cat <<EOF
   {
     "mcpServers": {
       "odin": {
         "type": "stdio",
         "command": "ssh",
         "args": ["your-server-alias", "/home/deploy/agent/run.sh"]
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

    if [ "$WIZARD_DONE" = true ]; then
        local version
        version=$(grep -m1 '__version__' "$INSTALL_DIR/server/deploy_agent.py" | cut -d'"' -f2)

        printf "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
        printf "${BOLD}${GREEN}  ✓ ODIN v${version} siap digunakan!${NC}\n"
        printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"
        printf "  Buka Claude Code dan coba:\n"
        printf "    ${CYAN}\"cek status server\"${NC}\n\n"
        printf "  Update kapan saja:\n"
        printf "    ${CYAN}odin-update${NC}\n\n"
        printf "  Dokumentasi:\n"
        printf "    ${CYAN}https://github.com/${REPO}${NC}\n\n"
    else
        show_config_guide
    fi
}

main "$@"
