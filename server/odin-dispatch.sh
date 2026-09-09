#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN forced-command dispatcher (K3 + K4)
#
# Dipasang di ~odin/.ssh/authorized_keys sebagai:
#   restrict,command="/home/odin/odin-dispatch.sh" ssh-ed25519 AAAA... odin-<alias>
#
# Efeknya: kunci SSH ODIN HANYA bisa meluncurkan MCP agent atau dua sub-perintah
# kontrol yang tervalidasi. Siapa pun yang memegang private key di
# ~/.odin/keys/<alias> tidak mendapat shell interaktif, tidak dapat TTY, dan
# karenanya tak bisa memakai pager `journalctl`/`systemctl status` (yang
# ber-NOPASSWD di sudoers) sebagai jalan eskalasi ke root.
#
# Satu-satunya bentuk perintah yang diterima:
#   (kosong)                                  → run.sh tanpa argumen
#   /home/odin/run.sh                         → idem
#   /home/odin/run.sh --project NAMA          → run.sh --project NAMA
#   /home/odin/run.sh --diagnose              → laporan keadaan server (K4)
#   /home/odin/run.sh --provision NAMA --root PATH → buat conf project (K4)
#
# NAMA dibatasi [A-Za-z0-9._-] sehingga tak bisa menyelipkan path/metakarakter.
# PATH wajib absolut, tanpa '..', dan hanya [A-Za-z0-9._/-].
#
# CATATAN K4: --diagnose & --provision ADA supaya CLI tidak perlu mengirim
# perintah shell lewat kunci ini. Sebelumnya CLI melakukannya — sshd membuang
# perintahnya, dan CLI salah menyimpulkan servernya rusak. Kanal sempit ini
# mengembalikan alur bebas-password TANPA memberi shell.
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

valid_name() {
    case "$1" in
        ""|*[!A-Za-z0-9._-]*|.|..) return 1 ;;
    esac
    return 0
}

# Absolut, tanpa '..', charset tertutup. Metakarakter sudah ditolak di atas.
valid_root() {
    case "$1" in
        /*) ;;
        *) return 1 ;;
    esac
    case "$1" in
        *[!A-Za-z0-9._/-]*|*..*) return 1 ;;
    esac
    return 0
}

case "${PARTS[0]}" in
    --project)
        [ "${#PARTS[@]}" -eq 2 ] || deny
        valid_name "${PARTS[1]}" || deny
        exec "$RUN_SH" --project "${PARTS[1]}"
        ;;
    --diagnose)
        [ "${#PARTS[@]}" -eq 1 ] || deny
        exec "$RUN_SH" --diagnose
        ;;
    --provision)
        [ "${#PARTS[@]}" -eq 4 ] || deny
        [ "${PARTS[2]}" = "--root" ] || deny
        valid_name "${PARTS[1]}" || deny
        valid_root "${PARTS[3]}" || deny
        exec "$RUN_SH" --provision "${PARTS[1]}" --root "${PARTS[3]}"
        ;;
    *)
        deny
        ;;
esac
