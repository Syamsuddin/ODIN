#!/usr/bin/env python3
"""
ODIN v2.0 — CLI untuk manajemen multi-server & multi-project.

Usage:
    odin server add|list|remove|test
    odin project add|list|status|switch|sync|remove
    odin update <server-alias>
    odin doctor <server-alias>

Non-interaktif (batch):
    odin project add --name foo --server srv --remote-root /var/www/foo \
        --workdir ~/PROJECTS/FOO --yes
    odin project sync --all     # regenerasi config lokal dari manifest
"""
from __future__ import annotations

__version__ = "2.3.0"

import argparse
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

try:
    import paramiko
except ImportError:
    paramiko = None  # type: ignore[assignment]

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# ── Paths ───────────────────────────────────────────────────────────────────
ODIN_DIR = Path.home() / ".odin"
SERVERS_DIR = ODIN_DIR / "servers"
PROJECTS_DIR = ODIN_DIR / "projects"
KEYS_DIR = ODIN_DIR / "keys"
MODES_DIR = ODIN_DIR / "modes"
ODIN_INSTALL_DIR = Path(__file__).resolve().parent.parent
ODIN_REMOTE_HOME = "/home/odin"
ODIN_DISPATCH_REMOTE = f"{ODIN_REMOTE_HOME}/odin-dispatch.sh"
USER_CLAUDE_DIR = Path.home() / ".claude"   # scope-user: hook + allow-list dibaca dari sini
USER_CLAUDE_JSON = Path.home() / ".claude.json"  # <- Claude Code MEMBACA definisi MCP dari sini

# ── Warna ───────────────────────────────────────────────────────────────────
_NO_COLOR = os.environ.get("NO_COLOR") or not sys.stdout.isatty()
def _c(code: str, text: str) -> str:
    return text if _NO_COLOR else f"\033[{code}m{text}\033[0m"

def info(msg: str) -> None: print(f"{_c('0;34', '▸')} {msg}")
def ok(msg: str) -> None:   print(f"{_c('0;32', '✓')} {msg}")
def warn(msg: str) -> None: print(f"{_c('1;33', '⚠')} {msg}")
def err(msg: str) -> None:  print(f"{_c('0;31', '✗')} {msg}", file=sys.stderr)

def progress(step: int, total: int, msg: str) -> None:
    print(f"  {_c('0;34', '▸')} [{step}/{total}] {msg}")

def banner(title: str) -> None:
    line = "━" * 50
    print(f"\n{_c('1;36', line)}")
    print(f"{_c('1;36', f'  {title}')}")
    print(f"{_c('1;36', line)}\n")

def ask_input(prompt: str, default: str = "") -> str:
    while True:
        if default:
            val = input(f"  {_c('1', prompt)} [{_c('0;36', default)}]: ") or default
        else:
            val = input(f"  {_c('1', prompt)} (wajib): ")
        if val.strip():
            return val.strip()
        warn("Nilai tidak boleh kosong.")

def ask_choice(prompt: str, options: list[str], default: int = 1) -> int:
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        raw = input(f"  {_c('1', prompt)} [{_c('0;36', str(default))}]: ") or str(default)
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        warn(f"Pilih 1-{len(options)}.")

def confirm(msg: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {msg} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw.startswith("y")


# ── YAML helpers (fallback ke JSON kalau pyyaml tidak ada) ──────────────────
def _save_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml:
        path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))
    else:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

def _load_yaml(path: Path) -> dict:
    text = path.read_text()
    if yaml:
        return yaml.safe_load(text) or {}
    return json.loads(text)


def ensure_dirs() -> None:
    for d in (ODIN_DIR, SERVERS_DIR, PROJECTS_DIR, KEYS_DIR, MODES_DIR):
        d.mkdir(parents=True, exist_ok=True)

def load_server(alias: str) -> dict:
    path = SERVERS_DIR / f"{alias}.yaml"
    if not path.exists():
        path = SERVERS_DIR / f"{alias}.json"
    if not path.exists():
        err(f"Server '{alias}' tidak ditemukan.")
        sys.exit(1)
    return _load_yaml(path)

def save_server(alias: str, data: dict) -> None:
    _save_yaml(SERVERS_DIR / f"{alias}.yaml", data)

def load_project(name: str) -> dict:
    path = PROJECTS_DIR / f"{name}.yaml"
    if not path.exists():
        path = PROJECTS_DIR / f"{name}.json"
    if not path.exists():
        err(f"Project '{name}' tidak ditemukan.")
        sys.exit(1)
    return _load_yaml(path)

def save_project(name: str, data: dict) -> None:
    _save_yaml(PROJECTS_DIR / f"{name}.yaml", data)

def list_servers() -> list[str]:
    if not SERVERS_DIR.exists():
        return []
    return sorted(p.stem for p in SERVERS_DIR.iterdir()
                  if p.suffix in (".yaml", ".json"))

def list_projects() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.stem for p in PROJECTS_DIR.iterdir()
                  if p.suffix in (".yaml", ".json"))

def _detect_current_project() -> str | None:
    """Deteksi project saat ini berdasarkan cwd cocok local_workdir."""
    cwd = str(Path.cwd().resolve())
    for name in list_projects():
        for ext in ("yaml", "json"):
            path = PROJECTS_DIR / f"{name}.{ext}"
            if path.exists():
                try:
                    d = _load_yaml(path)
                    registered = d.get("local_workdir", "")
                    if registered and os.path.normpath(cwd) == os.path.normpath(registered):
                        return name
                except Exception:
                    continue
    return None


# ── Config lokal MCP (satu sumber kebenaran: .claude/settings.json) ─────────
# Tools read-only yang aman di-auto-allow di workdir project.
READ_ONLY_TOOLS = [
    "mcp__odin__server_info", "mcp__odin__tail_log",
    "mcp__odin__http_health_check", "mcp__odin__memory_recall",
    "mcp__odin__memory_digest", "mcp__odin__memory_health",
    "mcp__odin__session_history", "mcp__odin__rollback_plan",
    "mcp__odin__inspect_server", "mcp__odin__audit_tail",
    "mcp__odin__runbook_templates", "mcp__odin__cortex_events",
    "mcp__odin__run_tests",
]

# Matcher hook: SEMUA tool odin lewat guard. Dengan matcher sempit, tool baru
# (mis. cortex_log) diam-diam lolos tanpa penilaian risiko. Guard sendiri yang
# memutuskan read-only → allow, sisanya → kartu risiko.
GUARD_MATCHER = "mcp__odin__.*"


def _validate_remote_root(path: str) -> bool:
    """Path remote harus absolut & hanya karakter aman (anti shell-injection)."""
    return bool(re.match(r"^/[A-Za-z0-9._/-]*$", path))


def _odin_mcp_entry(server_alias: str, name: str) -> dict:
    # -q: suppress SSH client warnings/banners to keep stdio clean for JSON-RPC
    # -T: disable pseudo-TTY so sshd won't print MOTD/PrintMotd to stdout
    return {
        "type": "stdio",
        "command": "ssh",
        "args": ["-q", "-T", server_alias, "/home/odin/run.sh", "--project", name],
    }


def _guard_path() -> str:
    return str(ODIN_INSTALL_DIR / "client" / "odin_guard.py")


def _strip_odin_from_mcp_json(workdir: str) -> bool:
    """Buang entry 'odin' dari .mcp.json (format lama) agar tak ada definisi ganda.
    Hapus file bila jadi kosong. Return True bila ada perubahan."""
    mcp_path = Path(workdir) / ".mcp.json"
    if not mcp_path.exists():
        return False
    try:
        data = json.loads(mcp_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    servers = data.get("mcpServers", {})
    if "odin" not in servers:
        return False
    servers.pop("odin", None)
    if not servers:
        data.pop("mcpServers", None)
    try:
        if not data:
            mcp_path.unlink()
        else:
            mcp_path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        return False
    return True


def _write_local_mcp_config(workdir: str, server_alias: str, name: str) -> Path:
    """Tulis .claude/settings.json kanonik (entry odin + hooks guard + allow read-only).
    Sekaligus migrasi: buang odin dari .mcp.json lama bila ada (anti-drift)."""
    claude_dir = Path(workdir) / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    settings.setdefault("mcpServers", {})["odin"] = _odin_mcp_entry(server_alias, name)

    # Hook di-MERGE (bukan assignment) — hook Bash/lainnya milik user harus selamat.
    guard_cmd = f"python3 '{_guard_path()}'"
    _ensure_guard_hook(settings, "PreToolUse", GUARD_MATCHER, guard_cmd)
    _ensure_guard_hook(settings, "PostToolUse", "mcp__odin__inspect_server", guard_cmd)

    allow_list = settings.setdefault("permissions", {}).setdefault("allow", [])
    for tool in READ_ONLY_TOOLS:
        if tool not in allow_list:
            allow_list.append(tool)

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    _strip_odin_from_mcp_json(workdir)
    return settings_path


def _remove_local_mcp_config(workdir: str) -> None:
    """Hapus entry odin dari kedua lokasi (.claude/settings.json & .mcp.json)."""
    settings_path = Path(workdir) / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            (settings.get("mcpServers") or {}).pop("odin", None)
            if not settings.get("mcpServers"):
                settings.pop("mcpServers", None)
            if settings:
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
            else:
                settings_path.unlink()
                claude_dir = settings_path.parent
                if claude_dir.exists() and not any(claude_dir.iterdir()):
                    claude_dir.rmdir()
        except (json.JSONDecodeError, OSError):
            pass
    _strip_odin_from_mcp_json(workdir)


def _has_odin_mcp(workdir: str) -> tuple[bool, str]:
    """True bila workdir punya entry MCP odin di settings.json ATAU .mcp.json."""
    base = Path(workdir)
    for label, p in ((".claude/settings.json", base / ".claude" / "settings.json"),
                     (".mcp.json", base / ".mcp.json")):
        if p.exists():
            try:
                d = json.loads(p.read_text())
                if "odin" in (d.get("mcpServers") or {}):
                    return True, label
            except (json.JSONDecodeError, OSError):
                continue
    return False, ""


def _has_global_odin_mcp() -> bool:
    """True bila Claude Code mengenali server MCP 'odin' di config global-nya.
    PENTING: Claude Code (v2.x) membaca definisi MCP dari ~/.claude.json — top-level
    `mcpServers` (user-scope) ATAU `projects[*].mcpServers` (local-scope) — dan dari
    .mcp.json. Ia TIDAK membaca mcpServers dari ~/.claude/settings.json. Maka cek
    ~/.claude.json sbg sumber kebenaran; settings.json hanya fallback legacy."""
    cj = USER_CLAUDE_JSON
    if cj.exists():
        try:
            d = json.loads(cj.read_text())
            if "odin" in (d.get("mcpServers") or {}):
                return True
            for p in (d.get("projects") or {}).values():
                if "odin" in ((p or {}).get("mcpServers") or {}):
                    return True
        except (json.JSONDecodeError, OSError):
            pass
    sp = USER_CLAUDE_DIR / "settings.json"  # legacy (lokasi lama yang diabaikan Claude Code)
    if sp.exists():
        try:
            return "odin" in (json.loads(sp.read_text()).get("mcpServers") or {})
        except (json.JSONDecodeError, OSError):
            pass
    return False


def _claude_json_register_odin() -> tuple[bool, str]:
    """Daftarkan MCP 'odin' user-scope ke ~/.claude.json (file yang DIBACA Claude Code).
    Utamakan CLI resmi `claude mcp add` (penanganan file aman & idempoten); bila `claude`
    tak ada di PATH, fallback tulis langsung ke ~/.claude.json."""
    launcher = str(ODIN_INSTALL_DIR / "client" / "odin_mcp_launch.py")
    claude_bin = shutil.which("claude")
    if claude_bin:
        subprocess.run([claude_bin, "mcp", "remove", "odin", "-s", "user"],
                       capture_output=True, text=True)  # idempoten
        r = subprocess.run([claude_bin, "mcp", "add", "odin", "-s", "user",
                            "--", "python3", launcher], capture_output=True, text=True)
        if r.returncode == 0:
            return True, "claude mcp add -s user"
    cj = USER_CLAUDE_JSON
    data: dict = {}
    if cj.exists():
        try:
            data = json.loads(cj.read_text())
        except (json.JSONDecodeError, OSError):
            return False, "~/.claude.json tak terbaca"
    data.setdefault("mcpServers", {})["odin"] = _global_mcp_entry()
    try:
        cj.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as e:
        return False, f"gagal tulis ~/.claude.json: {e}"
    return True, "tulis langsung ke ~/.claude.json"


def _claude_json_unregister_odin() -> bool:
    """Cabut MCP 'odin' user-scope dari ~/.claude.json (via CLI, fallback edit langsung)."""
    claude_bin = shutil.which("claude")
    if claude_bin:
        r = subprocess.run([claude_bin, "mcp", "remove", "odin", "-s", "user"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return True
    cj = USER_CLAUDE_JSON
    if not cj.exists():
        return False
    try:
        data = json.loads(cj.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if "odin" not in (data.get("mcpServers") or {}):
        return False
    data["mcpServers"].pop("odin", None)
    if not data["mcpServers"]:
        data.pop("mcpServers", None)
    try:
        cj.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        return False
    return True


# ── Config global MCP (scope-user: ~/.claude/settings.json) ─────────────────
# Satu entry untuk SEMUA project; project+server diresolusi dinamis di waktu-spawn
# oleh client/odin_mcp_launch.py (cocokkan cwd → local_workdir).
def _global_mcp_entry() -> dict:
    launcher = str(ODIN_INSTALL_DIR / "client" / "odin_mcp_launch.py")
    return {"type": "stdio", "command": "python3", "args": [launcher]}


def _ensure_guard_hook(settings: dict, event: str, matcher: str, command: str) -> None:
    """Sisipkan/replace hook guard tanpa membuang hook lain milik user (anti-clobber).
    Cocokkan berdasarkan matcher; bila sudah ada → perbarui, bila belum → append."""
    arr = settings.setdefault("hooks", {}).setdefault(event, [])
    entry = {"matcher": matcher,
             "hooks": [{"type": "command", "command": command, "timeout": 10}]}
    for h in arr:
        if h.get("matcher") == matcher:
            h["hooks"] = entry["hooks"]
            return
    arr.append(entry)


def _purge_odin_local(workdir: str) -> bool:
    """Cabut SELURUH jejak odin (mcpServers/hooks/allow) dari .claude/settings.json
    sebuah workdir — dipakai saat migrasi ke config global agar tak ada definisi ganda.
    Hanya menyentuh key milik odin; hook/allow user lain dipertahankan."""
    changed = False
    sp = Path(workdir) / ".claude" / "settings.json"
    if sp.exists():
        try:
            s = json.loads(sp.read_text())
        except (json.JSONDecodeError, OSError):
            s = None
        if isinstance(s, dict):
            if "odin" in (s.get("mcpServers") or {}):
                s["mcpServers"].pop("odin", None); changed = True
                if not s["mcpServers"]:
                    s.pop("mcpServers", None)
            hooks = s.get("hooks") or {}
            for ev in list(hooks.keys()):
                kept = [h for h in hooks[ev]
                        if not str(h.get("matcher", "")).startswith("mcp__odin__")]
                if len(kept) != len(hooks[ev]):
                    changed = True
                    hooks[ev] = kept
                if not hooks[ev]:
                    hooks.pop(ev, None)
            if "hooks" in s and not s["hooks"]:
                s.pop("hooks", None)
            allow = (s.get("permissions") or {}).get("allow")
            if isinstance(allow, list):
                new_allow = [a for a in allow if a not in READ_ONLY_TOOLS]
                if len(new_allow) != len(allow):
                    changed = True
                    if new_allow:
                        s["permissions"]["allow"] = new_allow
                    else:
                        s["permissions"].pop("allow", None)
                        if not s["permissions"]:
                            s.pop("permissions", None)
            if changed:
                if s:
                    sp.write_text(json.dumps(s, indent=2) + "\n")
                else:
                    sp.unlink()
                    cd = sp.parent
                    if cd.exists() and not any(cd.iterdir()):
                        cd.rmdir()
    if _strip_odin_from_mcp_json(workdir):
        changed = True
    return changed


def cmd_global_enable(migrate: bool = False) -> None:
    """Pasang ODIN MCP di scope-user agar tersedia OTOMATIS di tiap project terdaftar.

    Pemisahan lokasi (Claude Code membaca dari file berbeda):
      • definisi server MCP  → ~/.claude.json        (satu-satunya yang dibaca utk mcpServers)
      • hook guard + allow-list read-only → ~/.claude/settings.json (dibaca dari sini)."""
    # 1) Definisi server MCP ke ~/.claude.json — lokasi yang BENAR.
    okj, how = _claude_json_register_odin()
    if not okj:
        err(f"Gagal mendaftarkan MCP odin ke ~/.claude.json ({how}) — batal.")
        return

    # 2) Hook guard + allow-list read-only ke ~/.claude/settings.json.
    USER_CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    sp = USER_CLAUDE_DIR / "settings.json"
    settings: dict | None = {}
    if sp.exists():
        try:
            settings = json.loads(sp.read_text())
        except json.JSONDecodeError:
            warn(f"{sp} bukan JSON valid — hook/allow dilewati (server MCP sudah terdaftar).")
            settings = None

    if settings is not None:
        # Buang sisa mcpServers.odin salah-lokasi di settings.json (diabaikan Claude Code).
        if "odin" in (settings.get("mcpServers") or {}):
            settings["mcpServers"].pop("odin", None)
            if not settings["mcpServers"]:
                settings.pop("mcpServers", None)
        guard = _guard_path()
        cmd = f"python3 '{guard}'"
        _ensure_guard_hook(settings, "PreToolUse", GUARD_MATCHER, cmd)
        _ensure_guard_hook(settings, "PostToolUse", "mcp__odin__inspect_server", cmd)
        allow = settings.setdefault("permissions", {}).setdefault("allow", [])
        for t in READ_ONLY_TOOLS:
            if t not in allow:
                allow.append(t)
        sp.write_text(json.dumps(settings, indent=2) + "\n")

    ok(f"ODIN MCP global aktif (server→~/.claude.json via {how}; guard/allow→{sp}).")
    info("Tiap project terdaftar otomatis dapat MCP odin (resolusi project dari cwd).")

    if migrate:
        cleaned = []
        for name in list_projects():
            wd = load_project(name).get("local_workdir")
            if wd and Path(wd).exists() and _purge_odin_local(wd):
                cleaned.append(name)
        if cleaned:
            ok(f"Entry per-workdir lama dibersihkan: {', '.join(cleaned)} "
               f"(global jadi satu-satunya sumber, tanpa definisi/hook ganda).")
        else:
            info("Tidak ada entry per-workdir lama yang perlu dibersihkan.")
    warn("Restart Claude Code agar perubahan MCP termuat (MCP dimuat saat startup).")


def cmd_global_disable() -> None:
    """Cabut server MCP odin dari ~/.claude.json (hook/allow di settings.json dibiarkan)."""
    removed = _claude_json_unregister_odin()
    # Sapu juga sisa entry salah-lokasi di settings.json (kompat config lama).
    sp = USER_CLAUDE_DIR / "settings.json"
    if sp.exists():
        try:
            s = json.loads(sp.read_text())
            if "odin" in (s.get("mcpServers") or {}):
                s["mcpServers"].pop("odin", None)
                if not s["mcpServers"]:
                    s.pop("mcpServers", None)
                sp.write_text(json.dumps(s, indent=2) + "\n")
                removed = True
        except json.JSONDecodeError:
            pass
    if removed:
        ok("ODIN MCP global dinonaktifkan.")
        warn("Restart Claude Code agar perubahan termuat.")
    else:
        info("Entry odin global tidak ditemukan — tidak ada yang dicabut.")


def get_odin_version() -> str:
    agent = ODIN_INSTALL_DIR / "server" / "odin_agent.py"
    if agent.exists():
        m = re.search(r'__version__\s*=\s*"([^"]+)"', agent.read_text())
        if m:
            return m.group(1)
    return __version__


# ── SSH Session ─────────────────────────────────────────────────────────────
class SSHSession:
    """Wrapper paramiko untuk koneksi SSH."""

    def __init__(self, host: str, port: int, user: str, *,
                 password: str | None = None, key_path: str | None = None):
        if paramiko is None:
            err("Module 'paramiko' belum terinstall.")
            err("Install: pip install paramiko")
            sys.exit(1)
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_path = key_path
        self.client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": 15,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.key_path and Path(self.key_path).exists():
            kwargs["key_filename"] = str(self.key_path)
            kwargs["look_for_keys"] = False
        elif self.password:
            kwargs["password"] = self.password
        else:
            kwargs["allow_agent"] = True
            kwargs["look_for_keys"] = True
        self.client.connect(**kwargs)

    def run(self, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
        assert self.client is not None
        _, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        errs = stderr.read().decode(errors="replace")
        rc = stdout.channel.recv_exit_status()
        return out, errs, rc

    def upload(self, local_path: str, remote_path: str) -> None:
        assert self.client is not None
        sftp = self.client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

    def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None


# Langkah remote yang GAGAL selama `server add` (rc != 0). Dulu semua rc diabaikan
# sehingga setup "berhasil" padahal user admin tak punya NOPASSWD sudo.
_FAILED_STEPS: list = []

SERVER_FILES = ("odin_agent.py", "run.sh", "odin-dispatch.sh")


def _upload_server_file(ssh: "SSHSession", pp: str, name: str, mode: str) -> bool:
    """Upload satu file server ke /home/odin dengan izin & owner benar."""
    src = ODIN_INSTALL_DIR / "server" / name
    if not src.exists():
        err(f"File tidak ditemukan: {src}")
        return False
    dest = f"{ODIN_REMOTE_HOME}/{name}"
    tmp = f"/tmp/.odin-upload-{name}"
    try:
        ssh.upload(str(src), tmp)
    except Exception as e:
        err(f"Gagal upload {name}: {e}")
        return False
    out, errs, rc = ssh.run(f"{pp}mv {tmp} {dest} && {pp}chown odin:odin {dest} && "
                            f"{pp}chmod {mode} {dest}")
    if rc != 0:
        _FAILED_STEPS.append((f"pasang {name}", (errs or out).strip()[:160] or f"rc={rc}"))
        return False
    return True


# Pola sudoers RENTAN dari ODIN <= v2.2 (lihat SUDOERS_ODIN untuk alasannya).
# Server yang sudah terpasang TIDAK ikut terperbaiki oleh `server add` (aturan
# dilewati bila /etc/sudoers.d/odin sudah ada) maupun `odin update` (tak menyentuh
# sudoers) — jadi keberadaannya harus dideteksi & dilaporkan secara eksplisit.
VULNERABLE_SUDOERS_PATTERNS = [
    ("/usr/bin/tail", "tail dgn wildcard path → baca file apa pun sebagai root"),
    ("certbot renew *", "certbot dgn argumen bebas → --deploy-hook jalan sebagai root"),
    ("/usr/local/bin/*-deploy", "wildcard nama skrip → eksekusi skrip apa pun sbg root"),
]


def _audit_remote_sudoers(ssh: "SSHSession", pp: str = "") -> list[str]:
    """Daftar temuan pada /etc/sudoers.d/odin di server. Kosong = aman."""
    out, _, rc = ssh.run(f"{pp}cat /etc/sudoers.d/odin 2>/dev/null")
    if rc != 0 or not out.strip():
        return []
    rules = "\n".join(l for l in out.splitlines()
                       if l.strip() and not l.lstrip().startswith("#"))
    found = [why for pat, why in VULNERABLE_SUDOERS_PATTERNS if pat in rules]
    if "journalctl" in rules and "journalctl --no-pager" not in rules:
        found.append("journalctl tanpa --no-pager → pager sebagai root = shell root")
    return found


def _audit_remote_key(ssh: "SSHSession", pp: str = "") -> bool:
    """True bila SEMUA kunci di authorized_keys sudah ber-forced-command."""
    out, _, rc = ssh.run(f"{pp}cat {ODIN_REMOTE_HOME}/.ssh/authorized_keys 2>/dev/null")
    if rc != 0:
        return True                     # tak terbaca → jangan mengklaim apa pun
    lines = [l for l in out.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        return True
    return all("command=" in l for l in lines)


def _write_authorized_keys(ssh: "SSHSession", pubkey: str, pp: str = "") -> tuple[bool, str]:
    """Tulis baris forced-command untuk pubkey ini. Return (sukses, isi_lama)."""
    ak = f"{ODIN_REMOTE_HOME}/.ssh/authorized_keys"
    old, _, _ = ssh.run(f"{pp}cat {ak} 2>/dev/null")
    body = pubkey.split()[1] if len(pubkey.split()) > 1 else pubkey
    kept = [l for l in old.splitlines() if l.strip() and body not in l]
    content = "\n".join(kept + [_authorized_keys_line(pubkey)]) + "\n"
    _, _, rc = ssh.run(f"{pp}mkdir -p {ODIN_REMOTE_HOME}/.ssh && "
                       f"{pp}chmod 700 {ODIN_REMOTE_HOME}/.ssh && "
                       f"printf '%s' {shlex.quote(content)} | {pp}tee {ak} > /dev/null && "
                       f"{pp}chmod 600 {ak}")
    return rc == 0, old


def _restore_authorized_keys(ssh: "SSHSession", old: str, pp: str = "") -> None:
    ak = f"{ODIN_REMOTE_HOME}/.ssh/authorized_keys"
    ssh.run(f"printf '%s' {shlex.quote(old)} | {pp}tee {ak} > /dev/null && {pp}chmod 600 {ak}")


ODIN_SSH_CONFIG = ODIN_DIR / "ssh_config"


def _write_ssh_config_entry(alias: str, host: str, port: str, key_path: "Path") -> None:
    """Tulis entry SSH ke ~/.odin/ssh_config, di-Include dari ~/.ssh/config.

    File terpisah supaya: (a) cek tabrakan alias bisa EKSAK (dulu substring —
    'vps' dianggap sudah ada karena ada 'vps-app'), (b) `odin server remove`
    bisa mencabut entry-nya tanpa menyentuh config SSH milik user.
    IdentitiesOnly/BatchMode wajib: tanpa itu SSH bisa kehabisan jatah auth
    ('Too many authentication failures') atau menggantung menunggu password
    di pipe MCP yang tak punya TTY."""
    ODIN_SSH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    entry = (f"Host {alias}\n"
             f"    HostName {host}\n"
             f"    Port {port}\n"
             f"    User odin\n"
             f"    IdentityFile {key_path}\n"
             f"    IdentitiesOnly yes\n"
             f"    BatchMode yes\n"
             f"    StrictHostKeyChecking accept-new\n"
             f"    RequestTTY no\n"
             f"    LogLevel ERROR\n")
    blocks = _read_ssh_blocks()
    blocks[alias] = entry
    ODIN_SSH_CONFIG.write_text(
        "# Dikelola oleh ODIN CLI — jangan edit manual.\n"
        "# Di-Include dari ~/.ssh/config.\n\n" + "\n".join(blocks.values()))
    try:
        ODIN_SSH_CONFIG.chmod(0o600)
    except OSError:
        pass
    _ensure_ssh_include()
    ok(f"~/.odin/ssh_config — entry '{alias}' ditulis")


def _read_ssh_blocks() -> dict:
    """Parse ~/.odin/ssh_config jadi {alias: blok}."""
    blocks: dict = {}
    if not ODIN_SSH_CONFIG.exists():
        return blocks
    current = None
    for line in ODIN_SSH_CONFIG.read_text().splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("Host "):
            current = stripped.split(None, 1)[1].strip()
            blocks[current] = line
        elif current and (stripped.startswith("#") or not stripped):
            continue
        elif current:
            blocks[current] += line
    return blocks


def _remove_ssh_config_entry(alias: str) -> bool:
    """Cabut satu entry dari ~/.odin/ssh_config (cocok EKSAK, bukan substring)."""
    blocks = _read_ssh_blocks()
    if alias not in blocks:
        return False
    blocks.pop(alias)
    ODIN_SSH_CONFIG.write_text(
        "# Dikelola oleh ODIN CLI — jangan edit manual.\n"
        "# Di-Include dari ~/.ssh/config.\n\n" + "\n".join(blocks.values()))
    return True


def _ensure_ssh_include() -> None:
    """Pastikan ~/.ssh/config memuat `Include ~/.odin/ssh_config` PALING ATAS.

    Harus di atas: pada SSH, opsi pertama yang cocok menang — bila user punya
    blok `Host *` di atasnya, opsi kita akan kalah."""
    cfg = Path.home() / ".ssh" / "config"
    cfg.parent.mkdir(mode=0o700, exist_ok=True)
    include = "Include ~/.odin/ssh_config"
    existing = cfg.read_text() if cfg.exists() else ""
    if include in existing:
        return
    cfg.write_text(f"{include}\n\n{existing}" if existing else f"{include}\n")
    try:
        cfg.chmod(0o600)
    except OSError:
        pass
    ok("~/.ssh/config — 'Include ~/.odin/ssh_config' ditambahkan")


# JSON-RPC minimal untuk membuktikan MCP benar-benar hidup (bukan sekadar `test -f`).
_MCP_INIT = (
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":'
    '"2024-11-05","capabilities":{},"clientInfo":{"name":"odin-doctor","version":"1"}}}\n'
    '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
    '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n'
)


def _mcp_handshake(ssh: "SSHSession", project: str = "",
                   pp: str | None = None) -> tuple[bool, str]:
    """Jalankan run.sh lewat SSH & lakukan handshake MCP sungguhan.

    `test -f run.sh` tak membuktikan apa pun: run.sh rusak atau syntax error di
    agent tetap lulus. Ini mengirim initialize + tools/list lalu menghitung tool
    yang dikembalikan.

    pp: diisi saat sesi SSH masih sebagai ADMIN (alur `server add`). Agent lalu
    dijalankan lewat `su - odin` — kalau tidak, memory/audit dibuat sebagai root
    di /home/odin dan user odin kehilangan akses ke datanya sendiri."""
    args = f" --project {shlex.quote(project)}" if project else ""
    inner = (f"printf %s {shlex.quote(_MCP_INIT)} | "
             f"timeout 25 {ODIN_REMOTE_HOME}/run.sh{args} 2>/tmp/.odin-doctor.err")
    cmd = f"{pp}su - odin -c {shlex.quote(inner)}" if pp is not None else inner
    try:
        out, _, _ = ssh.run(cmd, timeout=40)
    except Exception as e:
        return False, f"eksekusi gagal: {e}"
    tools = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            tools = ((msg.get("result") or {}).get("tools")) or []
    if tools:
        return True, f"{len(tools)} tools"
    errs, _, _ = ssh.run("cat /tmp/.odin-doctor.err 2>/dev/null | tail -3; "
                         "rm -f /tmp/.odin-doctor.err")
    return False, errs.strip()[:200] or "tidak ada respons tools/list"


# ── Sudoers ODIN ────────────────────────────────────────────────────────────
# PENTING: sudo mencocokkan argumen dengan fnmatch(3) TANPA FNM_PATHNAME, jadi `*`
# ikut cocok dengan `/` dan `..`. Aturan seperti `tail -n * /var/log/*` karena itu
# BUKAN "hanya /var/log" — `tail -n 1 /var/log/../../etc/shadow` lolos dan membaca
# shadow sebagai root. Semua aturan berwildcard-path dihapus; yang tersisa hanya
# perintah dengan argumen tertutup atau wildcard yang tak menyentuh path.
SUDOERS_ODIN = """\
# ODIN MCP Agent — limited privileges (dikelola `odin server add`)
#
# ATURAN: JANGAN menambah wildcard pada ARGUMEN PATH. sudo memakai fnmatch tanpa
# FNM_PATHNAME sehingga `*` cocok dengan `/` dan `..` → path traversal ke file
# apa pun sebagai root. Batasi ke daftar perintah tertutup.
Defaults:odin !requiretty
Defaults:odin env_reset

# CATATAN: `systemctl status/is-active/is-enabled` TIDAK butuh sudo — dijalankan
# sebagai user odin. Aturan sudo untuk itu sengaja tak ada (mengurangi permukaan).

# Kendali service: daftar TERTUTUP, tanpa wildcard argumen bebas.
odin ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx, \\
                          /usr/bin/systemctl reload nginx, \\
                          /usr/bin/systemctl restart php*-fpm, \\
                          /usr/bin/systemctl reload php*-fpm, \\
                          /usr/bin/systemctl restart mysql, \\
                          /usr/bin/systemctl restart supervisor, \\
                          /usr/bin/systemctl restart redis-server

# Log systemd: --no-pager WAJIB — tanpa itu `journalctl` membuka pager `less`
# sebagai root, dan `!/bin/sh` di dalam pager = shell root.
odin ALL=(root) NOPASSWD: /usr/bin/journalctl --no-pager *

# Inspeksi ringan (argumen tak menunjuk path yang bisa di-traverse).
odin ALL=(root) NOPASSWD: /usr/bin/df *, /usr/bin/free *, /usr/sbin/nginx -t, \\
                          /usr/bin/ufw status *

# Renewal sertifikat: TANPA argumen bebas — `certbot renew *` memungkinkan
# --deploy-hook='...' yang dieksekusi sebagai root.
odin ALL=(root) NOPASSWD: /usr/bin/certbot renew --quiet, /usr/bin/certbot renew

# CATATAN: `tail -n * /var/log/*` DIHAPUS (baca file apa pun sebagai root).
# Log dibaca sebagai user odin lewat tool tail_log. Bila benar-benar perlu membaca
# log milik root, buat wrapper yang me-realpath argumennya di bawah /var/log lalu
# daftarkan wrapper itu di sini — bukan `tail` mentah.
# CATATAN: `/usr/local/bin/*-deploy` DIHAPUS (wildcard nama = skrip apa pun yang
# bisa ditulis user lain). Daftarkan path deploy script yang spesifik bila perlu.
"""


def _install_sudoers(ssh: "SSHSession", pp: str) -> bool:
    """Pasang /etc/sudoers.d/odin dengan validasi visudo + rollback.

    Sudoers rusak = seluruh sudo di server ikut rusak. Karena itu: tulis ke file
    sementara, validasi `visudo -cf`, baru pindahkan. Gagal validasi → dibuang."""
    tmp = "/tmp/.odin-sudoers.new"
    payload = SUDOERS_ODIN.replace("'", "'\\''")
    _, _, rc = ssh.run(f"printf '%s' '{payload}' > {tmp} && {pp}chown root:root {tmp} && "
                       f"{pp}chmod 440 {tmp}")
    if rc != 0:
        err("Gagal menulis sudoers sementara.")
        return False
    out, errs, rc = ssh.run(f"{pp}visudo -cf {tmp}")
    if rc != 0:
        ssh.run(f"{pp}rm -f {tmp}")
        err(f"Sudoers TIDAK valid — dibatalkan agar sudo server tak rusak: "
            f"{(errs or out).strip()[:200]}")
        return False
    _, errs, rc = ssh.run(f"{pp}mv {tmp} /etc/sudoers.d/odin && {pp}chmod 440 /etc/sudoers.d/odin")
    if rc != 0:
        ssh.run(f"{pp}rm -f {tmp}")
        err(f"Gagal memasang sudoers: {errs.strip()[:200]}")
        return False
    return True


def _authorized_keys_line(pubkey: str) -> str:
    """Baris authorized_keys dengan forced-command — kunci ODIN tak memberi shell.

    Tanpa ini, siapa pun yang memegang private key mendapat shell interaktif
    ber-TTY; `sudo journalctl`/`systemctl status` lalu bisa dipakai untuk spawn
    shell root lewat pager. Dengan `restrict,command=` kunci hanya bisa memanggil
    dispatcher yang cuma menerima `run.sh [--project <nama>]`."""
    return f'restrict,command="{ODIN_DISPATCH_REMOTE}" {pubkey}'


# ── odin server add ─────────────────────────────────────────────────────────
def cmd_server_add() -> str | None:
    """Wizard tambah server. Return alias bila tuntas, None bila gagal/dibatalkan."""
    ensure_dirs()
    banner("ODIN — Tambah Server")

    alias = ask_input("Alias server")
    if re.search(r"[^a-zA-Z0-9_-]", alias):
        err("Alias hanya boleh huruf, angka, - dan _")
        return
    if (SERVERS_DIR / f"{alias}.yaml").exists():
        err(f"Server '{alias}' sudah terdaftar. Gunakan 'odin server remove {alias}' dulu.")
        return

    host = ask_input("Hostname / IP")
    port = ask_input("Port SSH", default="22")
    user = ask_input("User sudoers", default="root")
    password = getpass.getpass(f"  {_c('1', 'Password SSH')}: ")
    print()

    info(f"Menghubungi {host}:{port} sebagai {user}...")
    ssh = SSHSession(host, int(port), user, password=password)
    try:
        ssh.connect()
    except Exception as e:
        err(f"Gagal koneksi SSH: {e}")
        return

    ok("Koneksi SSH berhasil")

    _FAILED_STEPS.clear()

    def must(cmd: str, label: str, timeout: int = 60) -> bool:
        """Jalankan langkah remote & CATAT bila gagal (rc != 0)."""
        out, errs, rc = ssh.run(cmd, timeout=timeout)
        if rc != 0:
            _FAILED_STEPS.append((label, (errs or out).strip()[:160] or f"rc={rc}"))
            return False
        return True

    # [1] Deteksi OS
    out, _, _ = ssh.run("cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'\"' -f2")
    os_name = out.strip() or "Unknown"
    out, _, _ = ssh.run("python3 --version 2>&1")
    py_ver = out.strip() or "?"
    progress(1, 8, f"Deteksi server: {os_name}, {py_ver}")

    pp = ""
    out, _, _ = ssh.run("whoami")
    if out.strip() != "root":
        pp = "sudo "

    # [2] Buat user odin
    _, _, rc = ssh.run("id odin 2>/dev/null")
    if rc != 0:
        out, errs, rc = ssh.run(f"{pp}useradd -m -s /bin/bash odin")
        if rc != 0:
            err(f"Gagal membuat user odin: {errs.strip()}")
            ssh.close()
            return
        progress(2, 8, "User odin dibuat")
    else:
        progress(2, 8, "User odin sudah ada")

    # [3] Setup sudoers
    _, _, rc = ssh.run(f"{pp}test -f /etc/sudoers.d/odin")
    if rc != 0:
        if not _install_sudoers(ssh, pp):
            ssh.close()
            return
        progress(3, 8, "Sudoers odin dikonfigurasi")
    else:
        progress(3, 8, "Sudoers odin sudah ada")

    # [4] Install venv + mcp[cli]
    _, _, rc = ssh.run("/home/odin/.venv/bin/python -c 'import mcp' 2>/dev/null")
    if rc != 0:
        info("  Menginstall venv + mcp[cli] (bisa 1-2 menit)...")
        # Ubuntu/Debian: `python3 -m venv` butuh paket python3-venv (ensurepip).
        # Tanpa ini venv terbentuk tanpa pip → "pip: No such file or directory".
        ssh.run(f"{pp}DEBIAN_FRONTEND=noninteractive apt-get update -qq", timeout=180)
        ssh.run(f"{pp}DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip", timeout=180)
        ssh.run(f"{pp}su - odin -c 'rm -rf /home/odin/.venv && python3 -m venv /home/odin/.venv'")
        out, errs, rc = ssh.run(
            f"{pp}su - odin -c '/home/odin/.venv/bin/pip install --quiet \"mcp[cli]\"'",
            timeout=180
        )
        if rc != 0:
            err(f"Gagal install mcp[cli]: {errs.strip()[:200]}")
            ssh.close()
            return
        progress(4, 8, "Venv + mcp[cli] terinstall")
    else:
        progress(4, 8, "Venv + mcp[cli] sudah ada")

    # [5] Upload odin_agent.py
    if not _upload_server_file(ssh, pp, "odin_agent.py", "600"):
        ssh.close()
        return
    progress(5, 8, "Upload odin_agent.py")

    # [6] Upload run.sh + dispatcher forced-command
    if not _upload_server_file(ssh, pp, "run.sh", "755"):
        ssh.close()
        return
    if not _upload_server_file(ssh, pp, "odin-dispatch.sh", "755"):
        ssh.close()
        return
    progress(6, 8, "Upload run.sh + odin-dispatch.sh")

    # [7] Generate + pasang SSH key (forced-command: kunci tak memberi shell)
    key_path = KEYS_DIR / alias
    if not key_path.exists():
        KEYS_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", f"odin-{alias}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    if key_path.exists():
        pubkey = key_path.with_suffix(".pub").read_text().strip()
        line = _authorized_keys_line(pubkey)
        ak = f"{ODIN_REMOTE_HOME}/.ssh/authorized_keys"
        must(f"{pp}mkdir -p {ODIN_REMOTE_HOME}/.ssh && {pp}chmod 700 {ODIN_REMOTE_HOME}/.ssh",
             "buat .ssh")
        # Isi disusun di laptop lalu ditulis SEKALI lewat `tee`. Redirect remote
        # (`> file`) dieksekusi oleh shell user admin, bukan root — akan gagal bila
        # admin bukan root. Sekalian idempoten: baris lama untuk kunci yang SAMA
        # dibuang (dulu `tee -a` menumpuk duplikat tiap `server add` diulang).
        key_body = pubkey.split()[1] if len(pubkey.split()) > 1 else pubkey
        existing, _, _ = ssh.run(f"{pp}cat {ak} 2>/dev/null")
        kept = [l for l in existing.splitlines() if l.strip() and key_body not in l]
        content = "\n".join(kept + [line]) + "\n"
        must(f"printf '%s' {shlex.quote(content)} | {pp}tee {ak} > /dev/null",
             "pasang authorized_keys")
        must(f"{pp}chmod 600 {ak} && {pp}chown -R odin:odin {ODIN_REMOTE_HOME}/.ssh",
             "set izin .ssh")
        progress(7, 8, "SSH key terpasang (forced-command → run.sh saja)")
    else:
        warn("Gagal generate SSH key — koneksi berikutnya butuh password")
        progress(7, 8, "SSH key dilewati")

    # Buat direktori projects/ dan memory/ di server
    must(f"{pp}su - odin -c 'mkdir -p /home/odin/projects /home/odin/memory'",
         "buat projects/ & memory/")
    ssh.run(f"{pp}chmod 700 /home/odin/memory")
    ssh.run(f"{pp}chown -R odin:odin /home/odin")

    # [8] Bukti nyata: MCP hidup (initialize + tools/list), bukan sekadar `test -f`.
    live, detail = _mcp_handshake(ssh, pp=pp)
    if live:
        progress(8, 8, f"Handshake MCP OK ({detail})")
    else:
        _FAILED_STEPS.append(("handshake MCP", detail))
        progress(8, 8, "Handshake MCP GAGAL")

    if _FAILED_STEPS:
        err("Setup server TIDAK tuntas — langkah berikut gagal:")
        for step, detail in _FAILED_STEPS:
            err(f"  • {step}: {detail}")
        warn("Config lokal TIDAK ditulis. Perbaiki (mis. pastikan user punya sudo "
             "NOPASSWD), lalu ulangi 'odin server add'.")
        ssh.close()
        return

    ssh.close()

    # Tulis entry SSH ke ~/.odin/ssh_config (di-Include dari ~/.ssh/config).
    _write_ssh_config_entry(alias, host, port, key_path)

    # Tulis ~/.odin/servers/<alias>.yaml
    save_server(alias, {
        "name": alias,
        "host": host,
        "port": int(port),
        "user": "odin",
        "key": str(key_path),
        "os": os_name,
        "python": py_ver,
        "created": datetime.now().isoformat(timespec="seconds"),
        "odin_version": get_odin_version(),
    })

    print()
    ok(f"Server '{alias}' siap!")
    info("Selanjutnya: odin project add")
    return alias


# ── odin project add ────────────────────────────────────────────────────────
def cmd_project_add(args=None) -> str | None:
    """Wizard tambah project. Return nama project bila tuntas, None bila gagal."""
    ensure_dirs()
    servers = list_servers()
    if not servers:
        err("Belum ada server. Jalankan 'odin server add' dulu.")
        return None

    # Mode non-interaktif aktif bila --yes diberikan (pakai default & lewati prompt).
    auto = bool(getattr(args, "yes", False))

    def resolve(value, prompt_fn, flag_label, *, default=None):
        if value:
            return value
        if auto:
            if default is not None:
                return default
            err(f"--{flag_label} wajib dalam mode non-interaktif.")
            sys.exit(2)
        return prompt_fn()

    banner("ODIN — Tambah Project")

    # ── Nama ──
    name = resolve(getattr(args, "name", None),
                   lambda: ask_input("Nama project"), "name")
    if re.search(r"[^a-zA-Z0-9_-]", name):
        err("Nama project hanya boleh huruf, angka, - dan _")
        return
    if name in list_projects():
        err(f"Project '{name}' sudah ada.")
        return

    # ── Server ──
    server_alias = getattr(args, "server", None)
    if server_alias:
        if server_alias not in servers:
            err(f"Server '{server_alias}' tidak terdaftar. Tersedia: {', '.join(servers)}")
            return
    elif len(servers) == 1:
        server_alias = servers[0]
        info(f"Server: {server_alias} (satu-satunya)")
    elif auto:
        err(f"--server wajib (ada >1 server: {', '.join(servers)}).")
        sys.exit(2)
    else:
        idx = ask_choice("Pilih server", servers)
        server_alias = servers[idx - 1]

    # ── Remote root & workdir ──
    remote_root = resolve(getattr(args, "remote_root", None),
                          lambda: ask_input("Path di server", default=f"/var/www/{name}"),
                          "remote-root", default=f"/var/www/{name}")
    if not _validate_remote_root(remote_root):
        err(f"Path remote tidak valid: {remote_root!r} (harus absolut & tanpa karakter aneh).")
        return

    workdir = resolve(getattr(args, "workdir", None),
                      lambda: ask_input("Workdir lokal", default=str(Path.cwd())),
                      "workdir", default=str(Path.cwd()))
    local_workdir = str(Path(workdir).expanduser().resolve())
    print()

    server = load_server(server_alias)

    # SSH ke server sebagai odin (pakai key)
    key = server.get("key", "")
    ssh = SSHSession(server["host"], server["port"], "odin", key_path=key)
    try:
        ssh.connect()
    except Exception as e:
        err(f"Gagal SSH ke server sebagai odin: {e}")
        warn("Pastikan SSH key sudah terpasang (odin server add).")
        return

    # [1] Validasi path remote ada
    _, _, rc = ssh.run(f"test -d {shlex.quote(remote_root)}")
    if rc != 0:
        warn(f"{remote_root} tidak ditemukan di server.")
        if not (auto or confirm("Lanjutkan tanpa validasi?")):
            ssh.close()
            return

    # [2] Buat project conf di server (remote_root sudah tervalidasi aman)
    conf_lines = [
        f"PROJECT_NAME={name}",
        f"PROJECT_ROOT={remote_root}",
        f"ALLOWED_LOG_DIRS=/var/log,{remote_root}",
    ]
    conf_content = "\n".join(conf_lines) + "\n"
    ssh.run(f"cat > /home/odin/projects/{name}.conf << 'ODIN_EOF'\n{conf_content}ODIN_EOF")
    progress(1, 3, f"Server config: projects/{name}.conf")

    # [3] Buat memory dir di server
    ssh.run(f"mkdir -p /home/odin/memory/{name} && chmod 700 /home/odin/memory/{name}")
    progress(2, 3, f"Memory dir: memory/{name}/")

    ssh.close()

    # [4] Tulis config lokal kanonik (+ migrasi .mcp.json lama)
    settings_path = _write_local_mcp_config(local_workdir, server_alias, name)
    progress(3, 3, f"Workdir config: {settings_path}")

    # [5] Tulis ~/.odin/projects/<name>.yaml
    save_project(name, {
        "name": name,
        "server": server_alias,
        "remote_root": remote_root,
        "local_workdir": local_workdir,
        "created": datetime.now().isoformat(timespec="seconds"),
    })

    print()
    ok(f"Project '{name}' siap!")
    info(f"Untuk mulai: cd {local_workdir} && claude")
    return name


# ── odin server list ────────────────────────────────────────────────────────
def cmd_server_list() -> None:
    servers = list_servers()
    if not servers:
        info("Belum ada server terdaftar. Jalankan 'odin server add'.")
        return
    projects = list_projects()
    proj_map: dict[str, list[str]] = {}
    for p in projects:
        try:
            pd = _load_yaml(PROJECTS_DIR / f"{p}.yaml")
        except Exception:
            try:
                pd = _load_yaml(PROJECTS_DIR / f"{p}.json")
            except Exception:
                continue
        srv = pd.get("server", "")
        proj_map.setdefault(srv, []).append(p)

    print(f"\n{'Alias':<15} {'Host':<25} {'Port':<6} {'OS':<20} {'Projects'}")
    print("─" * 80)
    for alias in servers:
        d = _load_yaml(SERVERS_DIR / f"{alias}.yaml")
        plist = ", ".join(proj_map.get(alias, [])) or "(belum ada)"
        print(f"{d.get('name', alias):<15} {d.get('host', '?'):<25} {d.get('port', 22):<6} "
              f"{d.get('os', '?'):<20} {plist}")
    print()


# ── odin server remove ──────────────────────────────────────────────────────
def cmd_server_remove(alias: str, purge: bool = False) -> None:
    path = SERVERS_DIR / f"{alias}.yaml"
    if not path.exists():
        path = SERVERS_DIR / f"{alias}.json"
    if not path.exists():
        err(f"Server '{alias}' tidak ditemukan.")
        return
    projects_using = []
    for p in list_projects():
        try:
            pd = _load_yaml(PROJECTS_DIR / f"{p}.yaml")
        except Exception:
            try:
                pd = _load_yaml(PROJECTS_DIR / f"{p}.json")
            except Exception:
                continue
        if pd.get("server") == alias:
            projects_using.append(p)
    if projects_using:
        err(f"Server '{alias}' masih dipakai project: {', '.join(projects_using)}")
        err("Hapus project dulu: odin project remove <name>")
        return
    if not confirm(f"Hapus server '{alias}'?"):
        return

    key_file = KEYS_DIR / alias
    pub_file = key_file.with_suffix(".pub")

    # --purge: cabut dulu kunci dari server SEBELUM kunci lokal dihapus. Tanpa ini
    # server tetap memercayai kunci yang sudah "dihapus" di laptop.
    if purge and pub_file.exists():
        try:
            server = _load_yaml(path)
            ssh = SSHSession(server["host"], int(server.get("port", 22)), "odin",
                             key_path=str(key_file))
            ssh.connect()
            body = pub_file.read_text().strip().split()
            body = body[1] if len(body) > 1 else body[0]
            ak = "/home/odin/.ssh/authorized_keys"
            _, _, rc = ssh.run(f"grep -v -F {shlex.quote(body)} {ak} > {ak}.new && "
                               f"mv {ak}.new {ak} && chmod 600 {ak}")
            ssh.close()
            ok("Kunci ODIN dicabut dari authorized_keys server." if rc == 0
               else "Gagal mencabut kunci di server — cabut manual.")
        except Exception as e:
            warn(f"--purge gagal terhubung ke server ({e}) — cabut kunci manual di "
                 f"/home/odin/.ssh/authorized_keys.")

    path.unlink()
    if key_file.exists():
        key_file.unlink()
    if pub_file.exists():
        pub_file.unlink()
    _remove_ssh_config_entry(alias)
    _remove_legacy_ssh_entry(alias)
    ok(f"Server '{alias}' dihapus.")


def _remove_legacy_ssh_entry(alias: str) -> None:
    """Cabut entry lama yang ditulis langsung ke ~/.ssh/config (ODIN <= v2.2)."""
    ssh_config = Path.home() / ".ssh" / "config"
    if not ssh_config.exists():
        return
    lines = ssh_config.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    skip = False
    for line in lines:
        if line.strip().startswith("Host ") and line.strip().split()[1] == alias:
            skip = True
            continue
        if skip and line.strip().startswith("Host "):
            skip = False
        if skip and (line.startswith("    ") or line.startswith("\t") or not line.strip()):
            continue
        skip = False
        new_lines.append(line)
    ssh_config.write_text("".join(new_lines))


# ── odin server test ────────────────────────────────────────────────────────
def cmd_server_test(alias: str) -> None:
    server = load_server(alias)
    key = server.get("key", "")
    ssh = SSHSession(server["host"], server["port"], "odin", key_path=key)

    banner(f"ODIN — Test Server '{alias}'")
    try:
        ssh.connect()
        ok("SSH koneksi berhasil")
    except Exception as e:
        err(f"SSH gagal: {e}")
        return

    checks = [
        ("odin_agent.py", "test -f /home/odin/odin_agent.py && echo OK"),
        ("run.sh executable", "test -x /home/odin/run.sh && echo OK"),
        ("odin-dispatch.sh", "test -x /home/odin/odin-dispatch.sh && echo OK"),
        ("mcp module", "/home/odin/.venv/bin/python -c 'from mcp.server.fastmcp import FastMCP; print(\"OK\")' 2>&1"),
        ("projects/ dir", "test -d /home/odin/projects && echo OK"),
        ("memory/ dir", "test -d /home/odin/memory && echo OK"),
    ]
    for label, cmd in checks:
        out, _, rc = ssh.run(cmd)
        status = _c("0;32", "OK") if rc == 0 else _c("0;31", "FAIL")
        detail = out.strip() if rc == 0 and "OK" not in out else ""
        extra = f" ({detail})" if detail else ""
        print(f"  [{status}] {label}{extra}")

    # Bukti fungsional: MCP benar-benar merespons (bukan sekadar file ada).
    live, detail = _mcp_handshake(ssh)
    status = _c("0;32", "OK") if live else _c("0;31", "FAIL")
    print(f"  [{status}] handshake MCP ({detail})")

    out, _, _ = ssh.run("ls /home/odin/projects/*.conf 2>/dev/null")
    if out.strip():
        print(f"\n  Projects di server:")
        for line in out.strip().split("\n"):
            pname = Path(line).stem
            print(f"    - {pname}")
    else:
        info("  Belum ada project di server.")

    ssh.close()
    print()


# ── odin project list ───────────────────────────────────────────────────────
def cmd_project_list() -> None:
    projects = list_projects()
    if not projects:
        info("Belum ada project. Jalankan 'odin project add'.")
        return
    current = _detect_current_project()
    print(f"\n{'':2} {'Project':<15} {'Server':<15} {'Mode':<12} {'Workdir Lokal'}")
    print("─" * 75)
    for name in projects:
        try:
            d = _load_yaml(PROJECTS_DIR / f"{name}.yaml")
        except Exception:
            try:
                d = _load_yaml(PROJECTS_DIR / f"{name}.json")
            except Exception:
                continue
        marker = _c("1;32", "→") if name == current else " "
        mode_file = MODES_DIR / name
        mode = mode_file.read_text().strip() if mode_file.exists() else "deploy"
        print(f"{marker:>2} {d.get('name', name):<15} {d.get('server', '?'):<15} "
              f"{mode:<12} {d.get('local_workdir', '?')}")
    if current:
        print(f"\n  {_c('1;32', '→')} = project aktif (workdir cocok)")
    print()


# ── odin project status ────────────────────────────────────────────────────
def cmd_project_status(name: str | None = None) -> None:
    """Status project — deteksi dari cwd atau terima nama."""
    if not name:
        name = _detect_current_project()
    if not name:
        cwd = str(Path.cwd().resolve())
        err(f"Workdir saat ini ({cwd}) tidak cocok project terdaftar manapun.")
        info("Gunakan: odin project status <name>")
        info("Daftar project terdaftar:")
        cmd_project_list()
        return

    d = load_project(name)
    server_alias = d.get("server", "?")

    banner(f"Status Project '{name}'")

    workdir = d.get("local_workdir", "")
    settings_path = Path(workdir) / ".claude" / "settings.json" if workdir else None

    mode_file = MODES_DIR / name
    mode = mode_file.read_text().strip() if mode_file.exists() else "(default: deploy)"

    print(f"  {'Server':<16}: {server_alias}")
    print(f"  {'Remote path':<16}: {d.get('remote_root', '?')}")
    print(f"  {'Workdir lokal':<16}: {workdir}")
    print(f"  {'Mode':<16}: {mode}")
    print(f"  {'Dibuat':<16}: {d.get('created', '?')}")
    print()

    # MCP odin bisa per-repo (lama) ATAU global ~/.claude/settings.json (v2.2+).
    has_local, local_src = _has_odin_mcp(workdir) if workdir else (False, "")
    has_global = _has_global_odin_mcp()
    has_mcp = has_local or has_global
    if has_local:
        mcp_src = local_src
        active_settings = settings_path
    elif has_global:
        mcp_src = "global ~/.claude.json"
        active_settings = USER_CLAUDE_JSON
    else:
        mcp_src = ""
        active_settings = settings_path
    settings_label = "~/.claude.json (global)" if (has_global and not has_local) else "settings.json"
    checks = [
        ("Project YAML",
         (PROJECTS_DIR / f"{name}.yaml").exists() or (PROJECTS_DIR / f"{name}.json").exists()),
        ("Workdir ada", Path(workdir).is_dir() if workdir else False),
        (settings_label, active_settings.exists() if active_settings else False),
        (f"MCP config odin{f' ({mcp_src})' if mcp_src else ''}", has_mcp),
    ]

    for label, ok_val in checks:
        status = _c("0;32", "OK") if ok_val else _c("0;31", "FAIL")
        print(f"  [{status}] {label}")

    # SSH ping server (timeout 5s)
    print()
    info("Menguji koneksi ke server (timeout 5s)...")
    try:
        server = load_server(server_alias)
        key = server.get("key", "")
        ssh = SSHSession(server["host"], server["port"], "odin", key_path=key)
        ssh.connect()
        print(f"  [{_c('0;32', 'OK')}] SSH koneksi")

        _, _, rc = ssh.run(f"test -f /home/odin/projects/{name}.conf", timeout=5)
        print(f"  [{_c('0;32', 'OK') if rc == 0 else _c('0;31', 'FAIL')}] Server project conf")

        _, _, rc = ssh.run(f"test -d /home/odin/memory/{name}", timeout=5)
        print(f"  [{_c('0;32', 'OK') if rc == 0 else _c('0;31', 'FAIL')}] Server memory dir")

        ssh.close()
    except Exception as e:
        print(f"  [{_c('0;31', 'FAIL')}] SSH koneksi: {e}")
    print()


# ── odin project switch ───────────────────────────────────────────────────
def cmd_project_switch(name: str) -> None:
    """Buka sesi baru di workdir project."""
    d = load_project(name)
    workdir = d.get("local_workdir", "")
    if not workdir or not Path(workdir).is_dir():
        err(f"Workdir '{workdir}' tidak ditemukan.")
        return

    if sys.platform == "darwin":
        try:
            script = (f'tell application "Terminal" to do script '
                      f'"cd {workdir} && claude"')
            subprocess.run(["osascript", "-e", script], check=True,
                           capture_output=True)
            ok(f"Tab baru dibuka di {workdir}")
            return
        except Exception:
            pass

    info(f"Pindah ke project '{name}':")
    print(f"\n  cd {workdir} && claude\n")
    info(f"(Sesi MCP baru akan terhubung ke server {d.get('server', '?')}, project {name})")


# ── odin project remove ─────────────────────────────────────────────────────
def cmd_project_remove(name: str) -> None:
    path = PROJECTS_DIR / f"{name}.yaml"
    if not path.exists():
        path = PROJECTS_DIR / f"{name}.json"
    if not path.exists():
        err(f"Project '{name}' tidak ditemukan.")
        return
    project = _load_yaml(path)
    if not confirm(f"Hapus project '{name}' ({project.get('server', '?')}:{project.get('remote_root', '?')})?"):
        return
    path.unlink()
    # Hapus MCP entry di kedua lokasi (.claude/settings.json & .mcp.json)
    workdir = project.get("local_workdir", "")
    if workdir:
        _remove_local_mcp_config(workdir)
    # Hapus mode file
    mode_file = MODES_DIR / name
    if mode_file.exists():
        mode_file.unlink()
    ok(f"Project '{name}' dihapus. Memory di server tetap utuh.")


# ── odin project sync ───────────────────────────────────────────────────────
def cmd_project_sync(name: str | None = None, sync_all: bool = False) -> None:
    """Regenerasi config lokal (.claude/settings.json) dari manifest YAML.
    Berguna saat settings terhapus, guard-path berubah (repo ODIN dipindah),
    atau migrasi .mcp.json lama ke format kanonik."""
    ensure_dirs()
    if sync_all:
        names = list_projects()
    else:
        target = name or _detect_current_project()
        if not target:
            err("Tidak bisa menentukan project (cwd tak cocok project manapun).")
            info("Sebutkan nama: odin project sync <name>  — atau  odin project sync --all")
            return
        names = [target]

    if not names:
        info("Belum ada project terdaftar.")
        return

    banner("ODIN — Sync Config Lokal")
    synced = 0
    for nm in names:
        if nm not in list_projects():
            err(f"{nm}: tidak terdaftar — dilewati")
            continue
        d = load_project(nm)
        workdir = d.get("local_workdir", "")
        server_alias = d.get("server", "")
        if not workdir or not Path(workdir).is_dir():
            warn(f"{nm}: workdir '{workdir}' tak ada — dilewati")
            continue
        if server_alias not in list_servers():
            warn(f"{nm}: server '{server_alias}' tak terdaftar — dilewati")
            continue
        path = _write_local_mcp_config(workdir, server_alias, nm)
        ok(f"{nm} → {server_alias}: {path}")
        synced += 1

    print()
    info(f"Selesai: {synced}/{len(names)} project ter-sync.")


# ── odin update ─────────────────────────────────────────────────────────────
def cmd_update(alias: str) -> None:
    server = load_server(alias)
    key = server.get("key", "")

    banner(f"ODIN — Update Server '{alias}'")
    ssh = SSHSession(server["host"], server["port"], "odin", key_path=key)
    try:
        ssh.connect()
    except Exception as e:
        err(f"SSH gagal: {e}")
        return

    missing = [f for f in SERVER_FILES if not (ODIN_INSTALL_DIR / "server" / f).exists()]
    if missing:
        err(f"File sumber tidak ditemukan di repo lokal: {', '.join(missing)}")
        ssh.close()
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    modes = {"odin_agent.py": "600", "run.sh": "755", "odin-dispatch.sh": "755"}

    # 1) Backup versi lama — update yang gagal harus bisa dibalik.
    backup_dir = f"{ODIN_REMOTE_HOME}/.backup/{stamp}"
    ssh.run(f"mkdir -p {backup_dir}")
    for name in SERVER_FILES:
        ssh.run(f"cp -p {ODIN_REMOTE_HOME}/{name} {backup_dir}/ 2>/dev/null || true")

    # 2) Upload ke .new — belum menimpa apa pun.
    staged: list[str] = []
    for name in SERVER_FILES:
        dest_new = f"{ODIN_REMOTE_HOME}/{name}.new"
        try:
            ssh.upload(str(ODIN_INSTALL_DIR / "server" / name), dest_new)
        except Exception as e:
            err(f"Gagal upload {name}: {e}")
            ssh.run(f"rm -f {' '.join(ODIN_REMOTE_HOME + '/' + n + '.new' for n in SERVER_FILES)}")
            ssh.close()
            return
        staged.append(name)

    # 3) Compile-check SEBELUM mengganti — file rusak tak boleh pernah aktif.
    out, errs, rc = ssh.run(
        f"{ODIN_REMOTE_HOME}/.venv/bin/python -m py_compile {ODIN_REMOTE_HOME}/odin_agent.py.new")
    if rc != 0:
        err(f"odin_agent.py.new GAGAL compile di server — update dibatalkan: "
            f"{(errs or out).strip()[:200]}")
        ssh.run(f"rm -f {' '.join(ODIN_REMOTE_HOME + '/' + n + '.new' for n in staged)}")
        ssh.close()
        return
    for sh in ("run.sh", "odin-dispatch.sh"):
        out, errs, rc = ssh.run(f"bash -n {ODIN_REMOTE_HOME}/{sh}.new")
        if rc != 0:
            err(f"{sh}.new syntax error — update dibatalkan: {(errs or out).strip()[:200]}")
            ssh.run(f"rm -f {' '.join(ODIN_REMOTE_HOME + '/' + n + '.new' for n in staged)}")
            ssh.close()
            return

    # 4) Ganti atomik.
    for name in staged:
        ssh.run(f"mv {ODIN_REMOTE_HOME}/{name}.new {ODIN_REMOTE_HOME}/{name} && "
                f"chmod {modes[name]} {ODIN_REMOTE_HOME}/{name}")
        ok(f"{name} diupdate")

    out, _, _ = ssh.run("grep -m1 '__version__' /home/odin/odin_agent.py | cut -d'\"' -f2")
    new_ver = out.strip()

    # 5) Buktikan hasilnya benar-benar jalan; rollback bila tidak.
    live, detail = _mcp_handshake(ssh)
    if live:
        ok(f"Handshake MCP OK ({detail})")
    else:
        err(f"Handshake MCP GAGAL setelah update: {detail}")
        warn(f"Rollback: ssh {alias} 'cp -p {backup_dir}/* {ODIN_REMOTE_HOME}/'")

    # 6) Migrasi keamanan yang BISA dilakukan sebagai user odin: pasang
    #    forced-command pada kunci. Tanpa langkah ini, server yang sudah
    #    terpasang tak pernah ikut menerima perbaikan K3 — `server add` melewati
    #    server lama dan update sebelumnya tak menyentuh authorized_keys.
    if not _audit_remote_key(ssh):
        pub = Path(key).with_suffix(".pub") if key else None
        if pub and pub.exists():
            info("Kunci SSH belum ber-forced-command — memasang (dengan verifikasi)...")
            written, old_ak = _write_authorized_keys(ssh, pub.read_text().strip())
            if not written:
                warn("Gagal menulis authorized_keys — kunci dibiarkan apa adanya.")
            else:
                # Forced-command berlaku saat AUTENTIKASI, jadi sesi ini tak
                # membuktikan apa pun. Buka koneksi BARU untuk mengujinya; gagal
                # → kembalikan isi lama supaya server tak terkunci dari ODIN.
                probe = SSHSession(server["host"], server["port"], "odin", key_path=key)
                live2 = False
                try:
                    probe.connect()
                    live2, detail2 = _mcp_handshake(probe)
                    probe.close()
                except Exception as e:
                    detail2 = str(e)
                if live2:
                    ok("Kunci SSH kini forced-command (hanya bisa meluncurkan run.sh)")
                else:
                    _restore_authorized_keys(ssh, old_ak)
                    err(f"Verifikasi forced-command GAGAL ({detail2}) — authorized_keys "
                        f"dikembalikan ke isi semula. Server tetap bisa diakses.")
        else:
            warn(f"Kunci publik lokal tak ditemukan — jalankan: odin server harden {alias}")

    # 7) Sudoers hanya bisa diperbaiki dengan hak admin — laporkan, jangan diam.
    findings = _audit_remote_sudoers(ssh)
    if findings:
        err("SUDOERS SERVER MASIH RENTAN:")
        for f in findings:
            err(f"  • {f}")
        warn(f"Perbaiki: odin server harden {alias}  (butuh kredensial admin/root)")

    info(f"Backup versi lama: {backup_dir}")
    ssh.close()
    ok(f"Server '{alias}' diupdate ke v{new_ver}")


# ── odin server harden ──────────────────────────────────────────────────────
def cmd_server_harden(alias: str) -> None:
    """Terapkan ulang pengerasan keamanan pada server yang SUDAH terpasang.

    `server add` melewati sudoers bila /etc/sudoers.d/odin sudah ada, dan `odin
    update` jalan sebagai user `odin` (tanpa hak menulis /etc/sudoers.d). Jadi
    server yang dipasang dengan ODIN <= v2.2 tetap memakai aturan sudoers rentan
    sampai perintah ini dijalankan dengan kredensial admin."""
    server = load_server(alias)
    banner(f"ODIN — Harden Server '{alias}'")

    info("Perintah ini memasang ulang sudoers (tanpa wildcard path), dispatcher "
         "forced-command, dan kunci SSH terbatas. Butuh user admin/sudo di server.")
    user = ask_input("User admin (sudo)", default="root")
    password = getpass.getpass(f"  {_c('1', 'Password SSH')}: ")
    print()

    ssh = SSHSession(server["host"], int(server.get("port", 22)), user, password=password)
    try:
        ssh.connect()
    except Exception as e:
        err(f"Gagal koneksi SSH: {e}")
        return
    ok("Koneksi SSH berhasil")

    out, _, _ = ssh.run("whoami")
    pp = "" if out.strip() == "root" else "sudo "

    before = _audit_remote_sudoers(ssh, pp)
    if before:
        warn("Temuan pada sudoers saat ini:")
        for f in before:
            warn(f"  • {f}")
    else:
        info("Sudoers saat ini tidak mengandung pola rentan yang dikenal.")

    # 1) Sudoers: backup → tulis baru → validasi visudo (rollback otomatis di dalam).
    ssh.run(f"{pp}cp -p /etc/sudoers.d/odin /etc/sudoers.d/odin.bak.$(date +%s) 2>/dev/null || true")
    if not _install_sudoers(ssh, pp):
        err("Sudoers TIDAK diganti (validasi gagal) — server dibiarkan apa adanya.")
        ssh.close()
        return
    ok("Sudoers diganti dengan aturan tanpa wildcard path (tervalidasi visudo)")

    # 2) Dispatcher forced-command harus ADA sebelum kunci dibatasi ke sana.
    if not _upload_server_file(ssh, pp, "odin-dispatch.sh", "755"):
        err("Gagal memasang odin-dispatch.sh — kunci TIDAK dibatasi (mencegah lockout).")
        ssh.close()
        return
    ok("odin-dispatch.sh terpasang")

    # 3) Kunci → forced-command, lalu VERIFIKASI lewat koneksi baru.
    key = server.get("key", "")
    pub = Path(key).with_suffix(".pub") if key else None
    if pub and pub.exists():
        written, old_ak = _write_authorized_keys(ssh, pub.read_text().strip(), pp)
        if not written:
            err("Gagal menulis authorized_keys.")
        else:
            probe = SSHSession(server["host"], int(server.get("port", 22)), "odin",
                               key_path=key)
            live = False
            try:
                probe.connect()
                live, detail = _mcp_handshake(probe)
                probe.close()
            except Exception as e:
                detail = str(e)
            if live:
                ok(f"Kunci SSH forced-command terverifikasi ({detail})")
            else:
                _restore_authorized_keys(ssh, old_ak, pp)
                err(f"Verifikasi GAGAL ({detail}) — authorized_keys dikembalikan. "
                    f"Pastikan run.sh & venv di server sehat (odin doctor {alias}).")
    else:
        warn(f"Kunci publik lokal tak ada di {key}.pub — lewati pembatasan kunci.")

    after = _audit_remote_sudoers(ssh, pp)
    ssh.close()
    print()
    if after:
        err("Masih ada temuan sudoers — periksa manual /etc/sudoers.d/odin.")
    else:
        ok(f"Server '{alias}' sudah dikeraskan.")


# ── odin doctor ─────────────────────────────────────────────────────────────
def cmd_doctor(alias: str) -> None:
    server = load_server(alias)
    key = server.get("key", "")

    banner(f"ODIN — Doctor '{alias}'")
    ssh = SSHSession(server["host"], server["port"], "odin", key_path=key)
    try:
        ssh.connect()
        ok("SSH koneksi berhasil")
    except Exception as e:
        err(f"SSH gagal: {e}")
        return

    checks = [
        ("odin_agent.py ada", "test -f /home/odin/odin_agent.py && echo OK"),
        ("run.sh executable", "test -x /home/odin/run.sh && echo OK"),
        ("mcp module", "/home/odin/.venv/bin/python -c 'import mcp' 2>/dev/null && echo OK"),
        ("projects/ dir", "test -d /home/odin/projects && echo OK"),
        ("memory/ dir", "test -d /home/odin/memory && echo OK"),
    ]
    for label, cmd in checks:
        out, _, rc = ssh.run(cmd)
        status = _c("0;32", "OK") if rc == 0 and "OK" in out else _c("0;31", "FAIL")
        print(f"  [{status}] {label}")

    # Postur keamanan — server lama tak otomatis ikut perbaikan v2.3.
    findings = _audit_remote_sudoers(ssh)
    if findings:
        print(f"  [{_c('0;31', 'FAIL')}] sudoers: {len(findings)} pola rentan")
        for f in findings:
            print(f"         • {f}")
    else:
        print(f"  [{_c('0;32', 'OK')}] sudoers tanpa pola rentan yang dikenal")
    if _audit_remote_key(ssh):
        print(f"  [{_c('0;32', 'OK')}] kunci SSH forced-command")
    else:
        print(f"  [{_c('0;31', 'FAIL')}] kunci SSH TANPA forced-command "
              f"(kunci memberi shell) → odin server harden {alias}")

    # Version
    out, _, _ = ssh.run("grep -m1 '__version__' /home/odin/odin_agent.py 2>/dev/null | cut -d'\"' -f2")
    ver = out.strip() or "?"
    print(f"  [{'INFO':^4}] ODIN version: v{ver}")

    # Disk
    out, _, _ = ssh.run("df -h / | tail -1 | awk '{print $5, $4}'")
    if out.strip():
        parts = out.strip().split()
        usage = parts[0] if parts else "?"
        avail = parts[1] if len(parts) > 1 else "?"
        print(f"  [{'INFO':^4}] Disk: {usage} used, {avail} available")

    # Memory
    out, _, _ = ssh.run("free -h 2>/dev/null | grep Mem | awk '{print $3\"/\"$2}'")
    if out.strip():
        print(f"  [{'INFO':^4}] Memory: {out.strip()}")

    # Projects — plus handshake MCP NYATA per project (initialize + tools/list).
    out, _, _ = ssh.run("ls /home/odin/projects/*.conf 2>/dev/null")
    if out.strip():
        print(f"\n  Projects:")
        for line in out.strip().split("\n"):
            pname = Path(line.strip()).stem
            _, _, rc = ssh.run(f"test -d /home/odin/memory/{pname}")
            mem_ok = _c("0;32", "✓") if rc == 0 else _c("0;31", "✗")
            live, detail = _mcp_handshake(ssh, pname)
            mcp_ok = _c("0;32", f"✓ {detail}") if live else _c("0;31", f"✗ {detail}")
            print(f"    {pname} (memory: {mem_ok} | MCP: {mcp_ok})")
    else:
        info("  Belum ada project di server.")
        live, detail = _mcp_handshake(ssh)
        status = _c("0;32", "OK") if live else _c("0;31", "FAIL")
        print(f"  [{status}] handshake MCP ({detail})")

    ssh.close()
    print()


# ═══════════════════════════════════════════════════════════════════════════
# INSTALASI — setup / self-update / uninstall / doctor lokal / version
#
# Tata letak laptop (dibuat install.sh / install.ps1):
#   ~/.odin/app/          kode (git checkout tag) → ODIN_INSTALL_DIR
#   ~/.odin/app/.venv/    dependensi CLI terisolasi
#   ~/.odin/bin/odin      wrapper CLI  (+ ~/.local/bin/odin → wrapper)
#   ~/.odin/{keys,servers,projects,modes,ssh_config}   state — TIDAK PERNAH
#                         disentuh installer/self-update; hanya `uninstall --purge`.
# ═══════════════════════════════════════════════════════════════════════════
ODIN_BIN_WRAPPER = ODIN_DIR / "bin" / "odin"
LOCAL_BIN_LINK = Path.home() / ".local" / "bin" / "odin"
SLASH_CMD_DST = USER_CLAUDE_DIR / "commands" / "odin"
INSTALL_CMD = "curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.sh | bash"


def _git(args: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    """Jalankan git di ODIN_INSTALL_DIR; return (sukses, output gabungan)."""
    try:
        r = subprocess.run(["git", "-C", str(cwd or ODIN_INSTALL_DIR), *args],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _app_git_desc() -> str:
    okg, out = _git(["describe", "--tags", "--always", "--dirty"])
    return out if okg else "?"


def _app_ref_kind() -> tuple[str, str]:
    """('branch', nama) bila checkout mengikuti branch; ('tag'/'commit', desc) bila detached."""
    okg, out = _git(["symbolic-ref", "--quiet", "--short", "HEAD"])
    if okg and out:
        return "branch", out
    okg, out = _git(["describe", "--tags", "--exact-match"])
    return ("tag", out) if okg else ("commit", _app_git_desc())


def _latest_tag() -> str | None:
    okg, out = _git(["tag", "--list", "v*", "--sort=-v:refname"])
    if not okg or not out:
        return None
    return out.splitlines()[0].strip() or None


def _install_slash_commands() -> bool:
    src = ODIN_INSTALL_DIR / ".claude" / "commands" / "odin"
    if not src.is_dir():
        return False
    SLASH_CMD_DST.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.md"):
        shutil.copy2(f, SLASH_CMD_DST / f.name)
    return True


# ── odin version ────────────────────────────────────────────────────────────
def cmd_version() -> None:
    kind, ref = _app_ref_kind()
    print(f"ODIN v{get_odin_version()}")
    print(f"  kode   : {ODIN_INSTALL_DIR}  ({kind} {ref})")
    print(f"  state  : {ODIN_DIR}")
    print(f"  python : {sys.version.split()[0]}  ({sys.executable})")


# ── odin doctor (tanpa alias → laptop) ─────────────────────────────────────
def _local_checks() -> list[tuple[str, bool, str]]:
    """Pemeriksaan sisi laptop. Return [(label, ok, detail)] — murni, mudah diuji."""
    checks: list[tuple[str, bool, str]] = []
    v = sys.version_info
    checks.append(("Python >= 3.10", v >= (3, 10), f"{v.major}.{v.minor}.{v.micro}"))
    checks.append(("paramiko", paramiko is not None,
                   getattr(paramiko, "__version__", "") if paramiko else "pip install paramiko"))
    checks.append(("pyyaml", yaml is not None, "" if yaml else "fallback JSON aktif"))
    for tool, why in (("git", "self-update"), ("ssh", "koneksi server"),
                      ("claude", "Claude Code CLI")):
        path = shutil.which(tool)
        checks.append((tool, bool(path), path or f"tidak ada di PATH ({why})"))

    cli = ODIN_INSTALL_DIR / "client" / "odin_cli.py"
    kind, ref = _app_ref_kind()
    checks.append(("Kode ODIN", cli.is_file(), f"{ODIN_INSTALL_DIR} ({kind} {ref})"))
    checks.append(("Wrapper odin", ODIN_BIN_WRAPPER.is_file(),
                   str(ODIN_BIN_WRAPPER) if ODIN_BIN_WRAPPER.is_file() else "jalankan ulang installer"))
    link_ok = LOCAL_BIN_LINK.exists()
    checks.append(("~/.local/bin/odin", link_ok,
                   str(LOCAL_BIN_LINK.resolve()) if link_ok else "jalankan ulang installer"))
    on_path = str(LOCAL_BIN_LINK.parent) in os.environ.get("PATH", "").split(os.pathsep)
    checks.append(("~/.local/bin di PATH", on_path,
                   "" if on_path else 'export PATH="$HOME/.local/bin:$PATH"'))

    checks.append(("MCP odin global (~/.claude.json)", _has_global_odin_mcp(),
                   "" if _has_global_odin_mcp() else "odin global enable"))
    hook_ok = False
    sp = USER_CLAUDE_DIR / "settings.json"
    if sp.exists():
        try:
            hooks = (json.loads(sp.read_text()).get("hooks") or {}).get("PreToolUse") or []
            hook_ok = any(str(h.get("matcher", "")).startswith("mcp__odin__") for h in hooks)
        except (json.JSONDecodeError, OSError):
            pass
    checks.append(("Guard hook global", hook_ok, "" if hook_ok else "odin global enable"))
    checks.append(("Slash command /odin:*", (SLASH_CMD_DST / "status.md").is_file(),
                   str(SLASH_CMD_DST)))

    servers, projects = list_servers(), list_projects()
    checks.append(("Server terdaftar", bool(servers), ", ".join(servers) or "odin setup"))
    missing = []
    for p in projects:
        try:
            wd = load_project(p).get("local_workdir", "")
        except SystemExit:
            continue
        if wd and not Path(wd).is_dir():
            missing.append(f"{p} → {wd}")
    checks.append(("Project terdaftar", bool(projects) and not missing,
                   ", ".join(projects) if not missing else "workdir hilang: " + "; ".join(missing)))
    return checks


def cmd_doctor_local() -> None:
    banner("ODIN — Doctor (laptop)")
    bad = 0
    for label, good, detail in _local_checks():
        status = _c("0;32", " OK ") if good else _c("0;31", "FAIL")
        bad += 0 if good else 1
        print(f"  [{status}] {label}{'  — ' + detail if detail else ''}")
    print()
    if bad:
        warn(f"{bad} pemeriksaan gagal — lihat saran di sebelah kanan.")
    else:
        ok("Laptop siap.")
    servers = list_servers()
    if servers:
        info("Cek server: " + "  ".join(f"odin doctor {s}" for s in servers))


# ── odin setup ──────────────────────────────────────────────────────────────
def cmd_setup(args=None) -> None:
    """Satu wizard dari nol sampai siap: server → project → Claude Code → verifikasi.

    Setiap tahap idempoten: yang sudah ada dilewati, jadi aman dijalankan ulang."""
    ensure_dirs()
    banner("ODIN — Setup")
    print("  Empat tahap: server → project → Claude Code → verifikasi.")
    print("  Tahap yang sudah beres akan dilewati.\n")

    # ── 1. Server ──
    print(_c("1", "  [1/4] Server"))
    servers = list_servers()
    alias: str | None = None
    if servers:
        info(f"Server terdaftar: {', '.join(servers)}")
        if confirm("Tambah server BARU?", default=False):
            alias = cmd_server_add()
        elif len(servers) == 1:
            alias = servers[0]
        else:
            alias = servers[ask_choice("Pakai server", servers) - 1]
    else:
        info("Belum ada server — wizard 'server add' dimulai.")
        alias = cmd_server_add()
    if not alias:
        err("Tahap server tidak tuntas — setup dihentikan. Ulangi: odin setup")
        return
    ok(f"Server: {alias}")

    # ── 2. Project ──
    print("\n" + _c("1", "  [2/4] Project"))
    name: str | None = _detect_current_project()
    if name:
        ok(f"Folder ini sudah terdaftar sebagai project '{name}'")
    else:
        cwd = str(Path.cwd())
        info(f"Workdir: {cwd}")
        ns = argparse.Namespace(
            name=getattr(args, "name", None), server=alias,
            remote_root=getattr(args, "remote_root", None),
            workdir=getattr(args, "workdir", None) or cwd, yes=False)
        name = cmd_project_add(ns)
    if not name:
        err("Tahap project tidak tuntas — setup dihentikan. Ulangi: odin setup")
        return

    # ── 3. Claude Code ──
    print("\n" + _c("1", "  [3/4] Claude Code"))
    if _has_global_odin_mcp():
        ok("MCP odin global sudah aktif")
    elif confirm("Aktifkan MCP odin GLOBAL (otomatis tersedia di semua project terdaftar)?",
                 default=True):
        cmd_global_enable(migrate=True)
    else:
        info("Memakai config per-workdir (.claude/settings.json).")
    if not shutil.which("claude"):
        warn("Claude Code CLI tidak ditemukan di PATH — install: https://claude.com/claude-code")

    # ── 4. Verifikasi ──
    print("\n" + _c("1", "  [4/4] Verifikasi"))
    proj = load_project(name)
    server = load_server(proj["server"])
    ssh = SSHSession(server["host"], int(server.get("port", 22)), "odin",
                     key_path=server.get("key", ""))
    try:
        ssh.connect()
        live, detail = _mcp_handshake(ssh, name)
        ssh.close()
    except Exception as e:
        live, detail = False, str(e)
    if live:
        ok(f"Handshake MCP project '{name}' OK ({detail})")
    else:
        err(f"Handshake MCP GAGAL: {detail}")
        info(f"Diagnosa: odin doctor {proj['server']}")

    wd = proj.get("local_workdir", str(Path.cwd()))
    print()
    ok("Setup selesai." if live else "Setup selesai dengan peringatan.")
    print(f"\n  Mulai bekerja:\n    {_c('0;36', f'cd {wd} && claude')}\n")
    print(f"  Di dalam Claude Code: {_c('0;36', '/odin:status')}")
    warn("Claude Code memuat MCP saat START — buka sesi baru, bukan sesi yang sedang berjalan.")


# ── odin self-update ────────────────────────────────────────────────────────
def cmd_self_update(version: str | None = None) -> None:
    """Perbarui kode ODIN di laptop (bukan server — itu `odin update <alias>`)."""
    if not (ODIN_INSTALL_DIR / ".git").is_dir():
        err(f"{ODIN_INSTALL_DIR} bukan checkout git — pasang ulang dengan installer:")
        info(INSTALL_CMD)
        return
    banner("ODIN — Self-update")
    before_ver = get_odin_version()
    _, before_rev = _git(["rev-parse", "HEAD"])

    okg, out = _git(["fetch", "--quiet", "--tags", "origin"])
    if not okg:
        err(f"Gagal fetch: {out[:200]}")
        return

    kind, ref = _app_ref_kind()
    target = version
    if not target:
        # Mengikuti branch → perbarui branch itu; ter-pin ke tag → tag terbaru.
        target = ref if kind == "branch" else (_latest_tag() or "main")
    info(f"Target: {target}")

    okb, _ = _git(["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{target}"])
    if okb:
        okc, out = _git(["checkout", "--quiet", "-B", target, f"origin/{target}"])
    else:
        okc, out = _git(["checkout", "--quiet", "--detach", target])
    if not okc:
        err(f"Gagal checkout '{target}': {out[:200]}")
        return

    _, after_rev = _git(["rev-parse", "HEAD"])
    after_ver = get_odin_version()
    if before_rev == after_rev:
        ok(f"Sudah versi terbaru — v{after_ver} ({_app_git_desc()})")
    else:
        ok(f"v{before_ver} → v{after_ver} ({_app_git_desc()})")

    # Dependensi: hanya bila requirements berubah (hemat waktu, tanpa jaringan).
    _, changed = _git(["diff", "--name-only", before_rev, after_rev])
    venv_py = ODIN_INSTALL_DIR / ".venv" / "bin" / "python"
    if "requirements-cli.txt" in changed and venv_py.exists():
        info("requirements-cli.txt berubah — memperbarui dependensi")
        r = subprocess.run([str(venv_py), "-m", "pip", "install", "--quiet", "-r",
                            str(ODIN_INSTALL_DIR / "requirements-cli.txt")],
                           capture_output=True, text=True)
        ok("Dependensi diperbarui") if r.returncode == 0 else warn(
            f"pip gagal: {r.stderr.strip()[:200]}")

    if _install_slash_commands():
        ok(f"Slash command /odin:* diperbarui")
    if before_rev != after_rev:
        info("Server belum ikut berubah — jalankan: odin update <alias>")


# ── odin uninstall ──────────────────────────────────────────────────────────
def _purge_odin_settings_file(sp: Path) -> bool:
    """Cabut mcpServers.odin, hook mcp__odin__*, dan allow-list ODIN dari satu
    settings.json. Key lain milik user dipertahankan. Return True bila berubah."""
    if not sp.exists():
        return False
    try:
        s = json.loads(sp.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(s, dict):
        return False
    changed = False
    if "odin" in (s.get("mcpServers") or {}):
        s["mcpServers"].pop("odin")
        changed = True
        if not s["mcpServers"]:
            s.pop("mcpServers")
    hooks = s.get("hooks") or {}
    for ev in list(hooks):
        kept = [h for h in hooks[ev]
                if not str((h or {}).get("matcher", "")).startswith("mcp__odin__")]
        if len(kept) != len(hooks[ev]):
            changed = True
            if kept:
                hooks[ev] = kept
            else:
                hooks.pop(ev)
    if "hooks" in s and not s["hooks"]:
        s.pop("hooks")
    allow = (s.get("permissions") or {}).get("allow")
    if isinstance(allow, list):
        kept = [a for a in allow if not str(a).startswith("mcp__odin__")]
        if len(kept) != len(allow):
            changed = True
            if kept:
                s["permissions"]["allow"] = kept
            else:
                s["permissions"].pop("allow")
                if not s["permissions"]:
                    s.pop("permissions")
    if changed:
        try:
            sp.write_text(json.dumps(s, indent=2) + "\n")
        except OSError:
            return False
    return changed


def cmd_uninstall(purge: bool = False, yes: bool = False) -> None:
    banner("ODIN — Uninstall")
    print("  Akan dihapus dari laptop ini:")
    print(f"    • kode        {ODIN_INSTALL_DIR}")
    print(f"    • wrapper     {ODIN_BIN_WRAPPER}  dan  {LOCAL_BIN_LINK}")
    print(f"    • Claude Code entry MCP odin, hook guard, allow-list, slash command /odin:*")
    if purge:
        print(f"    • {_c('1;31', 'STATE')}       {ODIN_DIR}  (kunci SSH, registry server/project) — PERMANEN")
    else:
        print(f"    • state       {ODIN_DIR}/{{keys,servers,projects,modes}}  DIPERTAHANKAN "
              f"(hapus dengan --purge)")
    print("  Server TIDAK disentuh. Untuk mencabut kunci dari server jalankan dulu:")
    print("    odin server remove <alias> --purge\n")
    if not yes and not confirm("Lanjutkan?", default=False):
        info("Dibatalkan.")
        return
    if purge and not yes and not confirm(
            f"Yakin HAPUS {ODIN_DIR} beserta semua kunci SSH? Tidak bisa dibalik.", default=False):
        info("Dibatalkan.")
        return

    # 1) Claude Code
    if _claude_json_unregister_odin():
        ok("Entry MCP odin dicabut dari ~/.claude.json")
    if _purge_odin_settings_file(USER_CLAUDE_DIR / "settings.json"):
        ok("Hook guard & allow-list dicabut dari ~/.claude/settings.json")
    for name in list_projects():
        try:
            wd = load_project(name).get("local_workdir", "")
        except SystemExit:
            continue
        if wd and Path(wd).exists() and _purge_odin_local(wd):
            ok(f"Config per-workdir project '{name}' dibersihkan")
    if SLASH_CMD_DST.is_dir():
        shutil.rmtree(SLASH_CMD_DST, ignore_errors=True)
        ok("Slash command /odin:* dihapus")

    # 2) Wrapper & symlink
    for link in (LOCAL_BIN_LINK, Path("/usr/local/bin/odin"), Path("/usr/local/bin/odin-update")):
        try:
            if link.is_symlink() and str(link.resolve()).startswith(str(ODIN_DIR)):
                link.unlink()
                ok(f"{link} dihapus")
        except OSError:
            warn(f"Tidak bisa menghapus {link} — hapus manual (mungkin butuh sudo).")
    for compat in (ODIN_DIR / "client", ODIN_DIR / "server"):
        if compat.is_symlink():
            compat.unlink()
    shutil.rmtree(ODIN_DIR / "bin", ignore_errors=True)

    # 3) Kode
    shutil.rmtree(ODIN_INSTALL_DIR, ignore_errors=True)
    ok(f"Kode dihapus: {ODIN_INSTALL_DIR}")

    # 4) State (hanya --purge)
    if purge:
        cfg = Path.home() / ".ssh" / "config"
        if cfg.exists():
            try:
                lines = [l for l in cfg.read_text().splitlines(keepends=True)
                         if "Include ~/.odin/ssh_config" not in l]
                cfg.write_text("".join(lines))
            except OSError:
                pass
        for old in (Path.home() / ".odin_mode",):
            if old.exists():
                old.unlink()
        shutil.rmtree(ODIN_DIR, ignore_errors=True)
        ok(f"State dihapus: {ODIN_DIR}")
    else:
        info(f"State dipertahankan di {ODIN_DIR} — pasang ulang kapan saja: {INSTALL_CMD}")

    print()
    ok("ODIN di-uninstall dari laptop ini.")
    info('Baris PATH "~/.local/bin" di shell rc dibiarkan (tak berbahaya).')


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="odin",
        description="ODIN — CLI untuk manajemen multi-server & multi-project",
    )
    parser.add_argument("--version", action="version", version=f"ODIN CLI v{get_odin_version()}")
    sub = parser.add_subparsers(dest="command")

    # server
    server_p = sub.add_parser("server", help="Kelola server")
    server_sub = server_p.add_subparsers(dest="action")
    server_sub.add_parser("add", help="Tambah server baru (interaktif)")
    server_sub.add_parser("list", help="Daftar server terdaftar")
    rem_p = server_sub.add_parser("remove", help="Hapus server")
    rem_p.add_argument("alias", help="Alias server")
    rem_p.add_argument("--purge", action="store_true",
                       help="Cabut juga kunci ODIN dari authorized_keys di server")
    test_p = server_sub.add_parser("test", help="Test koneksi server")
    test_p.add_argument("alias", help="Alias server")
    hard_p = server_sub.add_parser(
        "harden", help="Terapkan ulang sudoers & kunci terbatas pada server lama")
    hard_p.add_argument("alias", help="Alias server")

    # project
    proj_p = sub.add_parser("project", help="Kelola project")
    proj_sub = proj_p.add_subparsers(dest="action")
    padd_p = proj_sub.add_parser("add", help="Tambah project baru (interaktif / via flags)")
    padd_p.add_argument("--name", help="Nama project (slug)")
    padd_p.add_argument("--server", help="Alias server tujuan")
    padd_p.add_argument("--remote-root", dest="remote_root", help="Path app di server (mis. /var/www/foo)")
    padd_p.add_argument("--workdir", help="Workdir lokal (default: cwd)")
    padd_p.add_argument("-y", "--yes", action="store_true",
                        help="Non-interaktif: pakai default & lewati konfirmasi")
    proj_sub.add_parser("list", help="Daftar project terdaftar")
    psync_p = proj_sub.add_parser("sync", help="Regenerasi config lokal dari manifest")
    psync_p.add_argument("name", nargs="?", help="Nama project (opsional, deteksi dari cwd)")
    psync_p.add_argument("--all", action="store_true", dest="all", help="Sync semua project")
    pstat_p = proj_sub.add_parser("status", help="Status project (lokal + server)")
    pstat_p.add_argument("name", nargs="?", help="Nama project (opsional, deteksi dari cwd)")
    pswitch_p = proj_sub.add_parser("switch", help="Buka project di tab baru")
    pswitch_p.add_argument("name", help="Nama project")
    prem_p = proj_sub.add_parser("remove", help="Hapus project")
    prem_p.add_argument("name", help="Nama project")

    # global (MCP scope-user → tersedia di semua project)
    glob_p = sub.add_parser("global", help="Kelola ODIN MCP global (semua project)")
    glob_sub = glob_p.add_subparsers(dest="action")
    genable_p = glob_sub.add_parser("enable", help="Aktifkan MCP odin di scope-user")
    genable_p.add_argument("--migrate", action="store_true",
                           help="Cabut entry per-workdir lama agar tak ada definisi/hook ganda")
    glob_sub.add_parser("disable", help="Nonaktifkan MCP odin global")

    # setup — satu wizard dari nol sampai siap
    setup_p = sub.add_parser("setup", help="Wizard lengkap: server → project → Claude Code → verifikasi")
    setup_p.add_argument("--name", help="Nama project (lewati prompt)")
    setup_p.add_argument("--remote-root", dest="remote_root", help="Path app di server")
    setup_p.add_argument("--workdir", help="Workdir lokal (default: cwd)")

    # update (server) / self-update (laptop)
    upd_p = sub.add_parser("update", help="Update ODIN di SERVER (agent, run.sh, dispatcher)")
    upd_p.add_argument("alias", help="Alias server")
    su_p = sub.add_parser("self-update", help="Update ODIN di LAPTOP ini (kode CLI, guard, launcher)")
    su_p.add_argument("version", nargs="?", help="Tag/branch tujuan (default: rilis terbaru / branch aktif)")

    # doctor: tanpa alias = laptop, dengan alias = server
    doc_p = sub.add_parser("doctor", help="Diagnostik laptop (tanpa alias) atau server (<alias>)")
    doc_p.add_argument("alias", nargs="?", help="Alias server (opsional)")

    # uninstall / version
    un_p = sub.add_parser("uninstall", help="Hapus ODIN dari laptop ini (state dipertahankan)")
    un_p.add_argument("--purge", action="store_true", help="Hapus juga state: kunci SSH, registry")
    un_p.add_argument("-y", "--yes", action="store_true", help="Tanpa konfirmasi")
    sub.add_parser("version", help="Versi, lokasi kode & state")

    args = parser.parse_args()

    if args.command == "server":
        if args.action == "add":
            cmd_server_add()
        elif args.action == "list":
            cmd_server_list()
        elif args.action == "remove":
            cmd_server_remove(args.alias, purge=getattr(args, 'purge', False))
        elif args.action == "harden":
            cmd_server_harden(args.alias)
        elif args.action == "test":
            cmd_server_test(args.alias)
        else:
            server_p.print_help()
    elif args.command == "project":
        if args.action == "add":
            cmd_project_add(args)
        elif args.action == "list":
            cmd_project_list()
        elif args.action == "sync":
            cmd_project_sync(getattr(args, "name", None), getattr(args, "all", False))
        elif args.action == "status":
            cmd_project_status(getattr(args, "name", None))
        elif args.action == "switch":
            cmd_project_switch(args.name)
        elif args.action == "remove":
            cmd_project_remove(args.name)
        else:
            proj_p.print_help()
    elif args.command == "global":
        if args.action == "enable":
            cmd_global_enable(getattr(args, "migrate", False))
        elif args.action == "disable":
            cmd_global_disable()
        else:
            glob_p.print_help()
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "update":
        cmd_update(args.alias)
    elif args.command == "self-update":
        cmd_self_update(getattr(args, "version", None))
    elif args.command == "doctor":
        if args.alias:
            cmd_doctor(args.alias)
        else:
            cmd_doctor_local()
    elif args.command == "uninstall":
        cmd_uninstall(purge=args.purge, yes=args.yes)
    elif args.command == "version":
        cmd_version()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
