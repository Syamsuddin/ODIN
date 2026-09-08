#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN forced-command dispatcher (K3)
#
# Dipasang di ~odin/.ssh/authorized_keys sebagai:
#   restrict,command="/home/odin/odin-dispatch.sh" ssh-ed25519 AAAA... odin-<alias>
#
# Efeknya: kunci SSH ODIN HANYA bisa meluncurkan MCP agent. Siapa pun yang
# memegang private key di ~/.odin/keys/<alias> tidak mendapat shell interaktif,
# tidak dapat TTY, dan karenanya tak bisa memakai pager `journalctl`/`systemctl
# status` (yang ber-NOPASSWD di sudoers) sebagai jalan eskalasi ke root.
#
# Satu-satunya bentuk perintah yang diterima:
#   (kosong)                         → run.sh tanpa argumen
#   /home/odin/run.sh                → idem
#   /home/odin/run.sh --project NAMA → run.sh --project NAMA
# NAMA dibatasi [A-Za-z0-9._-] sehingga tak bisa menyelipkan path/metakarakter.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

RUN_SH="$(cd "$(dirname "$0")" && pwd)/run.sh"
CMD="${SSH_ORIGINAL_COMMAND:-}"

deny() {
    echo "ODIN: perintah ditolak — kunci ini hanya boleh menjalankan run.sh." >&2
    echo "      diminta: ${CMD}" >&2
    exit 126
}

# Tolak metakarakter shell sebelum parsing apa pun.
case "$CMD" in
    *[\;\&\|\$\`\<\>\(\)\{\}\!\\\'\"$'\n']*) deny ;;
esac

[ -z "$CMD" ] && exec "$RUN_SH"

# shellcheck disable=SC2206  # pemisahan kata memang disengaja; metakarakter sudah ditolak
PARTS=($CMD)

# Token pertama boleh berupa path ke run.sh (yang dikirim klien MCP) — buang.
case "${PARTS[0]:-}" in
    "$RUN_SH"|/home/odin/run.sh|run.sh|./run.sh) PARTS=("${PARTS[@]:1}") ;;
esac

if [ "${#PARTS[@]}" -eq 0 ]; then
    exec "$RUN_SH"
fi

if [ "${#PARTS[@]}" -ne 2 ] || [ "${PARTS[0]}" != "--project" ]; then
    deny
fi

PROJECT="${PARTS[1]}"
case "$PROJECT" in
    ""|*[!A-Za-z0-9._-]*|.|..) deny ;;
esac

exec "$RUN_SH" --project "$PROJECT"
