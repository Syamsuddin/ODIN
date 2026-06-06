#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Uninstaller — macOS & Linux
# Usage: curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/uninstall.sh | bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

INSTALL_DIR="${ODIN_INSTALL_DIR:-$HOME/.odin}"
BIN_LINK="/usr/local/bin/odin-update"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC} %s\n" "$*"; }

printf "\n${BOLD}${CYAN}  ⚡ ODIN Uninstaller${NC}\n\n"

if [ ! -d "$INSTALL_DIR" ]; then
    warn "ODIN tidak ditemukan di $INSTALL_DIR — tidak ada yang perlu dihapus."
    exit 0
fi

printf "${YELLOW}Ini akan menghapus:${NC}\n"
printf "  • ${CYAN}%s${NC} (source, venv, seluruh isi)\n" "$INSTALL_DIR"
printf "  • ${CYAN}%s${NC} (symlink, jika ada)\n" "$BIN_LINK"
printf "\n"
printf "${BOLD}Lanjutkan? [y/N]${NC} "
read -r confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    info "Dibatalkan."
    exit 0
fi

info "Menghapus $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"
ok "Direktori ODIN dihapus"

if [ -L "$BIN_LINK" ]; then
    if [ -w "$(dirname "$BIN_LINK")" ] 2>/dev/null; then
        rm -f "$BIN_LINK"
    else
        sudo rm -f "$BIN_LINK" 2>/dev/null || true
    fi
    ok "Symlink $BIN_LINK dihapus"
fi

printf "\n${GREEN}✓ ODIN berhasil di-uninstall.${NC}\n"
printf "${YELLOW}⚠ Catatan:${NC} Hapus manual konfigurasi MCP & hooks dari:\n"
printf "  • ${CYAN}~/.claude.json${NC} (mcpServers.odin)\n"
printf "  • ${CYAN}.claude/settings.json${NC} project (hooks PreToolUse)\n"
printf "  • File di server tetap ada — hapus manual jika diperlukan.\n\n"
