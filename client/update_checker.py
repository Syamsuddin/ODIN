#!/usr/bin/env python3
"""
ODIN Update Checker — cek versi terbaru dari GitHub, notifikasi user.

Dipanggil oleh guard saat startup (non-blocking, background).
Bisa juga dijalankan standalone: python3 update_checker.py

Mekanisme:
  1. Baca __version__ dari deploy_agent.py lokal.
  2. Hit GitHub API (releases/latest atau raw __version__ dari main).
  3. Bandingkan. Tampilkan notifikasi jika ada update.
  4. Cache hasil selama CACHE_TTL_HOURS agar tidak hit API setiap sesi.
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

REPO = "Syamsuddin/ODIN"
CACHE_TTL_HOURS = 6
INSTALL_DIR = Path(os.environ.get("ODIN_INSTALL_DIR", Path.home() / ".odin"))
CACHE_FILE = INSTALL_DIR / ".update-cache.json"

NC = "\033[0m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"


def _local_version() -> str:
    """Baca __version__ dari server/deploy_agent.py lokal."""
    candidates = [
        INSTALL_DIR / "server" / "deploy_agent.py",
        Path(__file__).resolve().parent.parent / "server" / "deploy_agent.py",
    ]
    for p in candidates:
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
            if m:
                return m.group(1)
    return "0.0.0"


def _remote_version() -> str | None:
    """Ambil versi terbaru dari GitHub (tag release atau raw file)."""
    # Coba GitHub Releases API dulu
    try:
        url = f"https://api.github.com/repos/{REPO}/releases/latest"
        req = Request(url, headers={"Accept": "application/vnd.github.v3+json",
                                     "User-Agent": "ODIN-UpdateChecker"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            return tag.lstrip("v")
    except (URLError, json.JSONDecodeError, KeyError, OSError):
        pass

    # Fallback: baca __version__ langsung dari main branch
    try:
        url = f"https://raw.githubusercontent.com/{REPO}/main/server/deploy_agent.py"
        req = Request(url, headers={"User-Agent": "ODIN-UpdateChecker"})
        with urlopen(req, timeout=5) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
            if m:
                return m.group(1)
    except (URLError, OSError):
        pass

    return None


def _parse_version(v: str) -> tuple:
    """Parse '0.9.0' -> (0, 9, 0) untuk perbandingan."""
    parts = re.findall(r'\d+', v)
    return tuple(int(p) for p in parts) if parts else (0,)


def _read_cache() -> dict | None:
    try:
        if CACHE_FILE.is_file():
            data = json.loads(CACHE_FILE.read_text())
            age_hours = (time.time() - data.get("checked_at", 0)) / 3600
            if age_hours < CACHE_TTL_HOURS:
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _write_cache(local: str, remote: str | None):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({
            "checked_at": time.time(),
            "local": local,
            "remote": remote,
        }, indent=2))
    except OSError:
        pass


def check_update(quiet: bool = False) -> dict:
    """Cek update. Return dict: {update_available, local, remote, message}."""
    local = _local_version()

    # Cek cache dulu
    cache = _read_cache()
    if cache and cache.get("local") == local:
        remote = cache.get("remote")
    else:
        remote = _remote_version()
        _write_cache(local, remote)

    if not remote:
        return {"update_available": False, "local": local, "remote": None,
                "message": ""}

    has_update = _parse_version(remote) > _parse_version(local)

    msg = ""
    if has_update:
        msg = (
            f"\n{YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}\n"
            f"  {BOLD}⚡ ODIN update tersedia!{NC}  "
            f"{CYAN}v{local}{NC} → {GREEN}v{remote}{NC}\n"
            f"\n"
            f"  Update sekarang:\n"
            f"    {CYAN}odin-update{NC}\n"
            f"    {CYAN}curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | bash{NC}\n"
            f"\n"
            f"  Changelog:\n"
            f"    {CYAN}https://github.com/{REPO}/releases/tag/v{remote}{NC}\n"
            f"{YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{NC}\n"
        )

    result = {"update_available": has_update, "local": local,
              "remote": remote, "message": msg}

    if not quiet and msg:
        print(msg, file=sys.stderr)

    return result


if __name__ == "__main__":
    r = check_update(quiet=False)
    if not r["update_available"]:
        print(f"{GREEN}✓{NC} ODIN v{r['local']} sudah versi terbaru.", file=sys.stderr)
