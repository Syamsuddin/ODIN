"""Test Core — _truncate, _path_inside, _build_invocation, _run, _DANGER_RE."""
import subprocess
import sys, types, os, time, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

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
import importlib.util
spec = importlib.util.spec_from_file_location("odin_agent", ROOT / "server" / "odin_agent.py")
da = importlib.util.module_from_spec(spec)
spec.loader.exec_module(da)


# ===========================================================================
# _truncate
# ===========================================================================
class TestTruncate(unittest.TestCase):

    def test_short_unchanged(self):
        self.assertEqual(da._truncate("hello"), "hello")

    def test_empty(self):
        self.assertEqual(da._truncate(""), "")

    def test_none(self):
        self.assertEqual(da._truncate(None), "")

    def test_exact_limit(self):
        text = "x" * da.OUTPUT_LIMIT
        self.assertEqual(da._truncate(text), text)

    def test_over_limit_truncated(self):
        text = "x" * (da.OUTPUT_LIMIT + 100)
        result = da._truncate(text)
        self.assertLess(len(result), len(text))
        self.assertIn("dipotong", result)

    def test_keeps_head_and_tail(self):
        head = "HEAD_" + "a" * 5000
        tail = "b" * 5000 + "_TAIL"
        middle = "m" * (da.OUTPUT_LIMIT + 100)
        text = head + middle + tail
        result = da._truncate(text)
        self.assertTrue(result.startswith("HEAD_"))
        self.assertTrue(result.endswith("_TAIL"))


# ===========================================================================
# _path_inside
# ===========================================================================
class TestPathInside(unittest.TestCase):
    """Test _path_inside. Gunakan MODE=ssh agar realpath tidak resolve symlink macOS."""

    def test_inside(self):
        with patch.object(da, "MODE", "ssh"):
            self.assertTrue(da._path_inside("/var/www/app/storage", ["/var/www"]))

    def test_exact_match(self):
        with patch.object(da, "MODE", "ssh"):
            self.assertTrue(da._path_inside("/var/www", ["/var/www"]))

    def test_outside(self):
        with patch.object(da, "MODE", "ssh"):
            self.assertFalse(da._path_inside("/etc/nginx", ["/var/www"]))

    def test_traversal_blocked(self):
        with patch.object(da, "MODE", "ssh"):
            self.assertFalse(da._path_inside("/var/www/../etc", ["/var/www"]))

    def test_relative_blocked(self):
        with patch.object(da, "MODE", "ssh"):
            self.assertFalse(da._path_inside("relative/path", ["/var/www"]))

    def test_multiple_allowed(self):
        with patch.object(da, "MODE", "ssh"):
            self.assertTrue(da._path_inside("/var/log/nginx/access.log", ["/var/www", "/var/log"]))

    def test_prefix_not_confused(self):
        with patch.object(da, "MODE", "ssh"):
            self.assertFalse(da._path_inside("/var/www-evil", ["/var/www"]))


# ===========================================================================
# _build_invocation
# ===========================================================================
class TestBuildInvocation(unittest.TestCase):

    def test_local_without_cwd(self):
        with patch.object(da, "MODE", "local"):
            argv = da._build_invocation("ls -la", None)
            self.assertEqual(argv[0], "bash")
            self.assertIn("ls -la", argv[-1])

    def test_local_with_cwd(self):
        with patch.object(da, "MODE", "local"):
            argv = da._build_invocation("ls", "/var/www")
            self.assertIn("cd", argv[-1])
            self.assertIn("/var/www", argv[-1])

    def test_ssh_mode(self):
        with patch.object(da, "MODE", "ssh"), \
             patch.object(da, "SSH_TARGET", "odin@vps"):
            argv = da._build_invocation("ls -la", None)
            self.assertIn("ssh", argv)
            self.assertIn("odin@vps", argv)

    def test_ssh_with_key(self):
        with patch.object(da, "MODE", "ssh"), \
             patch.object(da, "SSH_TARGET", "odin@vps"), \
             patch.object(da, "SSH_KEY", "/path/to/key"):
            argv = da._build_invocation("ls", None)
            self.assertIn("-i", argv)
            self.assertIn("/path/to/key", argv)

    def test_ssh_without_target_raises(self):
        with patch.object(da, "MODE", "ssh"), \
             patch.object(da, "SSH_TARGET", ""):
            with self.assertRaises(RuntimeError):
                da._build_invocation("ls", None)


# ===========================================================================
# _resolve_default_cwd — isolasi konteks P0 (ODIN tak chdir ke app dir;
# default cwd ditambahkan di level perintah)
# ===========================================================================
class TestResolveDefaultCwd(unittest.TestCase):

    def test_explicit_cwd_honored(self):
        # cwd eksplisit selalu dipakai apa adanya, tanpa cek isdir
        with patch.object(da, "PROJECT_ROOT", "/var/www/app"), \
             patch.object(da, "MODE", "local"):
            self.assertEqual(da._resolve_default_cwd("/tmp/x"), "/tmp/x")

    def test_empty_defaults_to_project_root_when_exists(self):
        with patch.object(da, "PROJECT_ROOT", "/var/www/app"), \
             patch.object(da, "MODE", "local"), \
             patch.object(da.os.path, "isdir", lambda p: p == "/var/www/app"):
            self.assertEqual(da._resolve_default_cwd(""), "/var/www/app")

    def test_empty_returns_none_when_root_missing(self):
        # mode SETUP: /var/www/app belum dibuat → None (jalan di home ODIN, bukan crash)
        with patch.object(da, "PROJECT_ROOT", "/var/www/app"), \
             patch.object(da, "MODE", "local"), \
             patch.object(da.os.path, "isdir", lambda p: False):
            self.assertIsNone(da._resolve_default_cwd(""))

    def test_empty_returns_none_without_project_root(self):
        with patch.object(da, "PROJECT_ROOT", ""), \
             patch.object(da, "MODE", "local"):
            self.assertIsNone(da._resolve_default_cwd(""))

    def test_ssh_mode_uses_root_without_local_isdir(self):
        # mode ssh: PROJECT_ROOT remote → jangan cek isdir lokal
        with patch.object(da, "PROJECT_ROOT", "/var/www/app"), \
             patch.object(da, "MODE", "ssh"), \
             patch.object(da.os.path, "isdir", lambda p: False):
            self.assertEqual(da._resolve_default_cwd(""), "/var/www/app")


# ===========================================================================
# _DANGER_RE — hard-block pada server
# ===========================================================================
class TestDangerRE(unittest.TestCase):

    def test_rm_rf_root(self):
        self.assertTrue(da._DANGER_RE.search("rm -rf /"))
        self.assertTrue(da._DANGER_RE.search("rm -rf /*"))

    def test_rm_rf_home(self):
        self.assertTrue(da._DANGER_RE.search("rm -rf ~"))

    def test_mkfs(self):
        self.assertTrue(da._DANGER_RE.search("mkfs.ext4 /dev/sda"))

    def test_dd_of_dev(self):
        self.assertTrue(da._DANGER_RE.search("dd if=/dev/zero of=/dev/sda"))

    def test_redirect_dev(self):
        self.assertTrue(da._DANGER_RE.search("> /dev/sda"))

    def test_fork_bomb(self):
        self.assertTrue(da._DANGER_RE.search(":(){ :|:& };:"))

    def test_shutdown(self):
        self.assertTrue(da._DANGER_RE.search("shutdown -h now"))

    def test_reboot(self):
        self.assertTrue(da._DANGER_RE.search("reboot"))

    def test_halt(self):
        self.assertTrue(da._DANGER_RE.search("halt"))

    def test_init_0(self):
        self.assertTrue(da._DANGER_RE.search("init 0"))

    def test_chmod_777_root(self):
        self.assertTrue(da._DANGER_RE.search("chmod -R 777 /"))

    def test_chown_R_root(self):
        self.assertTrue(da._DANGER_RE.search("chown -R root:root /"))

    def test_drop_database(self):
        self.assertTrue(da._DANGER_RE.search("drop database mydb"))

    def test_mysqladmin_drop(self):
        self.assertTrue(da._DANGER_RE.search("mysqladmin drop mydb"))

    def test_safe_not_matched(self):
        self.assertIsNone(da._DANGER_RE.search("ls -la"))
        self.assertIsNone(da._DANGER_RE.search("rm -rf /var/www/app"))
        self.assertIsNone(da._DANGER_RE.search("cat /etc/hosts"))


# ===========================================================================
# _run — unit test (mocked subprocess)
# ===========================================================================
class TestRun(unittest.TestCase):
    """_run dieksekusi SUNGGUHAN (bash lokal) — sejak v2.3 ia memakai Popen +
    process group, jadi mock subprocess.run tak lagi merepresentasikan apa pun."""

    def test_success(self):
        r = da._run("echo hello", None, 10)
        self.assertTrue(r["success"])
        self.assertEqual(r["exit_code"], 0)
        self.assertIn("hello", r["stdout"])

    def test_failure(self):
        r = da._run("exit 3", None, 10)
        self.assertFalse(r["success"])
        self.assertEqual(r["exit_code"], 3)

    def test_stderr_captured(self):
        r = da._run("echo oops >&2; exit 1", None, 10)
        self.assertIn("oops", r["stderr"])

    def test_exception(self):
        with patch("subprocess.Popen", side_effect=Exception("boom")):
            r = da._run("bad_cmd", None, 10)
        self.assertFalse(r["success"])
        self.assertIn("boom", r["stderr"])

    def test_danger_blocked(self):
        r = da._run("rm -rf /", None, 10)
        self.assertFalse(r["success"])
        self.assertTrue(r.get("blocked"))
        self.assertIn("DITOLAK", r["stderr"])

    def test_danger_flag_variants_blocked(self):
        """Regresi T7: varian flag tak boleh lolos hard-block."""
        for cmd in ("rm -fr /", "rm --recursive --force /", "find / -delete",
                    "systemctl poweroff", "mysql -e 'DROP SCHEMA prod'"):
            with self.subTest(cmd=cmd):
                self.assertTrue(da._run(cmd, None, 5).get("blocked"), cmd)

    def test_danger_allowed(self):
        r = da._run("true # rm -rf /", None, 10, allow_dangerous=True)
        self.assertTrue(r["success"])

    def test_timeout_clamped(self):
        r = da._run("echo hi", None, 99999)
        self.assertTrue(r["success"])          # tak error meski timeout > MAX_TIMEOUT

    def test_timeout_expired(self):
        r = da._run("sleep 30", None, 1)
        self.assertFalse(r["success"])
        self.assertTrue(r.get("timeout"))
        self.assertIn("TIMEOUT", r["stderr"])

    def test_timeout_kills_orphan_children(self):
        """Regresi T4: timeout dulu hanya membunuh wrapper bash — `composer install`
        / `npm ci` terus jalan sebagai orphan."""
        marker = "odin-orphan-test-4711"
        r = da._run(f"sleep 90 & echo {marker}; wait", None, 1)
        self.assertTrue(r.get("timeout"))
        time.sleep(0.5)
        found = subprocess.run(["pgrep", "-f", "sleep 90"],
                               capture_output=True, text=True).stdout.strip()
        self.assertEqual(found, "", "child process masih hidup setelah timeout")

    def test_non_utf8_output_not_reported_as_error(self):
        """Regresi T4: byte non-UTF-8 (mysqldump, git log latin1) dulu melempar
        UnicodeDecodeError dan dilaporkan ERROR dgn stdout kosong."""
        r = da._run(r"printf '\xff\xfe latin1 \xc3'", None, 10)
        self.assertTrue(r["success"])
        self.assertIn("latin1", r["stdout"])

    def test_output_is_bounded(self):
        """Output raksasa tidak boleh ditampung utuh di RAM."""
        r = da._run("head -c 3000000 /dev/zero | tr '\\0' 'x'", None, 30)
        self.assertTrue(r["success"])
        self.assertLess(len(r["stdout"]), da.OUTPUT_LIMIT + 200)
        self.assertIn("dipotong", r["stdout"])

    def test_lock_cwd_enforcement(self):
        orig_lock = da.LOCK_CWD_TO_PROJECT
        orig_root = da.PROJECT_ROOT
        try:
            da.LOCK_CWD_TO_PROJECT = True
            da.PROJECT_ROOT = "/var/www/app"
            r = da._run("ls", "/etc/nginx", 10)
            self.assertFalse(r["success"])
            self.assertIn("di luar PROJECT_ROOT", r["stderr"])
        finally:
            da.LOCK_CWD_TO_PROJECT = orig_lock
            da.PROJECT_ROOT = orig_root

    def test_duration_recorded(self):
        r = da._run("echo hi", None, 10)
        self.assertIn("duration_sec", r)
        self.assertIsInstance(r["duration_sec"], float)

    def test_command_in_result(self):
        r = da._run("whoami", None, 10)
        self.assertEqual(r["command"], "whoami")


class TestCacheInvalidation(unittest.TestCase):
    """Regresi: hasil READ yang di-cache harus dibuang saat ada perintah WRITE."""

    def setUp(self):
        da._result_cache.clear()

    def tearDown(self):
        da._result_cache.clear()

    def test_write_command_clears_cache(self):
        da._cache_set("cat /tmp/x", "", {"success": True, "stdout": "lama"})
        self.assertIsNotNone(da._cache_get("cat /tmp/x", ""))
        da._cache_invalidate("sed -i s/a/b/ /tmp/x")
        self.assertIsNone(da._cache_get("cat /tmp/x", ""))

    def test_read_command_keeps_cache(self):
        da._cache_set("cat /tmp/x", "", {"success": True, "stdout": "lama"})
        da._cache_invalidate("cat /tmp/x")
        self.assertIsNotNone(da._cache_get("cat /tmp/x", ""))


# ===========================================================================
# DANGER sync: guard vs server
# ===========================================================================
class TestDangerSync(unittest.TestCase):
    """Verifikasi bahwa pola katastrofik inti ada di KEDUA sisi."""

    def _load_guard(self):
        guard_spec = importlib.util.spec_from_file_location(
            "guard_sync", ROOT / "client" / "odin_guard.py")
        guard = importlib.util.module_from_spec(guard_spec)
        guard_spec.loader.exec_module(guard)
        return guard

    def test_shared_catastrophic_patterns(self):
        guard = self._load_guard()
        cases = [
            "rm -rf /",
            "rm -rf ~",
            "mkfs.ext4 /dev/sda",
            "dd if=/dev/zero of=/dev/sda",
            ":(){ :|:& };:",
            "shutdown -h now",
            "reboot",
            "halt",
            "chmod -R 777 /",
            "drop database mydb",
            "mysqladmin drop mydb",
        ]
        for cmd in cases:
            self.assertTrue(da._DANGER_RE.search(cmd),
                            f"server should block: {cmd}")
            self.assertTrue(guard.DANGER.search(cmd),
                            f"guard should catch: {cmd}")


# ===========================================================================
# PROJECT_NAME — project awareness
# ===========================================================================
class TestProjectName(unittest.TestCase):
    def test_project_name_default_empty(self):
        self.assertEqual(da.PROJECT_NAME, "")

    def test_server_info_has_project_name(self):
        with patch.object(da, "_run", return_value=(0, "ok", "")):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                da.server_info.__wrapped__()
            ) if hasattr(da.server_info, '__wrapped__') else None
        if result is None:
            self.skipTest("server_info wrapper not accessible")
        self.assertIn("project_name", result)

    def test_audit_record_has_project(self):
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as td:
            audit_file = os.path.join(td, "audit.jsonl")
            with patch.object(da, "AUDIT_ENABLED", True), \
                 patch.object(da, "AUDIT_FILE", audit_file), \
                 patch.object(da, "MEMORY_DIR", td), \
                 patch.object(da, "PROJECT_NAME", "simuru"):
                da._audit("run_command", "ls", {"success": True, "exit_code": 0, "duration_sec": 0.1})
            with open(audit_file) as f:
                record = _json.loads(f.readline())
            self.assertEqual(record["project"], "simuru")

    def test_audit_project_none_when_empty(self):
        import tempfile, json as _json
        with tempfile.TemporaryDirectory() as td:
            audit_file = os.path.join(td, "audit.jsonl")
            with patch.object(da, "AUDIT_ENABLED", True), \
                 patch.object(da, "AUDIT_FILE", audit_file), \
                 patch.object(da, "MEMORY_DIR", td), \
                 patch.object(da, "PROJECT_NAME", ""):
                da._audit("run_command", "ls", {"success": True, "exit_code": 0, "duration_sec": 0.1})
            with open(audit_file) as f:
                record = _json.loads(f.readline())
            self.assertIsNone(record["project"])


if __name__ == "__main__":
    unittest.main()
