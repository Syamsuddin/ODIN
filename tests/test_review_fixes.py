"""Regresi untuk temuan review ODIN v2.2 (K1-K5, T1-T7).

Setiap test di sini mengunci satu perbaikan supaya tidak diam-diam kembali.
Nama test menyebut kode temuannya agar mudah dilacak ke dokumen review.
"""
import importlib.util
import json
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["ODIN_SKIP_INSPECT"] = "1"

_mcp_mod = types.ModuleType("mcp")
_mcp_srv = types.ModuleType("mcp.server")
_mcp_fmcp = types.ModuleType("mcp.server.fastmcp")


class FakeMCP:
    def __init__(self, *a, **kw): pass
    def tool(self): return lambda f: f
    def resource(self, uri): return lambda f: f
    def run(self, **kw): pass


_mcp_fmcp.FastMCP = FakeMCP
sys.modules["mcp"] = _mcp_mod
sys.modules["mcp.server"] = _mcp_srv
sys.modules["mcp.server.fastmcp"] = _mcp_fmcp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load("guard_fixes", ROOT / "client" / "odin_guard.py")
import odin_cli  # noqa: E402
sys.path.insert(0, str(ROOT / "server"))
import odin_agent as da  # noqa: E402


def _sudoers_rules() -> str:
    """Hanya baris ATURAN (buang komentar) — komentar boleh menyebut pola lama."""
    return "\n".join(l for l in odin_cli.SUDOERS_ODIN.splitlines()
                     if l.strip() and not l.lstrip().startswith("#"))


# ===========================================================================
# K1 & K2 — sudoers tanpa wildcard path / argumen bebas
# ===========================================================================
class TestSudoers(unittest.TestCase):

    def test_k1_no_tail_wildcard_rule(self):
        """`tail -n * /var/log/*` = baca file apa pun sbg root (fnmatch tanpa
        FNM_PATHNAME → `*` cocok dengan `/` dan `..`)."""
        self.assertNotIn("/usr/bin/tail", _sudoers_rules())

    def test_k1_journalctl_forces_no_pager(self):
        """Pager sbg root = shell root lewat `!/bin/sh`."""
        for line in odin_cli.SUDOERS_ODIN.splitlines():
            if line.startswith("odin ") and "journalctl" in line:
                self.assertIn("--no-pager", line)
                break
        else:
            self.fail("aturan journalctl tidak ditemukan")

    def test_k2_certbot_has_no_free_arguments(self):
        """`certbot renew *` memungkinkan --deploy-hook='...' berjalan sbg root."""
        self.assertNotIn("certbot renew *", _sudoers_rules())

    def test_no_wildcard_on_any_path_argument(self):
        """Tak boleh ada `*` yang menempel pada argumen berupa path."""
        for line in odin_cli.SUDOERS_ODIN.splitlines():
            line = line.strip()
            if not line.startswith("odin ") or "NOPASSWD:" not in line:
                continue
            args = line.split("NOPASSWD:", 1)[1]
            for token in args.replace(",", " ").split():
                if token.startswith("/") and "*" in token:
                    self.assertNotIn("/", token[token.index("*"):],
                                     f"wildcard path berbahaya: {token}")

    def test_deploy_script_wildcard_removed(self):
        self.assertNotIn("/usr/local/bin/*-deploy", _sudoers_rules())


# ===========================================================================
# K3 — kunci SSH ODIN tak boleh memberi shell
# ===========================================================================
class TestForcedCommandKey(unittest.TestCase):

    def test_authorized_keys_line_is_restricted(self):
        line = odin_cli._authorized_keys_line("ssh-ed25519 AAAAC3Nz odin-vps")
        self.assertTrue(line.startswith('restrict,command="'))
        self.assertIn("odin-dispatch.sh", line)
        self.assertIn("ssh-ed25519 AAAAC3Nz", line)

    def test_dispatcher_exists_and_is_executable(self):
        d = ROOT / "server" / "odin-dispatch.sh"
        self.assertTrue(d.is_file())
        self.assertTrue(os.access(d, os.X_OK))

    def _dispatch(self, cmd, tmp):
        env = dict(os.environ, SSH_ORIGINAL_COMMAND=cmd)
        return subprocess.run([str(tmp / "odin-dispatch.sh")], env=env,
                              capture_output=True, text=True, cwd=str(tmp))

    def test_dispatcher_accepts_only_runsh(self):
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            shutil.copy(ROOT / "server" / "odin-dispatch.sh", tmp)
            (tmp / "run.sh").write_text('#!/usr/bin/env bash\necho "ARGS:$*"\n')
            (tmp / "run.sh").chmod(0o755)

            for cmd in ("", "/home/odin/run.sh", "/home/odin/run.sh --project simuru"):
                with self.subTest(allow=cmd):
                    r = self._dispatch(cmd, tmp)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertIn("ARGS:", r.stdout)

            for cmd in ("/bin/bash", "bash -i", "/home/odin/run.sh --project a; bash",
                        "/home/odin/run.sh --project ../../etc",
                        "/home/odin/run.sh --project x --project y",
                        "cat /etc/shadow"):
                with self.subTest(deny=cmd):
                    r = self._dispatch(cmd, tmp)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("ditolak", r.stderr)


# ===========================================================================
# K4 — classifier: perintah pembungkus & substitusi proses
# ===========================================================================
class TestWrapperClassifier(unittest.TestCase):

    def test_env_wrapper_is_not_read(self):
        self.assertEqual(guard.classify_command("env rm -rf /var/www/app"), "ask")

    def test_wrappers_defer_to_real_command(self):
        for cmd in ("nice -n 10 rm -rf /var/www/app",
                    "nohup composer install",
                    "timeout 30 systemctl restart nginx",
                    "xargs rm -f",
                    "stdbuf -oL sed -i s/a/b/ /etc/hosts",
                    "setsid npm ci"):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.classify_command(cmd), "ask")

    def test_wrappers_still_allow_real_reads(self):
        for cmd in ("env", "env FOO=bar cat /etc/hosts", "timeout 30 ls -la",
                    "nice -n 10 grep -r foo /var/log", "command -v php"):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.classify_command(cmd), "allow")

    def test_sed_long_in_place_is_write(self):
        self.assertEqual(guard.classify_command("sed --in-place=.bak s/x/y/ /etc/hosts"),
                         "ask")

    def test_process_substitution_forces_ask(self):
        self.assertEqual(guard.classify_command("cat <(rm -rf /tmp/x)"), "ask")
        self.assertEqual(guard.classify_command("diff <(ls) >(tee /tmp/x)"), "ask")

    def test_runbook_of_wrapped_writes_is_not_auto_allowed(self):
        """Runbook auto-allow bila SEMUA langkah read — `env rm -rf` dulu lolos."""
        steps = [{"label": "a", "command": "ls -la"},
                 {"label": "b", "command": "env rm -rf /var/www/app"}]
        self.assertTrue(any(guard.classify_command(s["command"]) != "allow"
                            for s in steps))


# ===========================================================================
# K5 — hook per-project di-merge, bukan ditimpa
# ===========================================================================
class TestLocalConfigHooks(unittest.TestCase):

    def test_existing_user_hooks_survive(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            (wd / ".claude").mkdir()
            (wd / ".claude" / "settings.json").write_text(json.dumps({
                "hooks": {"PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "mine"}]}]},
            }))
            odin_cli._write_local_mcp_config(str(wd), "srv", "proj")
            s = json.loads((wd / ".claude" / "settings.json").read_text())
            matchers = [h["matcher"] for h in s["hooks"]["PreToolUse"]]
            self.assertIn("Bash", matchers)
            self.assertIn(odin_cli.GUARD_MATCHER, matchers)

    def test_idempotent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            odin_cli._write_local_mcp_config(str(wd), "srv", "proj")
            odin_cli._write_local_mcp_config(str(wd), "srv", "proj")
            s = json.loads((wd / ".claude" / "settings.json").read_text())
            self.assertEqual(len(s["hooks"]["PreToolUse"]), 1)

    def test_matcher_covers_all_odin_tools(self):
        """Matcher sempit membuat tool BARU lolos tanpa penilaian guard."""
        import re as _re
        for tool in ("mcp__odin__run_command", "mcp__odin__cortex_log",
                     "mcp__odin__memory_health", "mcp__odin__tool_masa_depan"):
            self.assertTrue(_re.match(odin_cli.GUARD_MATCHER, tool), tool)


# ===========================================================================
# Guard: tool read-only auto-allow + resolusi project dari registry
# ===========================================================================
class TestGuardReadOnlyTools(unittest.TestCase):

    def test_read_only_tools_listed_both_sides(self):
        for t in guard.READ_ONLY_TOOLS:
            self.assertIn(f"mcp__odin__{t}", odin_cli.READ_ONLY_TOOLS, t)


class TestProjectDetectionFromRegistry(unittest.TestCase):
    """Config global (v2.2) tak menulis entry odin ke .claude/settings.json —
    identitas project harus tetap terbaca dari ~/.odin/projects."""

    def test_detect_from_registry_by_workdir_prefix(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            wd = Path(td) / "PROJ"
            (home / ".odin" / "projects").mkdir(parents=True)
            wd.mkdir()
            (home / ".odin" / "projects" / "simuru.yaml").write_text(
                f"name: simuru\nserver: vps-app\nlocal_workdir: {wd}\n")
            with patch.object(os.path, "expanduser",
                              side_effect=lambda p: p.replace("~", str(home))):
                got = guard._detect_from_registry(str(wd / "sub"))
        self.assertEqual(got, ("simuru", "vps-app"))

    def test_unregistered_dir_returns_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            (home / ".odin" / "projects").mkdir(parents=True)
            with patch.object(os.path, "expanduser",
                              side_effect=lambda p: p.replace("~", str(home))):
                self.assertEqual(guard._detect_from_registry(td), ("", ""))


# ===========================================================================
# T1 — singleton hanya boleh membunuh proses ODIN
# ===========================================================================
class TestSingletonPidVerification(unittest.TestCase):

    def test_own_process_recognised(self):
        with patch("subprocess.run") as m:
            m.return_value = types.SimpleNamespace(stdout="python3 /home/odin/odin_agent.py")
            self.assertTrue(da._is_odin_process(os.getpid()))

    def test_foreign_process_rejected(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertFalse(da._is_odin_process(999999))

    def test_non_odin_cmdline_rejected(self):
        with patch("builtins.open", side_effect=OSError), \
             patch("subprocess.run") as m:
            m.return_value = types.SimpleNamespace(stdout="/usr/bin/postgres -D /var/lib")
            self.assertFalse(da._is_odin_process(4242))


# ===========================================================================
# T2 — mode production hanya dari sinyal eksplisit
# ===========================================================================
class TestProductionSignal(unittest.TestCase):

    def test_uptime_is_not_a_signal(self):
        self.assertFalse(da._is_production_signal(
            {"app": {"app_env": "local"}, "base": {"uptime_days": 900}}))

    def test_app_env_is_a_signal(self):
        self.assertTrue(da._is_production_signal({"app": {"app_env": "production"}}))

    def test_apt_with_flag_before_subcommand_is_blocked(self):
        """`apt -y install` dulu lolos dari gerbang mode production."""
        orig = da._CURRENT_MODE
        try:
            da._CURRENT_MODE = "production"
            for cmd in ("apt -y install nginx", "apt-get -qq install curl",
                        "npm --silent install", "pip3 -q install requests"):
                with self.subTest(cmd=cmd):
                    self.assertIsNotNone(da._mode_gate("run_command", cmd), cmd)
        finally:
            da._CURRENT_MODE = orig


# ===========================================================================
# T4 & memory: buffer terbatas, newline repair
# ===========================================================================
class TestBoundedBuffer(unittest.TestCase):

    def test_keeps_head_and_tail(self):
        b = da._BoundedBuffer(1024)
        b.feed(b"A" * 5000 + b"Z" * 5000)
        text = b.text()
        self.assertTrue(text.startswith("A"))
        self.assertTrue(text.rstrip().endswith("Z"))
        self.assertIn("dipotong", text)
        self.assertLess(len(text), 3000)

    def test_invalid_utf8_replaced_not_raised(self):
        b = da._BoundedBuffer(1024)
        b.feed(b"\xff\xfe ok")
        self.assertIn("ok", b.text())


# ===========================================================================
# Perbaikan harus SAMPAI ke server yang sudah terpasang
# ===========================================================================
class FakeSSH:
    """SSHSession palsu: memetakan substring perintah → (stdout, rc)."""

    def __init__(self, responses):
        self.responses = responses
        self.ran = []

    def run(self, cmd, timeout=60):
        self.ran.append(cmd)
        for needle, (out, rc) in self.responses.items():
            if needle in cmd:
                return out, "", rc
        return "", "", 0

    def close(self):
        pass


class TestRemoteHardeningAudit(unittest.TestCase):
    """`server add` melewati sudoers bila filenya sudah ada dan `odin update`
    jalan sebagai user odin — tanpa audit ini, server lama diam-diam tetap
    memakai aturan rentan selamanya."""

    OLD = ("# ODIN\n"
           "odin ALL=(root) NOPASSWD: /usr/bin/tail -n * /var/log/*\n"
           "odin ALL=(root) NOPASSWD: /usr/bin/journalctl *\n"
           "odin ALL=(root) NOPASSWD: /usr/bin/certbot renew *\n"
           "odin ALL=(root) NOPASSWD: /usr/local/bin/*-deploy\n")

    def test_detects_every_vulnerable_pattern(self):
        ssh = FakeSSH({"cat /etc/sudoers.d/odin": (self.OLD, 0)})
        found = odin_cli._audit_remote_sudoers(ssh)
        self.assertEqual(len(found), 4)

    def test_new_sudoers_passes_own_audit(self):
        """Aturan yang kita pasang sendiri harus lolos audit — kalau tidak,
        `odin server harden` akan melapor gagal selamanya."""
        ssh = FakeSSH({"cat /etc/sudoers.d/odin": (odin_cli.SUDOERS_ODIN, 0)})
        self.assertEqual(odin_cli._audit_remote_sudoers(ssh), [])

    def test_missing_file_is_not_a_finding(self):
        ssh = FakeSSH({"cat /etc/sudoers.d/odin": ("", 1)})
        self.assertEqual(odin_cli._audit_remote_sudoers(ssh), [])

    def test_key_without_forced_command_detected(self):
        ssh = FakeSSH({"authorized_keys": ("ssh-ed25519 AAAAC3 odin-vps\n", 0)})
        self.assertFalse(odin_cli._audit_remote_key(ssh))

    def test_key_with_forced_command_accepted(self):
        line = odin_cli._authorized_keys_line("ssh-ed25519 AAAAC3 odin-vps")
        ssh = FakeSSH({"authorized_keys": (line + "\n", 0)})
        self.assertTrue(odin_cli._audit_remote_key(ssh))

    def test_rewrite_replaces_same_key_and_keeps_others(self):
        other = "ssh-rsa BBBB admin@laptop"
        ssh = FakeSSH({"cat ": (f"{other}\nssh-ed25519 AAAAC3 odin-vps\n", 0)})
        okw, old = odin_cli._write_authorized_keys(ssh, "ssh-ed25519 AAAAC3 odin-vps")
        self.assertTrue(okw)
        written = [c for c in ssh.ran if "tee" in c][0]
        self.assertIn(other, written)                       # kunci lain selamat
        self.assertIn('restrict,command=', written)
        self.assertEqual(written.count("AAAAC3"), 1)        # tak duplikat


# ===========================================================================
# ~/.odin/ssh_config — cek alias EKSAK + opsi anti-gantung
# ===========================================================================
class TestSshConfig(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._td = tempfile.mkdtemp()
        self.home = Path(self._td)
        self._orig_cfg = odin_cli.ODIN_SSH_CONFIG
        odin_cli.ODIN_SSH_CONFIG = self.home / "ssh_config"
        self._orig_home = Path.home
        Path.home = staticmethod(lambda: self.home)

    def tearDown(self):
        import shutil
        odin_cli.ODIN_SSH_CONFIG = self._orig_cfg
        Path.home = self._orig_home
        shutil.rmtree(self._td, ignore_errors=True)

    def test_similar_alias_is_not_treated_as_existing(self):
        """Dulu cek `f"Host {alias}" not in existing` — substring: 'vps' dianggap
        sudah ada karena ada 'vps-app', sehingga entry-nya tak pernah ditulis."""
        odin_cli._write_ssh_config_entry("vps-app", "1.2.3.4", "22", self.home / "k1")
        odin_cli._write_ssh_config_entry("vps", "5.6.7.8", "2222", self.home / "k2")
        blocks = odin_cli._read_ssh_blocks()
        self.assertEqual(sorted(blocks), ["vps", "vps-app"])
        self.assertIn("5.6.7.8", blocks["vps"])

    def test_entry_has_anti_hang_options(self):
        odin_cli._write_ssh_config_entry("vps", "1.2.3.4", "22", self.home / "k")
        text = odin_cli.ODIN_SSH_CONFIG.read_text()
        self.assertIn("IdentitiesOnly yes", text)
        self.assertIn("BatchMode yes", text)

    def test_include_is_first_line_of_ssh_config(self):
        """Opsi pertama yang cocok menang di SSH — Include harus di atas `Host *`."""
        cfg = self.home / ".ssh" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("Host *\n    IdentityFile ~/.ssh/id_rsa\n")
        odin_cli._write_ssh_config_entry("vps", "1.2.3.4", "22", self.home / "k")
        self.assertTrue(cfg.read_text().startswith("Include ~/.odin/ssh_config"))

    def test_remove_is_exact_and_idempotent(self):
        odin_cli._write_ssh_config_entry("vps-app", "1.2.3.4", "22", self.home / "k1")
        odin_cli._write_ssh_config_entry("vps", "5.6.7.8", "22", self.home / "k2")
        self.assertTrue(odin_cli._remove_ssh_config_entry("vps"))
        self.assertIn("vps-app", odin_cli._read_ssh_blocks())
        self.assertFalse(odin_cli._remove_ssh_config_entry("vps"))


# ===========================================================================
# tail_log — pipefail & analisis isi log
# ===========================================================================
class TestTailLog(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._td = tempfile.mkdtemp()
        self.dir = os.path.realpath(self._td)
        self.log = os.path.join(self.dir, "app.log")
        with open(self.log, "w") as f:
            f.write("normal\nSQLSTATE[HY000] [2002] Connection refused\n")
        self._orig = da.ALLOWED_LOG_DIRS
        da.ALLOWED_LOG_DIRS = [self.dir]

    def tearDown(self):
        import shutil
        da.ALLOWED_LOG_DIRS = self._orig
        shutil.rmtree(self._td, ignore_errors=True)

    def test_missing_file_with_grep_is_failure(self):
        """`tail <hilang> | grep x || true` dulu exit 0 → sukses palsu."""
        r = da.tail_log(os.path.join(self.dir, "hilang.log"), 10, "X")
        self.assertFalse(r["success"])

    def test_grep_without_match_is_success(self):
        r = da.tail_log(self.log, 10, "TIDAKADA")
        self.assertTrue(r["success"])
        self.assertEqual(r["stdout"].strip(), "")

    def test_error_pattern_detected_on_successful_read(self):
        """`tail` yang berhasil SELALU exit 0 — analisis harus tetap jalan."""
        r = da.tail_log(self.log, 10)
        self.assertTrue(r["success"])
        self.assertEqual(r["_analysis"]["error_type"], "db_conn")

    def test_success_does_not_inflate_error_counts(self):
        before = dict(da._error_counts)
        da.tail_log(self.log, 10)
        self.assertEqual(da._error_counts, before)


# ===========================================================================
# run.sh — argumen & multi-project
# ===========================================================================
class TestRunSh(unittest.TestCase):

    def _run_sh(self, args, projects):
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            shutil.copy(ROOT / "server" / "run.sh", tmp)
            (tmp / "projects").mkdir()
            for name in projects:
                (tmp / "projects" / f"{name}.conf").write_text(
                    f'PROJECT_NAME={name}\nPROJECT_ROOT=/var/www/{name}\n')
            (tmp / "odin_agent.py").write_text("")
            return subprocess.run(["bash", str(tmp / "run.sh"), *args],
                                  capture_output=True, text=True)

    def test_unknown_argument_rejected(self):
        r = self._run_sh(["--evil"], [])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tak dikenal", r.stderr)

    def test_invalid_project_name_rejected(self):
        r = self._run_sh(["--project", "../etc"], ["a"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tidak valid", r.stderr)

    def test_multiple_projects_without_flag_is_fatal(self):
        """Dulu jatuh senyap ke PROJECT_ROOT=/var/www/html — project & memory SALAH."""
        r = self._run_sh([], ["alpha", "beta"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("wajib pakai --project", r.stderr)


if __name__ == "__main__":
    unittest.main()
