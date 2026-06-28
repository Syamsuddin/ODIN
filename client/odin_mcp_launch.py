#!/usr/bin/env python3
"""ODIN global MCP launcher.

Dipanggil oleh entry MCP scope-user (~/.claude/settings.json). Saat Claude Code
men-spawn MCP stdio, cwd = root workspace. Launcher ini meresolusi project+server
dari cwd (cocokkan ke local_workdir di ~/.odin/projects/*.yaml — asumsi yang sama
dipakai odin_cli._detect_current_project), lalu exec SSH stdio MCP ke server yang
benar dengan --project yang benar.

Satu entry global → melayani SEMUA project terdaftar, resolusi dinamis di waktu-spawn.
Dir yang bukan project terdaftar → menolak bersih (exit 1), tanpa spawn SSH.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECTS_DIR = Path.home() / ".odin" / "projects"

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


def main() -> None:
    proj = _detect(os.getcwd())
    if not proj or not proj.get("server") or not proj.get("name"):
        sys.stderr.write(
            "odin: cwd bukan project ODIN terdaftar — jalankan `odin project add` "
            "untuk menautkan workdir ini ke server.\n"
        )
        sys.exit(1)
    # -q: bungkam banner SSH agar stdio bersih untuk JSON-RPC; -T: tanpa PTY (cegah MOTD)
    os.execvp(
        "ssh",
        ["ssh", "-q", "-T", proj["server"], "/home/odin/run.sh", "--project", proj["name"]],
    )


if __name__ == "__main__":
    main()
