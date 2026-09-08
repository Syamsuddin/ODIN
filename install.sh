#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Installer — macOS & Linux
#
#   curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.sh | bash
#
# Opsi (argumen atau env):
#   --yes / -y            non-interaktif (jawab default untuk semua prompt)
#   --no-setup            jangan tawarkan `odin setup` di akhir
#   --version <ref>       tag/branch/commit yang dipasang   (env: ODIN_VERSION)
#   --home <dir>          lokasi ODIN_HOME, default ~/.odin  (env: ODIN_HOME)
#
# Tata letak (kode dan STATE dipisah — installer tak pernah memindahkan state):
#   ~/.odin/app/          kode ODIN (git checkout) — diganti oleh `odin self-update`
#   ~/.odin/app/.venv/    dependensi CLI terisolasi (kebal PEP 668)
#   ~/.odin/bin/odin      wrapper CLI
#   ~/.odin/{keys,servers,projects,modes,ssh_config}   state milik user
#   ~/.local/bin/odin     symlink ke wrapper (tanpa sudo)
#
# Idempoten: dijalankan ulang = update. Tidak pernah memakai sudo.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

REPO="Syamsuddin/ODIN"
REPO_URL="${ODIN_REPO_URL:-https://github.com/${REPO}.git}"
ODIN_HOME="${ODIN_HOME:-$HOME/.odin}"
VERSION="${ODIN_VERSION:-}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
ASSUME_YES=0
RUN_SETUP=1
AUTO_VERSION=0   # 1 = versi dipilih otomatis dari GitHub Releases (bukan diminta user)

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)      ASSUME_YES=1 ;;
        --no-setup)    RUN_SETUP=0 ;;
        --version)     VERSION="${2:-}"; shift ;;
        --version=*)   VERSION="${1#*=}" ;;
        --home)        ODIN_HOME="${2:-}"; shift ;;
        --home=*)      ODIN_HOME="${1#*=}" ;;
        -h|--help)     sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Argumen tak dikenal: $1" >&2; exit 2 ;;
    esac
    shift
done

APP_DIR="$ODIN_HOME/app"
VENV_DIR="$APP_DIR/.venv"
BIN_DIR="$ODIN_HOME/bin"
LOCAL_BIN="$HOME/.local/bin"

# ── TTY untuk prompt (penting saat `curl | bash`: stdin adalah pipe) ────────
if [ -t 0 ]; then
    TTY_FD=0
elif [ "$ASSUME_YES" = 1 ]; then
    TTY_FD=""
else
    exec 3</dev/tty 2>/dev/null || { echo "ERROR: tidak bisa membuka /dev/tty. Gunakan --yes." >&2; exit 1; }
    TTY_FD=3
fi

# ── Output ──────────────────────────────────────────────────────────────────
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; DIM=''; NC=''
fi
info()  { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()   { printf "${RED}✗${NC} %s\n" "$*" >&2; }
fatal() { err "$*"; exit 1; }
step()  { printf "\n${BOLD}[%s/%s]${NC} %s\n" "$1" "$2" "$3"; }

ask_yn() {  # ask_yn "Pertanyaan" default(Y|N)
    local q="$1" def="${2:-Y}" ans=""
    if [ "$ASSUME_YES" = 1 ] || [ -z "$TTY_FD" ]; then
        [ "$def" = "Y" ]; return
    fi
    if [ "$def" = "Y" ]; then
        printf "${BOLD}%s${NC} [Y/n] " "$q"
    else
        printf "${BOLD}%s${NC} [y/N] " "$q"
    fi
    read -r ans <&"$TTY_FD" || ans=""
    case "$ans" in
        [Yy]*) return 0 ;;
        [Nn]*) return 1 ;;
        *) [ "$def" = "Y" ] ;;
    esac
}

banner() {
    printf "${BOLD}${CYAN}"
    cat <<'ART'

   ⚡ ODIN — MCP Agent AI for Claude Code
      Installer untuk macOS & Linux
ART
    printf "${NC}\n"
}

# ── 1. Prasyarat ────────────────────────────────────────────────────────────
PYTHON=""
find_python() {
    local c
    for c in python3 python3.13 python3.12 python3.11 python3.10 python; do
        if command -v "$c" >/dev/null 2>&1 && \
           "$c" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_PYTHON_MAJOR,$MIN_PYTHON_MINOR) else 1)" 2>/dev/null; then
            PYTHON="$(command -v "$c")"
            return 0
        fi
    done
    return 1
}

check_prereqs() {
    local os
    case "$(uname -s)" in
        Darwin) os="macOS" ;;
        Linux)  os="Linux" ;;
        *) fatal "OS tidak didukung: $(uname -s). Gunakan macOS, Linux, atau WSL." ;;
    esac
    ok "$os ($(uname -m))"

    command -v git >/dev/null 2>&1 || fatal "'git' tidak ditemukan. Install git lalu ulangi."
    ok "git $(git --version | awk '{print $3}')"

    find_python || fatal "Python >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} tidak ditemukan."
    ok "Python $("$PYTHON" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') ($PYTHON)"

    command -v ssh >/dev/null 2>&1 && ok "ssh tersedia" || warn "'ssh' tidak ditemukan — dibutuhkan saat menghubungkan server."

    if command -v claude >/dev/null 2>&1; then
        ok "Claude Code CLI tersedia"
    else
        warn "Claude Code CLI belum ada di PATH — ODIN tetap terpasang; install Claude Code sebelum 'odin setup'."
    fi
}

# ── 2. Versi ────────────────────────────────────────────────────────────────
resolve_version() {
    if [ -n "$VERSION" ]; then
        info "Versi diminta: $VERSION"
        return
    fi
    # Tag rilis terbaru dari GitHub; gagal/offline → main.
    local tag=""
    if command -v curl >/dev/null 2>&1; then
        tag="$(curl -fsSL --max-time 6 -H 'Accept: application/vnd.github+json' \
               "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
               | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)" || tag=""
    fi
    if [ -n "$tag" ]; then
        VERSION="$tag"
        AUTO_VERSION=1
        info "Versi rilis terbaru: $VERSION"
    else
        VERSION="main"
        info "Tidak ada info rilis (offline atau belum ada tag) — memakai branch main"
    fi
}

# ── 3. Migrasi tata letak lama (kode langsung di ~/.odin) ───────────────────
migrate_legacy_layout() {
    [ -d "$ODIN_HOME/.git" ] || return 0
    warn "Tata letak lama terdeteksi (kode di $ODIN_HOME) — memigrasikan ke $APP_DIR"
    # Hapus HANYA file yang dilacak git + .git; state (keys/servers/projects/modes)
    # tidak dilacak (dan di-gitignore), jadi otomatis selamat.
    local f
    git -C "$ODIN_HOME" ls-files -z 2>/dev/null | while IFS= read -r -d '' f; do
        rm -f "$ODIN_HOME/$f" 2>/dev/null || true
    done
    rm -rf "$ODIN_HOME/.git"
    # Direktori bekas kode: buang cache, hapus bila kosong; bila masih berisi
    # sesuatu yang tak dikenal, PINDAHKAN (jangan hapus) agar tak ada data hilang.
    local d keep="$ODIN_HOME/.legacy-$(date +%Y%m%d%H%M%S)"
    for d in client server tests examples assets docs .claude .pytest_cache __pycache__; do
        [ -e "$ODIN_HOME/$d" ] || continue
        [ -L "$ODIN_HOME/$d" ] && continue
        find "$ODIN_HOME/$d" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
        find "$ODIN_HOME/$d" -name '.DS_Store' -delete 2>/dev/null || true
        find "$ODIN_HOME/$d" -type d -empty -delete 2>/dev/null || true
        if [ -d "$ODIN_HOME/$d" ]; then
            mkdir -p "$keep" && mv "$ODIN_HOME/$d" "$keep/" && warn "Sisa tak dikenal dipindah ke $keep/$d"
        fi
    done
    rm -f "$ODIN_HOME/odin-update.sh" "$ODIN_HOME/odin-cli.sh" "$ODIN_HOME/.update-cache.json" 2>/dev/null || true
    [ -d "$ODIN_HOME/.venv" ] && rm -rf "$ODIN_HOME/.venv"
    ok "State dipertahankan: $(ls -d "$ODIN_HOME"/keys "$ODIN_HOME"/servers "$ODIN_HOME"/projects 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"

    # Symlink lama di /usr/local/bin menunjuk file yang baru dihapus.
    local link
    for link in /usr/local/bin/odin /usr/local/bin/odin-update; do
        if [ -L "$link" ] && case "$(readlink "$link")" in "$ODIN_HOME"/*) true ;; *) false ;; esac; then
            if rm -f "$link" 2>/dev/null; then
                ok "Symlink lama dihapus: $link"
            else
                warn "Symlink lama $link menunjuk file yang sudah tidak ada. Hapus manual: sudo rm $link"
            fi
        fi
    done
}

# ── 4. Kode ─────────────────────────────────────────────────────────────────
install_app() {
    mkdir -p "$ODIN_HOME"
    if [ -d "$APP_DIR/.git" ]; then
        info "Memperbarui $APP_DIR → $VERSION"
        git -C "$APP_DIR" fetch --quiet --tags origin 2>/dev/null \
            || fatal "Gagal fetch dari $REPO_URL. Cek koneksi internet."
    else
        info "Mengunduh ODIN → $APP_DIR"
        rm -rf "$APP_DIR"
        git clone --quiet "$REPO_URL" "$APP_DIR" 2>/dev/null \
            || fatal "Gagal clone $REPO_URL. Cek koneksi internet."
    fi
    # Checkout ref: tag/branch remote/commit. Branch → lacak origin/<branch>.
    if git -C "$APP_DIR" show-ref --verify --quiet "refs/remotes/origin/$VERSION"; then
        git -C "$APP_DIR" checkout --quiet -B "$VERSION" "origin/$VERSION"
    elif git -C "$APP_DIR" rev-parse --verify --quiet "$VERSION^{commit}" >/dev/null; then
        git -C "$APP_DIR" checkout --quiet --detach "$VERSION"
    else
        fatal "Ref '$VERSION' tidak ditemukan di $REPO_URL."
    fi
    # Rilis yang dipilih OTOMATIS tapi lebih tua dari installer ini (belum punya
    # `odin setup`) → jatuh ke main; kalau tidak, langkah berikutnya tak ada.
    if [ "$AUTO_VERSION" = 1 ] && ! grep -q "def cmd_setup" "$APP_DIR/client/odin_cli.py" 2>/dev/null; then
        warn "Rilis $VERSION lebih tua dari installer ini — memakai branch main"
        VERSION="main"
        git -C "$APP_DIR" checkout --quiet -B main origin/main
    fi
    local desc
    desc="$(git -C "$APP_DIR" describe --tags --always 2>/dev/null || true)"
    ok "Kode ODIN @ ${desc:-$VERSION}"
}

# ── 5. Dependensi CLI (venv terisolasi) ─────────────────────────────────────
install_deps() {
    if [ "${ODIN_SKIP_DEPS:-0}" = 1 ]; then
        warn "ODIN_SKIP_DEPS=1 — dependensi CLI dilewati (mode uji)"
        return
    fi
    local req="$APP_DIR/requirements-cli.txt"
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        info "Membuat venv $VENV_DIR"
        "$PYTHON" -m venv "$VENV_DIR" 2>/dev/null || {
            err "Gagal membuat venv. Di Debian/Ubuntu: sudo apt install python3-venv"
            fatal "Ulangi installer setelah python3-venv terpasang."
        }
    fi
    info "Menginstall dependensi CLI (paramiko, pyyaml)"
    "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip 2>/dev/null || true
    "$VENV_DIR/bin/python" -m pip install --quiet -r "$req" \
        || fatal "Gagal install dependensi. Coba manual: $VENV_DIR/bin/pip install -r $req"
    ok "Dependensi CLI terpasang di venv"
}

# ── 6. Wrapper + PATH ───────────────────────────────────────────────────────
install_wrapper() {
    mkdir -p "$BIN_DIR" "$LOCAL_BIN"
    local py="$VENV_DIR/bin/python"
    [ -x "$py" ] || py="$PYTHON"
    cat > "$BIN_DIR/odin" <<WRAP
#!/usr/bin/env bash
# ODIN CLI wrapper — dibuat oleh install.sh. Jangan edit; jalankan ulang installer.
export ODIN_HOME="$ODIN_HOME"
exec "$py" "$APP_DIR/client/odin_cli.py" "\$@"
WRAP
    chmod 755 "$BIN_DIR/odin"
    ln -sfn "$BIN_DIR/odin" "$LOCAL_BIN/odin"
    ok "Perintah: $LOCAL_BIN/odin"

    # Symlink kompatibilitas — hook & entry MCP dari versi lama menunjuk
    # ~/.odin/client/... dan ~/.odin/server/...; biarkan tetap valid.
    ln -sfn "$APP_DIR/client" "$ODIN_HOME/client"
    ln -sfn "$APP_DIR/server" "$ODIN_HOME/server"
}

ensure_path() {
    case ":$PATH:" in
        *":$LOCAL_BIN:"*) ok "$LOCAL_BIN sudah ada di PATH"; return ;;
    esac
    local shell_name rc line
    shell_name="$(basename "${SHELL:-bash}")"
    case "$shell_name" in
        zsh)  rc="$HOME/.zshrc";  line='export PATH="$HOME/.local/bin:$PATH"' ;;
        bash) rc="$HOME/.bashrc"; line='export PATH="$HOME/.local/bin:$PATH"'
              [ "$(uname -s)" = Darwin ] && [ -f "$HOME/.bash_profile" ] && rc="$HOME/.bash_profile" ;;
        fish) rc="$HOME/.config/fish/config.fish"; line='fish_add_path -g $HOME/.local/bin' ;;
        *)    rc="$HOME/.profile"; line='export PATH="$HOME/.local/bin:$PATH"' ;;
    esac
    if [ -f "$rc" ] && grep -Fq '.local/bin' "$rc"; then
        warn "$rc sudah menyebut ~/.local/bin — buka terminal baru agar 'odin' dikenali."
        return
    fi
    if ask_yn "Tambahkan ~/.local/bin ke PATH di $rc?" Y; then
        mkdir -p "$(dirname "$rc")"
        printf '\n# ODIN CLI\n%s\n' "$line" >> "$rc"
        ok "PATH ditambahkan ke $rc — buka terminal baru (atau: source $rc)"
    else
        warn "Tambahkan sendiri ke PATH: $line"
    fi
}

# ── 7. Slash command /odin:* ────────────────────────────────────────────────
install_slash_commands() {
    local src="$APP_DIR/.claude/commands/odin" dst="$HOME/.claude/commands/odin"
    [ -d "$src" ] || return 0
    mkdir -p "$dst"
    cp -f "$src"/*.md "$dst"/ 2>/dev/null && ok "Slash command /odin:* → $dst" || warn "Gagal menyalin slash command"
}

# ── 8. Verifikasi ───────────────────────────────────────────────────────────
verify() {
    local out
    if out="$("$BIN_DIR/odin" --version 2>&1)"; then
        ok "$out"
    else
        fatal "Wrapper gagal dijalankan: $out"
    fi
    if [ "${ODIN_SKIP_DEPS:-0}" != 1 ]; then
        "$VENV_DIR/bin/python" -c "import paramiko, yaml" 2>/dev/null \
            && ok "Modul paramiko & pyyaml siap" \
            || fatal "Modul paramiko/pyyaml tidak bisa di-import dari venv."
    fi
}

# ── Main ────────────────────────────────────────────────────────────────────
main() {
    banner
    step 1 6 "Prasyarat";        check_prereqs
    step 2 6 "Versi";            resolve_version
    # Clone dulu, baru bersihkan tata letak lama — bila clone gagal, kode lama masih ada.
    step 3 6 "Kode";             install_app; migrate_legacy_layout
    step 4 6 "Dependensi";       install_deps
    step 5 6 "Perintah & PATH";  install_wrapper; ensure_path; install_slash_commands
    step 6 6 "Verifikasi";       verify

    local ver
    ver="$(grep -m1 '__version__' "$APP_DIR/server/odin_agent.py" | cut -d'"' -f2)"
    printf "\n${BOLD}${GREEN}✓ ODIN v%s terpasang${NC}   ${DIM}%s${NC}\n\n" "$ver" "$ODIN_HOME"

    local has_servers=0
    ls "$ODIN_HOME"/servers/*.yaml >/dev/null 2>&1 && has_servers=1

    if [ "$has_servers" = 1 ]; then
        printf "  Server sudah terdaftar. Perintah berguna:\n"
        printf "    ${CYAN}odin doctor${NC}            cek laptop + server\n"
        printf "    ${CYAN}odin update <alias>${NC}    perbarui agent di server\n"
        printf "    ${CYAN}odin server harden <alias>${NC}   terapkan sudoers & kunci aman (server lama)\n\n"
        return
    fi

    printf "  Langkah berikutnya — satu perintah, wizard memandu sisanya:\n\n"
    printf "    ${CYAN}odin setup${NC}\n\n"
    if [ "$RUN_SETUP" = 1 ] && [ -n "$TTY_FD" ] && [ "$ASSUME_YES" = 0 ]; then
        if ask_yn "Jalankan 'odin setup' sekarang?" Y; then
            printf "\n"
            # stdin harus TTY: di `curl | bash` stdin adalah pipe skrip ini.
            "$BIN_DIR/odin" setup </dev/tty
        fi
    fi
}

main
[ "${TTY_FD:-}" = 3 ] && exec 3<&- 2>/dev/null || true
