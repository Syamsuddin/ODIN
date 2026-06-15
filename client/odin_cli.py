#!/usr/bin/env python3
"""
ODIN v2.0 — CLI untuk manajemen multi-server & multi-project.

Usage:
    odin server add|list|remove|test
    odin project add|list|remove
    odin update <server-alias>
    odin doctor <server-alias>
"""
from __future__ import annotations

__version__ = "2.0.0"

import argparse
import getpass
import json
import os
import re
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


# ── odin server add ─────────────────────────────────────────────────────────
def cmd_server_add() -> None:
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

    # [1] Deteksi OS
    out, _, _ = ssh.run("cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'\"' -f2")
    os_name = out.strip() or "Unknown"
    out, _, _ = ssh.run("python3 --version 2>&1")
    py_ver = out.strip() or "?"
    progress(1, 7, f"Deteksi server: {os_name}, {py_ver}")

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
        progress(2, 7, "User odin dibuat")
    else:
        progress(2, 7, "User odin sudah ada")

    # [3] Setup sudoers
    _, _, rc = ssh.run(f"{pp}test -f /etc/sudoers.d/odin")
    if rc != 0:
        sudoers = textwrap.dedent("""\
            # ODIN MCP Agent — limited privileges
            odin ALL=(root) NOPASSWD: /usr/bin/systemctl status *
            odin ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx, \\
                                      /usr/bin/systemctl reload nginx, \\
                                      /usr/bin/systemctl restart php*-fpm, \\
                                      /usr/bin/systemctl reload php*-fpm, \\
                                      /usr/bin/systemctl restart mysql, \\
                                      /usr/bin/systemctl restart supervisor, \\
                                      /usr/bin/systemctl restart redis-server
            odin ALL=(root) NOPASSWD: /usr/bin/tail -n * /var/log/*
            odin ALL=(root) NOPASSWD: /usr/bin/journalctl *
            odin ALL=(root) NOPASSWD: /usr/bin/certbot renew *
            odin ALL=(root) NOPASSWD: /usr/bin/df *, /usr/bin/free *, /usr/sbin/nginx -t, /usr/bin/ufw status *
            odin ALL=(root) NOPASSWD: /usr/local/bin/*-deploy
        """)
        ssh.run(f"echo '{sudoers}' | {pp}tee /etc/sudoers.d/odin > /dev/null")
        ssh.run(f"{pp}chmod 440 /etc/sudoers.d/odin")
        progress(3, 7, "Sudoers odin dikonfigurasi")
    else:
        progress(3, 7, "Sudoers odin sudah ada")

    # [4] Install venv + mcp[cli]
    _, _, rc = ssh.run("/home/odin/.venv/bin/python -c 'import mcp' 2>/dev/null")
    if rc != 0:
        info("  Menginstall venv + mcp[cli] (bisa 1-2 menit)...")
        ssh.run(f"{pp}su - odin -c 'python3 -m venv /home/odin/.venv'")
        out, errs, rc = ssh.run(
            f"{pp}su - odin -c '/home/odin/.venv/bin/pip install --quiet \"mcp[cli]\"'",
            timeout=180
        )
        if rc != 0:
            err(f"Gagal install mcp[cli]: {errs.strip()[:200]}")
            ssh.close()
            return
        progress(4, 7, "Venv + mcp[cli] terinstall")
    else:
        progress(4, 7, "Venv + mcp[cli] sudah ada")

    # [5] Upload odin_agent.py
    agent_src = ODIN_INSTALL_DIR / "server" / "odin_agent.py"
    if not agent_src.exists():
        err(f"File tidak ditemukan: {agent_src}")
        ssh.close()
        return
    ssh.upload(str(agent_src), "/tmp/odin_agent.py")
    ssh.run(f"{pp}mv /tmp/odin_agent.py /home/odin/odin_agent.py")
    ssh.run(f"{pp}chown odin:odin /home/odin/odin_agent.py")
    ssh.run(f"{pp}chmod 600 /home/odin/odin_agent.py")
    progress(5, 7, "Upload odin_agent.py")

    # [6] Upload run.sh
    run_src = ODIN_INSTALL_DIR / "server" / "run.sh"
    if not run_src.exists():
        err(f"File tidak ditemukan: {run_src}")
        ssh.close()
        return
    ssh.upload(str(run_src), "/tmp/odin_run.sh")
    ssh.run(f"{pp}mv /tmp/odin_run.sh /home/odin/run.sh")
    ssh.run(f"{pp}chown odin:odin /home/odin/run.sh")
    ssh.run(f"{pp}chmod 755 /home/odin/run.sh")
    progress(6, 7, "Upload run.sh")

    # [7] Generate + pasang SSH key
    key_path = KEYS_DIR / alias
    if not key_path.exists():
        KEYS_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", f"odin-{alias}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    if key_path.exists():
        pubkey = key_path.with_suffix(".pub").read_text().strip()
        ssh.run(f"{pp}mkdir -p /home/odin/.ssh && {pp}chmod 700 /home/odin/.ssh")
        ssh.run(f"echo '{pubkey}' | {pp}tee -a /home/odin/.ssh/authorized_keys > /dev/null")
        ssh.run(f"{pp}chmod 600 /home/odin/.ssh/authorized_keys")
        ssh.run(f"{pp}chown -R odin:odin /home/odin/.ssh")
        progress(7, 7, "SSH key terpasang")
    else:
        warn("Gagal generate SSH key — koneksi berikutnya butuh password")
        progress(7, 7, "SSH key dilewati")

    # Buat direktori projects/ dan memory/ di server
    ssh.run(f"{pp}su - odin -c 'mkdir -p /home/odin/projects /home/odin/memory'")
    ssh.run(f"{pp}chmod 700 /home/odin/memory")
    ssh.run(f"{pp}chown -R odin:odin /home/odin")

    ssh.close()

    # Tulis ~/.ssh/config entry
    ssh_config = Path.home() / ".ssh" / "config"
    ssh_config.parent.mkdir(mode=0o700, exist_ok=True)
    existing = ssh_config.read_text() if ssh_config.exists() else ""
    if f"Host {alias}" not in existing:
        entry = f"\nHost {alias}\n    HostName {host}\n    Port {port}\n    User odin\n    IdentityFile {key_path}\n    StrictHostKeyChecking accept-new\n"
        with open(ssh_config, "a") as f:
            f.write(entry)
        ok(f"~/.ssh/config — entry '{alias}' ditambahkan")

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


# ── odin project add ────────────────────────────────────────────────────────
def cmd_project_add() -> None:
    ensure_dirs()
    servers = list_servers()
    if not servers:
        err("Belum ada server. Jalankan 'odin server add' dulu.")
        return

    banner("ODIN — Tambah Project")

    name = ask_input("Nama project")
    if re.search(r"[^a-zA-Z0-9_-]", name):
        err("Nama project hanya boleh huruf, angka, - dan _")
        return
    if name in list_projects():
        err(f"Project '{name}' sudah ada.")
        return

    if len(servers) == 1:
        server_alias = servers[0]
        info(f"Server: {server_alias} (satu-satunya)")
    else:
        idx = ask_choice("Pilih server", servers)
        server_alias = servers[idx - 1]

    remote_root = ask_input("Path di server", default=f"/var/www/{name}")
    local_workdir = ask_input("Workdir lokal", default=str(Path.cwd()))
    local_workdir = str(Path(local_workdir).expanduser().resolve())
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
    _, _, rc = ssh.run(f"test -d {remote_root}")
    if rc != 0:
        warn(f"{remote_root} tidak ditemukan di server.")
        if not confirm("Lanjutkan tanpa validasi?"):
            ssh.close()
            return

    # [2] Buat project conf di server
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

    # [4] Tulis .claude/settings.json di workdir lokal
    claude_dir = Path(local_workdir) / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"

    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}

    settings.setdefault("mcpServers", {})["odin"] = {
        "type": "stdio",
        "command": "ssh",
        "args": [server_alias, "/home/odin/run.sh", "--project", name],
    }

    guard_path = str(ODIN_INSTALL_DIR / "client" / "odin_guard.py")

    settings.setdefault("hooks", {})
    settings["hooks"]["PreToolUse"] = [{
        "matcher": "mcp__odin__(run_command|service_action|laravel_deploy|run_tests|runbook|inspect_server|memory_write|memory_forget)",
        "hooks": [{
            "type": "command",
            "command": f"python3 '{guard_path}'",
            "timeout": 10,
        }],
    }]
    settings["hooks"]["PostToolUse"] = [{
        "matcher": "mcp__odin__inspect_server",
        "hooks": [{
            "type": "command",
            "command": f"python3 '{guard_path}'",
            "timeout": 10,
        }],
    }]

    read_only_tools = [
        "mcp__odin__server_info", "mcp__odin__tail_log",
        "mcp__odin__http_health_check", "mcp__odin__memory_recall",
        "mcp__odin__memory_digest", "mcp__odin__session_history",
        "mcp__odin__rollback_plan", "mcp__odin__inspect_server",
        "mcp__odin__audit_tail", "mcp__odin__runbook_templates",
    ]
    allow_list = settings.setdefault("permissions", {}).setdefault("allow", [])
    for tool in read_only_tools:
        if tool not in allow_list:
            allow_list.append(tool)

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
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
def cmd_server_remove(alias: str) -> None:
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
    path.unlink()
    key_file = KEYS_DIR / alias
    if key_file.exists():
        key_file.unlink()
    pub_file = key_file.with_suffix(".pub")
    if pub_file.exists():
        pub_file.unlink()
    _remove_ssh_config_entry(alias)
    ok(f"Server '{alias}' dihapus.")


def _remove_ssh_config_entry(alias: str) -> None:
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
        ("mcp module", "/home/odin/.venv/bin/python -c 'import mcp; print(mcp.__version__)' 2>&1"),
        ("projects/ dir", "test -d /home/odin/projects && echo OK"),
        ("memory/ dir", "test -d /home/odin/memory && echo OK"),
    ]
    for label, cmd in checks:
        out, _, rc = ssh.run(cmd)
        status = _c("0;32", "OK") if rc == 0 else _c("0;31", "FAIL")
        detail = out.strip() if rc == 0 and "OK" not in out else ""
        extra = f" ({detail})" if detail else ""
        print(f"  [{status}] {label}{extra}")

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

    checks = [
        ("Project YAML",
         (PROJECTS_DIR / f"{name}.yaml").exists() or (PROJECTS_DIR / f"{name}.json").exists()),
        ("Workdir ada", Path(workdir).is_dir() if workdir else False),
        ("settings.json", settings_path.exists() if settings_path else False),
    ]
    if settings_path and settings_path.exists():
        try:
            s = json.loads(settings_path.read_text())
            checks.append(("MCP config odin", "odin" in s.get("mcpServers", {})))
        except Exception:
            checks.append(("MCP config odin", False))

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
    # Hapus MCP entry di .claude/settings.json workdir
    workdir = project.get("local_workdir", "")
    if workdir:
        settings_path = Path(workdir) / ".claude" / "settings.json"
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
                settings.get("mcpServers", {}).pop("odin", None)
                if settings.get("mcpServers") or settings.get("hooks") or settings.get("permissions"):
                    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
                else:
                    settings_path.unlink()
                    claude_dir = settings_path.parent
                    if claude_dir.exists() and not any(claude_dir.iterdir()):
                        claude_dir.rmdir()
            except (json.JSONDecodeError, OSError):
                pass
    # Hapus mode file
    mode_file = MODES_DIR / name
    if mode_file.exists():
        mode_file.unlink()
    ok(f"Project '{name}' dihapus. Memory di server tetap utuh.")


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

    agent_src = ODIN_INSTALL_DIR / "server" / "odin_agent.py"
    run_src = ODIN_INSTALL_DIR / "server" / "run.sh"

    if not agent_src.exists() or not run_src.exists():
        err("File sumber tidak ditemukan di repo lokal.")
        ssh.close()
        return

    ssh.upload(str(agent_src), "/home/odin/odin_agent.py")
    ssh.run("chmod 600 /home/odin/odin_agent.py")
    ok("odin_agent.py diupdate")

    ssh.upload(str(run_src), "/home/odin/run.sh")
    ssh.run("chmod 755 /home/odin/run.sh")
    ok("run.sh diupdate")

    out, _, _ = ssh.run("grep -m1 '__version__' /home/odin/odin_agent.py | cut -d'\"' -f2")
    new_ver = out.strip()

    ssh.close()
    ok(f"Server '{alias}' diupdate ke v{new_ver}")


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

    # Projects
    out, _, _ = ssh.run("ls /home/odin/projects/*.conf 2>/dev/null")
    if out.strip():
        print(f"\n  Projects:")
        for line in out.strip().split("\n"):
            pname = Path(line.strip()).stem
            # Cek memory dir
            _, _, rc = ssh.run(f"test -d /home/odin/memory/{pname}")
            mem_ok = _c("0;32", "✓") if rc == 0 else _c("0;31", "✗")
            print(f"    {pname} (memory: {mem_ok})")
    else:
        info("  Belum ada project di server.")

    ssh.close()
    print()


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="odin",
        description="ODIN — CLI untuk manajemen multi-server & multi-project",
    )
    parser.add_argument("--version", action="version", version=f"ODIN CLI v{__version__}")
    sub = parser.add_subparsers(dest="command")

    # server
    server_p = sub.add_parser("server", help="Kelola server")
    server_sub = server_p.add_subparsers(dest="action")
    server_sub.add_parser("add", help="Tambah server baru (interaktif)")
    server_sub.add_parser("list", help="Daftar server terdaftar")
    rem_p = server_sub.add_parser("remove", help="Hapus server")
    rem_p.add_argument("alias", help="Alias server")
    test_p = server_sub.add_parser("test", help="Test koneksi server")
    test_p.add_argument("alias", help="Alias server")

    # project
    proj_p = sub.add_parser("project", help="Kelola project")
    proj_sub = proj_p.add_subparsers(dest="action")
    proj_sub.add_parser("add", help="Tambah project baru (interaktif)")
    proj_sub.add_parser("list", help="Daftar project terdaftar")
    pstat_p = proj_sub.add_parser("status", help="Status project (lokal + server)")
    pstat_p.add_argument("name", nargs="?", help="Nama project (opsional, deteksi dari cwd)")
    pswitch_p = proj_sub.add_parser("switch", help="Buka project di tab baru")
    pswitch_p.add_argument("name", help="Nama project")
    prem_p = proj_sub.add_parser("remove", help="Hapus project")
    prem_p.add_argument("name", help="Nama project")

    # update
    upd_p = sub.add_parser("update", help="Update ODIN di server")
    upd_p.add_argument("alias", help="Alias server")

    # doctor
    doc_p = sub.add_parser("doctor", help="Diagnostik server")
    doc_p.add_argument("alias", help="Alias server")

    args = parser.parse_args()

    if args.command == "server":
        if args.action == "add":
            cmd_server_add()
        elif args.action == "list":
            cmd_server_list()
        elif args.action == "remove":
            cmd_server_remove(args.alias)
        elif args.action == "test":
            cmd_server_test(args.alias)
        else:
            server_p.print_help()
    elif args.command == "project":
        if args.action == "add":
            cmd_project_add()
        elif args.action == "list":
            cmd_project_list()
        elif args.action == "status":
            cmd_project_status(getattr(args, "name", None))
        elif args.action == "switch":
            cmd_project_switch(args.name)
        elif args.action == "remove":
            cmd_project_remove(args.name)
        else:
            proj_p.print_help()
    elif args.command == "update":
        cmd_update(args.alias)
    elif args.command == "doctor":
        cmd_doctor(args.alias)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
