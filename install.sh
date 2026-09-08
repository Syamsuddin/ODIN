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
ODIN_VENV_PY=""     # diisi install_cli_deps bila dependensi masuk ke venv

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
            # JANGAN `mv "$INSTALL_DIR"`. Folder ini juga rumah state CLI —
            # keys/ servers/ projects/ modes/. Memindahkannya membuat semua
            # private key hilang dari jalur yang masih ditunjuk ~/.ssh/config,
            # dan LogLevel QUIET menyembunyikan kegagalannya dari Claude Code.
            info "$INSTALL_DIR sudah ada (bukan git repo) — clone di tempat, state dipertahankan"
            local tmp_clone
            tmp_clone="$(mktemp -d)"
            if ! git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$tmp_clone/repo" --quiet 2>&1; then
                rm -rf "$tmp_clone"
                fatal "Gagal clone dari GitHub. Cek koneksi internet."
            fi
            # Pindahkan repo (termasuk .git) ke INSTALL_DIR tanpa menyentuh state.
            (cd "$tmp_clone/repo" && tar cf - .) | (cd "$INSTALL_DIR" && tar xf -)
            rm -rf "$tmp_clone"
            ok "ODIN berhasil diunduh (state CLI di $INSTALL_DIR dipertahankan)"
        else
            if ! git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" --quiet 2>&1; then
                fatal "Gagal clone dari GitHub. Cek koneksi internet."
            fi
            ok "ODIN berhasil diunduh"
        fi
    fi
}

# ── Slash command /odin:* ke ~/.claude/commands ───────────────────────────
install_slash_commands() {
    local src="$INSTALL_DIR/.claude/commands/odin"
    local dst="$HOME/.claude/commands/odin"
    if [ ! -d "$src" ]; then
        return
    fi
    # Tanpa langkah ini, /odin:status hanya bekerja saat Claude Code dijalankan
    # DARI DALAM repo ODIN — Claude Code hanya membaca command dari cwd project
    # atau dari ~/.claude/commands.
    mkdir -p "$dst"
    cp -f "$src"/*.md "$dst"/ 2>/dev/null && \
        ok "Slash command /odin:* terpasang di ~/.claude/commands/odin" || \
        warn "Gagal menyalin slash command ke $dst"
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
        return
    fi
    # PEP 668 (Debian 12, Ubuntu 23.04+, Homebrew): install ke Python sistem
    # ditolak dengan 'externally-managed-environment'. Pakai venv sendiri, lalu
    # `odin` dijalankan dengan interpreter venv itu (lihat install_cli_command).
    info "Python ini externally-managed — memakai venv di $INSTALL_DIR/.venv"
    if $PYTHON -m venv "$INSTALL_DIR/.venv" 2>/dev/null && \
       "$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$req" 2>/dev/null; then
        ODIN_VENV_PY="$INSTALL_DIR/.venv/bin/python"
        ok "Dependensi CLI terinstall di venv"
    else
        warn "Gagal install dependensi CLI — 'odin server add' butuh: paramiko, pyyaml"
        warn "Coba manual: $PYTHON -m venv $INSTALL_DIR/.venv && $INSTALL_DIR/.venv/bin/pip install -r $req"
    fi
}

# ── Install odin CLI command ──────────────────────────────────────────────
install_cli_command() {
    local cli="$INSTALL_DIR/client/odin_cli.py"
    if [ ! -f "$cli" ]; then
        return
    fi
    chmod +x "$cli"
    # Wrapper, bukan symlink: dependensi CLI bisa berada di venv (PEP 668), dan
    # symlink ke skrip .py akan memakai interpreter sistem yang tak punya paramiko.
    local wrapper="$INSTALL_DIR/odin-cli.sh"
    local py="${ODIN_VENV_PY:-$PYTHON}"
    cat > "$wrapper" <<WRAPPER
#!/usr/bin/env bash
exec "$py" "$cli" "\$@"
WRAPPER
    chmod +x "$wrapper"
    local bin_link="/usr/local/bin/odin"
    if [ -w "$(dirname "$bin_link")" ] 2>/dev/null; then
        ln -sf "$wrapper" "$bin_link" 2>/dev/null && \
            ok "Perintah 'odin' tersedia di PATH" || true
    else
        if command -v sudo >/dev/null 2>&1; then
            sudo ln -sf "$wrapper" "$bin_link" 2>/dev/null && \
                ok "Perintah 'odin' tersedia di PATH" || \
                warn "Jalankan manual: $wrapper"
        else
            warn "Tidak bisa buat symlink — jalankan manual: $wrapper"
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
    install_slash_commands
    install_updater

    local version
    version=$(grep -m1 '__version__' "$INSTALL_DIR/server/odin_agent.py" | cut -d'"' -f2)

    printf "\n${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${BOLD}${GREEN}  ✓ ODIN v${version} terinstall${NC}\n"
    printf "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n\n"
    printf "  ${BOLD}Fitur:${NC}\n"
    printf "    • ${CYAN}Continuous Learning${NC} — belajar dari error & sukses\n"
    printf "    • ${CYAN}Orchestrator${NC} — auto-recall memory relevan\n"
    printf "    • ${CYAN}Cortex${NC} — consciousness lintas-project\n"
    printf "    • ${CYAN}20 MCP tools${NC}, ${CYAN}4 memory namespace${NC}\n\n"

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
            # `curl | bash`: stdin skrip ini adalah PIPE. Tanpa </dev/tty, input()
            # di Python membaca sisa skrip/EOF dan wizard langsung gagal.
            if command -v odin >/dev/null 2>&1; then
                odin server add </dev/tty
            elif [ -x "$odin_cmd" ]; then
                "$PYTHON" "$odin_cmd" server add </dev/tty
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
