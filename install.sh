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
    show_config_guide
}

main "$@"
