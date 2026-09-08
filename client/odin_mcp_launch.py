#!/usr/bin/env python3
"""ODIN global MCP launcher.

Dipanggil oleh entry MCP scope-user (~/.claude/settings.json). Saat Claude Code
men-spawn MCP stdio, cwd = root workspace. Launcher ini meresolusi project+server
dari cwd (cocokkan ke local_workdir di ~/.odin/projects/*.yaml — asumsi yang sama
dipakai odin_cli._detect_current_project), lalu exec SSH stdio MCP ke server yang
benar dengan --project yang benar.

Satu entry global -> melayani SEMUA project terdaftar, resolusi dinamis di waktu-spawn.
Dir yang bukan project terdaftar -> menolak bersih (exit 1), tanpa spawn SSH.

ROBUSTNESS (kenapa launcher ini tidak boleh menggantung Claude saat startup MCP):
  Claude Code mengkoneksikan server MCP HANYA saat startup. Bila launcher MENGGANTUNG
  (mis. ssh menunggu prompt passphrase/host-key) sampai timeout, tool `mcp__odin__*`
  diam-diam TIDAK pernah muncul tanpa pesan jelas. Maka:
    1. Dial server EKSPLISIT dari registry ODIN (~/.odin/servers/<alias>.yaml:
       host/port/user/key) -> KEBAL terhadap drift/hijack ~/.ssh/config (sama seperti
       cara CLI `odin` yang dial IP+key langsung). Fallback ke alias bila registry
       tak lengkap (kompat lama).
    2. Opsi SSH yang membuat kegagalan CEPAT & KERAS, bukan menggantung: BatchMode
       (jangan pernah prompt), ConnectTimeout, ServerAlive* (jaga tunnel hidup +
       deteksi peer mati), StrictHostKeyChecking=accept-new (tak ada prompt host-key).
    3. Selalu menulis 1 baris diagnosa target ke stderr sebelum exec -> log MCP Claude
       memperlihatkan PERSIS ke mana ia menyambung (deteksi config/registry drift).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ODIN_DIR = Path.home() / ".odin"
PROJECTS_DIR = ODIN_DIR / "projects"
SERVERS_DIR = ODIN_DIR / "servers"

# Opsi SSH bersama: utamakan GAGAL-CEPAT ketimbang menggantung startup MCP.
# -T  : tanpa PTY -> cegah MOTD bocor ke stdout (stdout harus bersih utk JSON-RPC).
#       (-q sengaja TIDAK dipakai: agar error fatal ssh tetap terlihat di log MCP.)
SSH_COMMON = [
    "-T",
    "-o", "BatchMode=yes",                    # JANGAN pernah prompt (passwd/passphrase/host-key) -> fail cepat
    "-o", "ConnectTimeout=10",                # host mati -> gagal ~10s, bukan menggantung
    "-o", "ServerAliveInterval=15",           # jaga tunnel MCP hidup selama sesi
    "-o", "ServerAliveCountMax=4",            # peer mati terdeteksi ~60s lalu keluar
    "-o", "StrictHostKeyChecking=accept-new",  # terima host baru tanpa prompt; tolak key BERUBAH
]

try:
    import yaml  # type: ignore

    def _load(p: Path) -> dict:
        return yaml.safe_load(p.read_text()) or {}
except ImportError:  # fallback: manifest ODIN adalah YAML flat "key: value"
    def _load(p: Path) -> dict:
        d: dict = {}
        for line in p.read_text().splitlines():
            line = line.rstrip()
            if not line or line.lstrip().startswith("#") or ":" not in line:
                continue
            k, _, v = line.partition(":")
            d[k.strip()] = v.strip().strip("'\"")
        return d


def _detect(cwd: str) -> dict | None:
    """Project yang local_workdir-nya == cwd ATAU prefix dari cwd (subdir).
    Bila beberapa cocok, pilih yang terpanjang (paling spesifik)."""
    cwd = os.path.normpath(str(Path(cwd).resolve()))
    best: dict | None = None
    best_len = -1
    if not PROJECTS_DIR.is_dir():
        return None
    for f in sorted(PROJECTS_DIR.glob("*.yaml")) + sorted(PROJECTS_DIR.glob("*.json")):
        try:
            d = _load(f)
        except Exception:
            continue
        wd = d.get("local_workdir")
        if not wd:
            continue
        wd = os.path.normpath(str(Path(wd).resolve()))  # resolve simbolik agar cocok cwd
        if cwd == wd or cwd.startswith(wd + os.sep):
            if len(wd) > best_len:
                best, best_len = d, len(wd)
    return best


def _server_conn(alias: str) -> dict:
    """Detail koneksi eksplisit dari registry ODIN (~/.odin/servers/<alias>.{yaml,yml,json}).
    Mengembalikan {} bila tak ditemukan/terbaca -> pemanggil jatuh ke jalur alias."""
    for ext in (".yaml", ".yml", ".json"):
        f = SERVERS_DIR / f"{alias}{ext}"
        if f.is_file():
            try:
                return _load(f) or {}
            except Exception:
                return {}
    return {}


def main() -> None:
    proj = _detect(os.getcwd())
    if not proj or not proj.get("server") or not proj.get("name"):
        sys.stderr.write(
            "odin-launch: cwd bukan project ODIN terdaftar — jalankan `odin project add` "
            "untuk menautkan workdir ini ke server.\n"
        )
        sys.exit(1)

    alias = str(proj["server"]).strip()
    name = str(proj["name"]).strip()
    remote_cmd = ["/home/odin/run.sh", "--project", name]

    conn = _server_conn(alias)
    host = str(conn.get("host") or "").strip()
    user = str(conn.get("user") or "").strip()
    port = str(conn.get("port") or "").strip()
    key = str(conn.get("key") or "").strip()
    key_path = Path(key).expanduser() if key else None

    argv = ["ssh", *SSH_COMMON]
    if host and user and key_path and key_path.is_file():
        # Jalur EKSPLISIT (disukai): dial host+key langsung, abaikan ~/.ssh/config.
        # IdentitiesOnly: jangan coba key lain dari agent (hindari "too many auth failures").
        argv += ["-o", "IdentitiesOnly=yes", "-i", str(key_path)]
        if port:
            argv += ["-p", port]
        target = f"{user}@{host}"
        sys.stderr.write(
            f"odin-launch: {name} -> {target}:{port or '22'} via key registry (kebal ssh_config)\n"
        )
    else:
        # FALLBACK: pakai alias + ~/.ssh/config (kompat lama / registry tak lengkap).
        target = alias
        why = "registry tak lengkap" if conn else "registry tak ada"
        sys.stderr.write(
            f"odin-launch: {name} -> alias '{alias}' via ~/.ssh/config (fallback: {why})\n"
        )
    argv += [target, *remote_cmd]
    sys.stderr.flush()

    try:
        os.execvp("ssh", argv)
    except FileNotFoundError:
        sys.stderr.write("odin-launch: 'ssh' tidak ditemukan di PATH.\n")
        sys.exit(127)


if __name__ == "__main__":
    main()
