"""Test sistem instalasi v2.3: install.sh (integrasi nyata terhadap repo lokal)
dan perintah CLI setup/self-update/uninstall/doctor/version.

install.sh dijalankan SUNGGUHAN dengan HOME sementara dan ODIN_REPO_URL menunjuk
repo ini (file://), ODIN_SKIP_DEPS=1 agar tak butuh jaringan/pip. Ini menguji
tata letak, migrasi tata letak lama, idempotensi, dan PATH — bukan sekadar
`bash -n`.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))
import odin_cli  # noqa: E402


def _current_ref() -> str:
    """Ref yang bisa di-checkout dari clone: branch aktif, atau commit bila detached."""
    r = subprocess.run(["git", "-C", str(ROOT), "symbolic-ref", "--quiet", "--short", "HEAD"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _env(home: Path, **extra) -> dict:
    env = {
        "HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "SHELL": "/bin/zsh", "NO_COLOR": "1", "TERM": "dumb",
        "ODIN_REPO_URL": f"file://{ROOT}", "ODIN_VERSION": _current_ref(),
        "ODIN_SKIP_DEPS": "1",
    }
    env.update(extra)
    return env


def _run_install(home: Path, *args, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(ROOT / "install.sh"), "--yes", "--no-setup", *args],
                          env=_env(home, **extra), capture_output=True, text=True, timeout=180)


@unittest.skipUnless(shutil.which("git") and shutil.which("bash"), "butuh git & bash")
class TestInstallSh(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="odin-install-")
        self.home = Path(self._td)
        (self.home / ".zshrc").write_text("# rc\n")

    def tearDown(self):
        shutil.rmtree(self._td, ignore_errors=True)

    def test_fresh_install_layout(self):
        r = _run_install(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        odin = self.home / ".odin"
        self.assertTrue((odin / "app" / ".git").is_dir(), "kode harus di app/")
        self.assertTrue((odin / "app" / "client" / "odin_cli.py").is_file())
        wrapper = odin / "bin" / "odin"
        self.assertTrue(wrapper.is_file() and os.access(wrapper, os.X_OK))
        link = self.home / ".local" / "bin" / "odin"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), wrapper.resolve())
        # symlink kompatibilitas utk hook/MCP lama
        self.assertTrue((odin / "client").is_symlink())
        self.assertTrue((odin / "server").is_symlink())
        self.assertTrue((odin / "client" / "odin_guard.py").is_file())
        # slash command global
        self.assertTrue((self.home / ".claude" / "commands" / "odin" / "status.md").is_file())
        # PATH ditambahkan ke rc shell
        self.assertIn(".local/bin", (self.home / ".zshrc").read_text())
        # tidak pernah memakai sudo
        self.assertNotIn("sudo", r.stdout)

    def test_wrapper_runs_cli(self):
        r = _run_install(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = subprocess.run([str(self.home / ".odin" / "bin" / "odin"), "--version"],
                             env=_env(self.home), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        # Versi dibaca dari checkout YANG TERPASANG, bukan dipaku dan bukan dari
        # working tree: literal hanyut diam-diam tiap rilis (tertinggal di 2.3.0
        # sampai v2.5.0), sedangkan working tree bisa mendahului git karena
        # install.sh meng-clone — bukan menyalin — repo ini.
        installed = (self.home / ".odin" / "app" / "client" / "odin_cli.py").read_text()
        version = re.search(r'__version__ = "([^"]+)"', installed).group(1)
        self.assertIn(version, out.stdout + out.stderr)

    def test_idempotent_rerun(self):
        r1 = _run_install(self.home)
        r2 = _run_install(self.home)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("Memperbarui", r2.stdout)
        rc = (self.home / ".zshrc").read_text()
        self.assertEqual(rc.count(".local/bin"), 1, "PATH tidak boleh ditambah dua kali")
        self.assertEqual(r1.returncode, 0)

    def test_custom_home(self):
        alt = self.home / "custom-odin"
        r = _run_install(self.home, "--home", str(alt))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((alt / "app" / ".git").is_dir())
        self.assertIn(f'ODIN_HOME="{alt}"', (alt / "bin" / "odin").read_text())

    def test_unknown_ref_fails_clearly(self):
        r = _run_install(self.home, ODIN_VERSION="v0.0.0-tidak-ada")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tidak ditemukan", r.stdout + r.stderr)

    def test_legacy_layout_migrated_state_preserved(self):
        """ODIN <= v2.2: kode langsung di ~/.odin bersama keys/servers/projects.
        Installer harus memindahkan kode ke app/ TANPA menyentuh state."""
        odin = self.home / ".odin"
        subprocess.run(["git", "clone", "--quiet", f"file://{ROOT}", str(odin)], check=True,
                       capture_output=True)
        (odin / "keys").mkdir()
        (odin / "keys" / "vps").write_text("PRIVATE-KEY-DUMMY\n")
        (odin / "servers").mkdir()
        (odin / "servers" / "vps.yaml").write_text("name: vps\nhost: 1.2.3.4\n")
        (odin / "client" / "__pycache__").mkdir(parents=True, exist_ok=True)
        (odin / "client" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        (odin / "odin-update.sh").write_text("#!/bin/sh\n")

        r = _run_install(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Tata letak lama", r.stdout)
        # state utuh
        self.assertEqual((odin / "keys" / "vps").read_text(), "PRIVATE-KEY-DUMMY\n")
        self.assertTrue((odin / "servers" / "vps.yaml").is_file())
        # kode lama hilang, kode baru di app/
        self.assertFalse((odin / ".git").exists())
        self.assertFalse((odin / "odin-update.sh").exists())
        self.assertTrue((odin / "app" / ".git").is_dir())
        # jalur lama tetap valid lewat symlink
        self.assertTrue((odin / "client").is_symlink())
        self.assertTrue((odin / "client" / "odin_guard.py").is_file())
        self.assertTrue((odin / "server" / "odin_agent.py").is_file())

    def test_no_tty_without_yes_fails(self):
        """`curl | bash` tanpa TTY dan tanpa --yes harus gagal jelas, bukan menggantung."""
        env = _env(self.home)
        r = subprocess.run(["bash", str(ROOT / "install.sh"), "--no-setup"], env=env,
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        # Di CI tanpa /dev/tty → exit 1 dengan pesan; di terminal dev /dev/tty ada dan
        # prompt akan terjawab default — keduanya sah, yang penting tidak menggantung.
        self.assertIn(r.returncode, (0, 1))


class TestUninstallSh(unittest.TestCase):

    def test_syntax(self):
        for f in ("install.sh", "uninstall.sh"):
            r = subprocess.run(["bash", "-n", str(ROOT / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{f}: {r.stderr}")


# ═══════════════════════════════════════════════════════════════════════════
# CLI: perintah instalasi
# ═══════════════════════════════════════════════════════════════════════════
class _TempLayout:
    """Tata letak ODIN lengkap di direktori sementara + patch konstanta modul."""

    def __init__(self):
        self.td = Path(tempfile.mkdtemp(prefix="odin-cli-"))
        self.odin = self.td / ".odin"
        self.app = self.odin / "app"
        self.claude = self.td / ".claude"
        (self.app / "client").mkdir(parents=True)
        (self.app / "server").mkdir()
        (self.app / "client" / "odin_cli.py").write_text("# dummy\n")
        (self.app / "server" / "odin_agent.py").write_text('__version__ = "9.9.9"\n')
        (self.odin / "bin").mkdir()
        (self.odin / "bin" / "odin").write_text("#!/bin/sh\n")
        (self.odin / "keys").mkdir()
        (self.odin / "keys" / "vps").write_text("KEY\n")
        (self.odin / "servers").mkdir()
        (self.odin / "projects").mkdir()
        (self.odin / "modes").mkdir()
        (self.odin / "client").symlink_to(self.app / "client")
        (self.td / ".local" / "bin").mkdir(parents=True)
        (self.td / ".local" / "bin" / "odin").symlink_to(self.odin / "bin" / "odin")
        self.claude.mkdir()
        (self.claude / "commands" / "odin").mkdir(parents=True)
        (self.claude / "commands" / "odin" / "status.md").write_text("x")
        (self.claude / "settings.json").write_text(json.dumps({
            "model": "opus",
            "hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "mine"}]},
                {"matcher": "mcp__odin__.*", "hooks": [{"type": "command", "command": "guard"}]}]},
            "permissions": {"allow": ["Bash(ls)", "mcp__odin__server_info"]},
        }))
        (self.td / "claude.json").write_text(json.dumps({
            "mcpServers": {"odin": {"command": "python3", "args": ["x"]}, "other": {}}}))

    def patches(self):
        return [
            patch.object(odin_cli, "ODIN_DIR", self.odin),
            patch.object(odin_cli, "ODIN_INSTALL_DIR", self.app),
            patch.object(odin_cli, "SERVERS_DIR", self.odin / "servers"),
            patch.object(odin_cli, "PROJECTS_DIR", self.odin / "projects"),
            patch.object(odin_cli, "KEYS_DIR", self.odin / "keys"),
            patch.object(odin_cli, "MODES_DIR", self.odin / "modes"),
            patch.object(odin_cli, "ODIN_BIN_WRAPPER", self.odin / "bin" / "odin"),
            patch.object(odin_cli, "LOCAL_BIN_LINK", self.td / ".local" / "bin" / "odin"),
            patch.object(odin_cli, "USER_CLAUDE_DIR", self.claude),
            patch.object(odin_cli, "USER_CLAUDE_JSON", self.td / "claude.json"),
            patch.object(odin_cli, "SLASH_CMD_DST", self.claude / "commands" / "odin"),
            patch.object(odin_cli.shutil, "which", return_value=None),
        ]

    def cleanup(self):
        shutil.rmtree(self.td, ignore_errors=True)


class TestUninstallCmd(unittest.TestCase):

    def setUp(self):
        self.L = _TempLayout()
        self._ps = self.L.patches()
        for p in self._ps:
            p.start()

    def tearDown(self):
        for p in self._ps:
            p.stop()
        self.L.cleanup()

    def test_uninstall_keeps_state_by_default(self):
        odin_cli.cmd_uninstall(purge=False, yes=True)
        self.assertFalse(self.L.app.exists(), "kode harus dihapus")
        self.assertFalse((self.L.odin / "bin").exists())
        self.assertFalse((self.L.td / ".local" / "bin" / "odin").exists())
        self.assertFalse((self.L.odin / "client").exists(), "symlink kompat dihapus")
        self.assertTrue((self.L.odin / "keys" / "vps").is_file(), "state HARUS selamat")
        self.assertFalse((self.L.claude / "commands" / "odin").exists())
        # Claude Code: hanya jejak odin yang dicabut
        s = json.loads((self.L.claude / "settings.json").read_text())
        self.assertEqual(s["model"], "opus")
        self.assertEqual([h["matcher"] for h in s["hooks"]["PreToolUse"]], ["Bash"])
        self.assertEqual(s["permissions"]["allow"], ["Bash(ls)"])
        cj = json.loads((self.L.td / "claude.json").read_text())
        self.assertNotIn("odin", cj["mcpServers"])
        self.assertIn("other", cj["mcpServers"])

    def test_uninstall_purge_removes_state(self):
        odin_cli.cmd_uninstall(purge=True, yes=True)
        self.assertFalse(self.L.odin.exists())

    def test_purge_settings_file_is_surgical(self):
        sp = self.L.claude / "settings.json"
        self.assertTrue(odin_cli._purge_odin_settings_file(sp))
        self.assertFalse(odin_cli._purge_odin_settings_file(sp), "kedua kali: tak ada perubahan")
        s = json.loads(sp.read_text())
        self.assertIn("Bash", [h["matcher"] for h in s["hooks"]["PreToolUse"]])


class TestDoctorLocal(unittest.TestCase):

    def test_checks_structure_and_detects_missing_wrapper(self):
        L = _TempLayout()
        (L.odin / "bin" / "odin").unlink()
        try:
            with patch.object(odin_cli, "ODIN_DIR", L.odin), \
                 patch.object(odin_cli, "ODIN_INSTALL_DIR", L.app), \
                 patch.object(odin_cli, "SERVERS_DIR", L.odin / "servers"), \
                 patch.object(odin_cli, "PROJECTS_DIR", L.odin / "projects"), \
                 patch.object(odin_cli, "ODIN_BIN_WRAPPER", L.odin / "bin" / "odin"), \
                 patch.object(odin_cli, "LOCAL_BIN_LINK", L.td / ".local" / "bin" / "odin"), \
                 patch.object(odin_cli, "USER_CLAUDE_DIR", L.claude), \
                 patch.object(odin_cli, "USER_CLAUDE_JSON", L.td / "claude.json"), \
                 patch.object(odin_cli, "SLASH_CMD_DST", L.claude / "commands" / "odin"):
                checks = odin_cli._local_checks()
        finally:
            L.cleanup()
        labels = {c[0]: c for c in checks}
        for label, good, detail in checks:
            self.assertIsInstance(label, str)
            self.assertIsInstance(good, bool)
            self.assertIsInstance(detail, str)
        self.assertFalse(labels["Wrapper odin"][1])
        self.assertTrue(labels["MCP odin global (~/.claude.json)"][1])
        self.assertTrue(labels["Guard hook global"][1])
        self.assertTrue(labels["Slash command /odin:*"][1])
        self.assertFalse(labels["Server terdaftar"][1])


@unittest.skipUnless(shutil.which("git"), "butuh git")
class TestSelfUpdate(unittest.TestCase):
    """Repo upstream palsu dengan dua tag; app di-clone ter-pin ke tag lama."""

    def _git(self, cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="odin-su-"))
        self.up = self.td / "upstream"
        (self.up / "server").mkdir(parents=True)
        (self.up / ".claude" / "commands" / "odin").mkdir(parents=True)
        self._git(self.td, "init", "-q", "-b", "main", str(self.up))
        self._write("1.0.0", "a")
        self._git(self.up, "add", "-A"); self._git(self.up, "commit", "-qm", "v1")
        self._git(self.up, "tag", "v1.0.0")
        self._write("2.0.0", "b")
        self._git(self.up, "add", "-A"); self._git(self.up, "commit", "-qm", "v2")
        self._git(self.up, "tag", "v2.0.0")
        self.app = self.td / "app"
        subprocess.run(["git", "clone", "-q", str(self.up), str(self.app)], check=True)
        self._git(self.app, "checkout", "-q", "--detach", "v1.0.0")
        self.slash = self.td / "slash"

    def _write(self, ver, cmd_text):
        (self.up / "server" / "odin_agent.py").write_text(f'__version__ = "{ver}"\n')
        (self.up / ".claude" / "commands" / "odin" / "status.md").write_text(cmd_text)
        (self.up / "requirements-cli.txt").write_text("paramiko\n")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_pinned_to_tag_moves_to_latest_tag(self):
        with patch.object(odin_cli, "ODIN_INSTALL_DIR", self.app), \
             patch.object(odin_cli, "SLASH_CMD_DST", self.slash):
            self.assertEqual(odin_cli.get_odin_version(), "1.0.0")
            odin_cli.cmd_self_update()
            self.assertEqual(odin_cli.get_odin_version(), "2.0.0")
            self.assertEqual(odin_cli._app_ref_kind(), ("tag", "v2.0.0"))
            self.assertEqual((self.slash / "status.md").read_text(), "b")

    def test_explicit_version(self):
        with patch.object(odin_cli, "ODIN_INSTALL_DIR", self.app), \
             patch.object(odin_cli, "SLASH_CMD_DST", self.slash):
            odin_cli.cmd_self_update("v2.0.0")
            self.assertEqual(odin_cli.get_odin_version(), "2.0.0")
            odin_cli.cmd_self_update("v1.0.0")          # rollback juga bisa
            self.assertEqual(odin_cli.get_odin_version(), "1.0.0")

    def test_following_branch_updates_branch(self):
        self._git(self.app, "checkout", "-q", "-B", "main", "origin/main")
        self._git(self.app, "reset", "-q", "--hard", "v1.0.0")
        with patch.object(odin_cli, "ODIN_INSTALL_DIR", self.app), \
             patch.object(odin_cli, "SLASH_CMD_DST", self.slash):
            odin_cli.cmd_self_update()
            self.assertEqual(odin_cli.get_odin_version(), "2.0.0")
            self.assertEqual(odin_cli._app_ref_kind(), ("branch", "main"))

    def test_not_a_git_checkout_points_to_installer(self):
        plain = self.td / "plain"
        plain.mkdir()
        with patch.object(odin_cli, "ODIN_INSTALL_DIR", plain):
            odin_cli.cmd_self_update()          # tidak boleh melempar exception
        self.assertFalse((plain / ".git").exists())


class TestVersionCmd(unittest.TestCase):

    def test_version_prints_locations(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            odin_cli.cmd_version()
        out = buf.getvalue()
        self.assertIn("ODIN v", out)
        self.assertIn("kode", out)
        self.assertIn("state", out)


if __name__ == "__main__":
    unittest.main()
