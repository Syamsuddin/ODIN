#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Uninstaller — macOS & Linux
# Usage: curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/uninstall.sh | bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

INSTALL_DIR="${ODIN_INSTALL_DIR:-$HOME/.odin}"
BIN_LINK="/usr/local/bin/odin-update"
CLAUDE_JSON="$HOME/.claude.json"
GLOBAL_SETTINGS="$HOME/.claude/settings.json"
ODIN_MODE_FILE="$HOME/.odin_mode"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC} %s\n" "$*"; }

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

printf "\n${BOLD}${CYAN}  ODIN Uninstaller${NC}\n\n"

if [ ! -d "$INSTALL_DIR" ]; then
    warn "ODIN tidak ditemukan di $INSTALL_DIR — tidak ada yang perlu dihapus."
    exit 0
fi

printf "${YELLOW}Ini akan menghapus:${NC}\n"
printf "  • ${CYAN}%s${NC} (source, venv, seluruh isi)\n" "$INSTALL_DIR"
printf "  • ${CYAN}%s${NC} (symlink, jika ada)\n" "$BIN_LINK"
[ -f "$ODIN_MODE_FILE" ] && printf "  • ${CYAN}%s${NC}\n" "$ODIN_MODE_FILE"
printf "  • Entry ${CYAN}mcpServers.odin${NC} dari ~/.claude.json\n"
printf "  • Hook ${CYAN}mcp__odin__${NC} dari settings.json\n"
printf "\n"
printf "${BOLD}Lanjutkan? [y/N]${NC} "
read -r confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    info "Dibatalkan."
    exit 0
fi

# ── Hapus direktori ODIN ───────────────────────────────────────────────────
info "Menghapus $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"
ok "Direktori ODIN dihapus"

# ── Hapus symlink ──────────────────────────────────────────────────────────
if [ -L "$BIN_LINK" ]; then
    if [ -w "$(dirname "$BIN_LINK")" ] 2>/dev/null; then
        rm -f "$BIN_LINK"
    else
        sudo rm -f "$BIN_LINK" 2>/dev/null || true
    fi
    ok "Symlink $BIN_LINK dihapus"
fi

# ── Hapus ~/.odin_mode ─────────────────────────────────────────────────────
if [ -f "$ODIN_MODE_FILE" ]; then
    rm -f "$ODIN_MODE_FILE"
    ok "$ODIN_MODE_FILE dihapus"
fi

# ── Bersihkan ~/.claude.json (hapus mcpServers.odin) ───────────────────────
if [ -n "$PYTHON" ] && [ -f "$CLAUDE_JSON" ]; then
    info "Membersihkan mcpServers.odin dari ~/.claude.json..."
    if "$PYTHON" -c "
import json, sys
try:
    with open('$CLAUDE_JSON') as f:
        data = json.load(f)
    mcp = data.get('mcpServers', {})
    removed = []
    for name in ('odin', 'deploy-agent'):
        if name in mcp:
            del mcp[name]
            removed.append(name)
    if removed:
        with open('$CLAUDE_JSON', 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        print(','.join(removed))
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        ok "mcpServers.odin dihapus dari ~/.claude.json"
    else
        info "mcpServers.odin tidak ditemukan — skip"
    fi
else
    [ ! -f "$CLAUDE_JSON" ] && info "~/.claude.json tidak ada — skip"
fi

# ── Bersihkan settings.json (hapus hook & permissions odin) ────────────────
clean_settings() {
    local settings_path="$1"
    [ ! -f "$settings_path" ] && return 1
    [ -z "$PYTHON" ] && return 1

    "$PYTHON" -c "
import json, sys
try:
    with open('$settings_path') as f:
        data = json.load(f)
    changed = False

    # Hapus permissions odin
    perms = data.get('permissions', {})
    allow = perms.get('allow', [])
    new_allow = [p for p in allow if not p.startswith('mcp__odin__')]
    if len(new_allow) != len(allow):
        perms['allow'] = new_allow
        changed = True

    # Hapus hooks odin
    hooks = data.get('hooks', {})
    pre = hooks.get('PreToolUse', [])
    new_pre = [h for h in pre if not (isinstance(h, dict) and 'mcp__odin__' in h.get('matcher', ''))]
    if len(new_pre) != len(pre):
        hooks['PreToolUse'] = new_pre
        changed = True

    if changed:
        with open('$settings_path', 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        sys.exit(0)
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

info "Membersihkan hook & permissions odin dari settings.json..."
cleaned=false
if clean_settings "$GLOBAL_SETTINGS"; then
    ok "Global settings (~/.claude/settings.json) dibersihkan"
    cleaned=true
fi

# Cari project settings.json yang mungkin ada
for proj_settings in "$HOME"/.claude/projects/*/settings.json; do
    [ -f "$proj_settings" ] || continue
    if clean_settings "$proj_settings"; then
        ok "Project settings ($proj_settings) dibersihkan"
        cleaned=true
    fi
done

if [ "$cleaned" = false ]; then
    info "Tidak ada hook/permissions odin ditemukan — skip"
fi

# ── Tawarkan cleanup server ────────────────────────────────────────────────
printf "\n${BOLD}Hapus ODIN dari server juga? [y/N]${NC} "
read -r do_server
if [[ "$do_server" =~ ^[Yy]$ ]]; then
    printf "\n"
    printf "  ${BOLD}SSH user@host${NC} server (contoh: root@192.168.1.100): "
    read -r server_host
    if [ -n "$server_host" ]; then
        info "Menghubungi $server_host..."
        if ssh -o ConnectTimeout=10 "$server_host" \
            "rm -rf /home/odin/deploy_agent.py /home/odin/run.sh /home/odin/.venv /home/odin/memory" 2>/dev/null; then
            ok "File ODIN di server dihapus"
            printf "    ${YELLOW}⚠${NC} User odin masih ada — hapus manual: ${CYAN}sudo userdel -r odin${NC}\n"
        else
            warn "Gagal menghapus — hapus manual di server:"
            printf "    ${CYAN}rm -rf /home/odin/deploy_agent.py /home/odin/run.sh /home/odin/.venv /home/odin/memory${NC}\n"
        fi
    fi
fi

printf "\n${GREEN}✓ ODIN berhasil di-uninstall.${NC}\n\n"
