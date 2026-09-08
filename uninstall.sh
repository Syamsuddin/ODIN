#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Uninstaller — macOS & Linux
#
#   curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/uninstall.sh | bash
#   curl -fsSL .../uninstall.sh | bash -s -- --purge     # hapus juga kunci & registry
#   curl -fsSL .../uninstall.sh | bash -s -- --yes       # tanpa konfirmasi
#
# Normalnya cukup `odin uninstall` — skrip ini hanya pembungkus yang juga bisa
# membersihkan tata letak lama (ODIN <= v2.2, kode langsung di ~/.odin).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

ODIN_HOME="${ODIN_HOME:-$HOME/.odin}"
PURGE=0; YES=0
for a in "$@"; do
    case "$a" in
        --purge) PURGE=1 ;;
        -y|--yes) YES=1 ;;
        *) echo "Argumen tak dikenal: $a" >&2; exit 2 ;;
    esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
info() { printf "${BLUE}▸${NC} %s\n" "$*"; }
ok()   { printf "${GREEN}✓${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()  { printf "${RED}✗${NC} %s\n" "$*" >&2; }

# ── Jalur normal: CLI v2.3+ punya `odin uninstall` ──────────────────────────
if [ -x "$ODIN_HOME/bin/odin" ]; then
    args=()
    [ "$PURGE" = 1 ] && args+=(--purge)
    [ "$YES" = 1 ] && args+=(--yes)
    if [ -t 0 ]; then
        exec "$ODIN_HOME/bin/odin" uninstall "${args[@]}"
    elif [ "$YES" = 1 ]; then
        exec "$ODIN_HOME/bin/odin" uninstall "${args[@]}" </dev/null
    else
        exec "$ODIN_HOME/bin/odin" uninstall "${args[@]}" </dev/tty
    fi
fi

# ── Jalur lama: kode langsung di ~/.odin (ODIN <= v2.2) ─────────────────────
if [ ! -d "$ODIN_HOME" ]; then
    warn "ODIN tidak ditemukan di $ODIN_HOME — tidak ada yang dihapus."
    exit 0
fi

printf "\n  ODIN Uninstaller (tata letak lama)\n\n"
printf "  Akan dihapus:\n"
printf "    • kode ODIN di %s (state keys/servers/projects %s)\n" "$ODIN_HOME" \
       "$([ "$PURGE" = 1 ] && echo 'IKUT DIHAPUS' || echo 'dipertahankan')"
printf "    • symlink /usr/local/bin/odin, /usr/local/bin/odin-update\n"
printf "    • entry mcpServers.odin di ~/.claude.json; hook & allow ODIN di ~/.claude/settings.json\n\n"
if [ "$YES" != 1 ]; then
    if [ -t 0 ]; then read -r -p "  Lanjutkan? [y/N] " ans; else read -r -p "  Lanjutkan? [y/N] " ans </dev/tty; fi
    case "$ans" in [Yy]*) ;; *) info "Dibatalkan."; exit 0 ;; esac
fi

PY="$(command -v python3 || command -v python || true)"
if [ -n "$PY" ]; then
    "$PY" - <<'PYEOF' && ok "Config Claude Code dibersihkan" || true
import json, os
from pathlib import Path
home = Path.home()
cj = home / ".claude.json"
if cj.exists():
    try:
        d = json.loads(cj.read_text())
        if "odin" in (d.get("mcpServers") or {}):
            d["mcpServers"].pop("odin")
            cj.write_text(json.dumps(d, indent=2) + "\n")
    except Exception:
        pass
sp = home / ".claude" / "settings.json"
if sp.exists():
    try:
        s = json.loads(sp.read_text())
        (s.get("mcpServers") or {}).pop("odin", None)
        for ev, lst in list((s.get("hooks") or {}).items()):
            s["hooks"][ev] = [h for h in lst if not str((h or {}).get("matcher", "")).startswith("mcp__odin__")]
        allow = (s.get("permissions") or {}).get("allow")
        if isinstance(allow, list):
            s["permissions"]["allow"] = [a for a in allow if not str(a).startswith("mcp__odin__")]
        sp.write_text(json.dumps(s, indent=2) + "\n")
    except Exception:
        pass
PYEOF
fi
rm -rf "$HOME/.claude/commands/odin" 2>/dev/null || true

for link in /usr/local/bin/odin /usr/local/bin/odin-update; do
    if [ -L "$link" ]; then
        rm -f "$link" 2>/dev/null && ok "$link dihapus" || warn "Hapus manual: sudo rm $link"
    fi
done

if [ "$PURGE" = 1 ]; then
    rm -rf "$ODIN_HOME" "$HOME/.odin_mode"
    ok "$ODIN_HOME dihapus seluruhnya (termasuk kunci SSH)"
else
    if [ -d "$ODIN_HOME/.git" ]; then
        git -C "$ODIN_HOME" ls-files -z 2>/dev/null | while IFS= read -r -d '' f; do rm -f "$ODIN_HOME/$f"; done
        rm -rf "$ODIN_HOME/.git" "$ODIN_HOME/.venv" "$ODIN_HOME/client" "$ODIN_HOME/server" \
               "$ODIN_HOME/tests" "$ODIN_HOME/odin-update.sh" "$ODIN_HOME/odin-cli.sh" 2>/dev/null || true
        ok "Kode dihapus; state di $ODIN_HOME dipertahankan (keys/servers/projects/modes)"
    else
        warn "$ODIN_HOME bukan checkout git — tidak ada kode yang dikenali; state dibiarkan."
    fi
fi
printf "\n"
ok "ODIN di-uninstall dari laptop ini. Server tidak disentuh."
