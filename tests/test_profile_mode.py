"""Test Server Profile, Mode Derivation, dan Mode Enforcement."""
import sys, types, os, tempfile, unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

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

guard_spec = importlib.util.spec_from_file_location("guard", ROOT / "client" / "odin_guard.py")
guard = importlib.util.module_from_spec(guard_spec)
guard_spec.loader.exec_module(guard)


# ===========================================================================
# _parse_sections
# ===========================================================================
class TestParseSections(unittest.TestCase):

    def test_basic(self):
        out = "@@OS@@\nUbuntu 24.04\n@@KERNEL@@\n6.8.0\n"
        s = da._parse_sections(out)
        self.assertEqual(s["OS"], "Ubuntu 24.04")
        self.assertEqual(s["KERNEL"], "6.8.0")

    def test_multiline(self):
        out = "@@LOG@@\nline1\nline2\nline3\n@@END@@\ndone"
        s = da._parse_sections(out)
        self.assertIn("line2", s["LOG"])
        self.assertEqual(s["END"], "done")

    def test_empty_section(self):
        out = "@@A@@\n@@B@@\ndata\n"
        s = da._parse_sections(out)
        self.assertEqual(s["A"], "")
        self.assertEqual(s["B"], "data")

    def test_no_markers(self):
        s = da._parse_sections("just plain text")
        self.assertEqual(s, {})


# ===========================================================================
# _detect_type
# ===========================================================================
class TestDetectType(unittest.TestCase):

    @patch.object(da, "_run")
    def test_web_app(self, mock_run):
        mock_run.return_value = {"success": True, "stdout":
            "@@NGINX@@\n/usr/sbin/nginx\nfound\n@@APACHE@@\nnone\n"
            "@@PHP@@\n/usr/bin/php\nfound\n@@NODE@@\nnone\n"
            "@@MYSQL@@\nfound\n@@PGSQL@@\nnone\n@@MONGOD@@\nnone\n"
            "@@DOCKER@@\nnone\n@@CONTAINERS@@\n0\n", "stderr": "", "exit_code": 0}
        stype, found = da._detect_type()
        self.assertEqual(stype, "web-app")

    @patch.object(da, "_run")
    def test_database(self, mock_run):
        mock_run.return_value = {"success": True, "stdout":
            "@@NGINX@@\nnone\n@@APACHE@@\nnone\n@@PHP@@\nnone\n@@NODE@@\nnone\n"
            "@@MYSQL@@\nfound\n@@PGSQL@@\nnone\n@@MONGOD@@\nnone\n"
            "@@DOCKER@@\nnone\n@@CONTAINERS@@\n0\n", "stderr": "", "exit_code": 0}
        stype, _ = da._detect_type()
        self.assertEqual(stype, "database")

    @patch.object(da, "_run")
    def test_container(self, mock_run):
        mock_run.return_value = {"success": True, "stdout":
            "@@NGINX@@\nnone\n@@APACHE@@\nnone\n@@PHP@@\nnone\n@@NODE@@\nnone\n"
            "@@MYSQL@@\nnone\n@@PGSQL@@\nnone\n@@MONGOD@@\nnone\n"
            "@@DOCKER@@\n/usr/bin/docker\nfound\n@@CONTAINERS@@\n3\n", "stderr": "", "exit_code": 0}
        stype, _ = da._detect_type()
        self.assertEqual(stype, "container")

    @patch.object(da, "_run")
    def test_general(self, mock_run):
        mock_run.return_value = {"success": True, "stdout":
            "@@NGINX@@\nnone\n@@APACHE@@\nnone\n@@PHP@@\nnone\n@@NODE@@\nnone\n"
            "@@MYSQL@@\nnone\n@@PGSQL@@\nnone\n@@MONGOD@@\nnone\n"
            "@@DOCKER@@\nnone\n@@CONTAINERS@@\n0\n", "stderr": "", "exit_code": 0}
        stype, _ = da._detect_type()
        self.assertEqual(stype, "general")


# ===========================================================================
# _derive_mode
# ===========================================================================
class TestDeriveMode(unittest.TestCase):

    def setUp(self):
        self._orig_fold = da._mem_fold
        da._mem_fold = lambda: {}

    def tearDown(self):
        da._mem_fold = self._orig_fold

    def test_web_app_setup_no_web(self):
        p = {"type": "web-app", "stacks": {}, "app": None, "base": {"uptime_days": 0, "disk_pct": 10}}
        self.assertEqual(da._derive_mode(p), "setup")

    def test_web_app_setup_no_env(self):
        p = {"type": "web-app",
             "stacks": {"web": {"status": "active"}, "runtime": {"fpm": "php8.3-fpm"}},
             "app": {"exists": True, "env_exists": False, "vendor_exists": True},
             "base": {"uptime_days": 30, "disk_pct": 40}}
        self.assertEqual(da._derive_mode(p), "setup")

    def test_web_app_deploy(self):
        p = {"type": "web-app",
             "stacks": {"web": {"status": "active"}, "runtime": {"fpm": "php8.3-fpm"}},
             "app": {"exists": True, "env_exists": True, "vendor_exists": True},
             "base": {"uptime_days": 2, "disk_pct": 40}}
        self.assertEqual(da._derive_mode(p), "deploy")

    def test_web_app_production(self):
        p = {"type": "web-app",
             "stacks": {"web": {"status": "active"}, "runtime": {"fpm": "php8.3-fpm"}},
             "app": {"exists": True, "env_exists": True, "vendor_exists": True},
             "base": {"uptime_days": 30, "disk_pct": 40}}
        self.assertEqual(da._derive_mode(p), "production")

    def test_web_app_high_disk_stays_deploy(self):
        p = {"type": "web-app",
             "stacks": {"web": {"status": "active"}, "runtime": {"fpm": "php8.3-fpm"}},
             "app": {"exists": True, "env_exists": True, "vendor_exists": True},
             "base": {"uptime_days": 30, "disk_pct": 85}}
        self.assertEqual(da._derive_mode(p), "deploy")

    def test_database_setup(self):
        p = {"type": "database", "stacks": {}, "app": None, "base": {"uptime_days": 0}}
        self.assertEqual(da._derive_mode(p), "setup")

    def test_database_production(self):
        p = {"type": "database",
             "stacks": {"database": {"status": "active"}},
             "app": None, "base": {"uptime_days": 14}}
        self.assertEqual(da._derive_mode(p), "production")

    def test_container_setup(self):
        p = {"type": "container", "stacks": {}, "app": None, "base": {"uptime_days": 0}}
        self.assertEqual(da._derive_mode(p), "setup")

    def test_container_production(self):
        p = {"type": "container",
             "stacks": {"docker": {"status": "active", "running_count": 5}},
             "app": None, "base": {"uptime_days": 10}}
        self.assertEqual(da._derive_mode(p), "production")

    def test_general_always_deploy(self):
        p = {"type": "general", "stacks": {}, "app": None, "base": {"uptime_days": 100}}
        self.assertEqual(da._derive_mode(p), "deploy")

    def test_mode_override(self):
        da._mem_fold = lambda: {"server:mode-override": {"text": "setup"}}
        p = {"type": "web-app",
             "stacks": {"web": {"status": "active"}, "runtime": {"fpm": "php8.3-fpm"}},
             "app": {"exists": True, "env_exists": True, "vendor_exists": True},
             "base": {"uptime_days": 30, "disk_pct": 40}}
        self.assertEqual(da._derive_mode(p), "setup")


# ===========================================================================
# _mode_gate
# ===========================================================================
class TestModeGate(unittest.TestCase):

    def setUp(self):
        self._orig = da._CURRENT_MODE

    def tearDown(self):
        da._CURRENT_MODE = self._orig

    def test_deploy_allows_everything(self):
        da._CURRENT_MODE = "deploy"
        self.assertIsNone(da._mode_gate("laravel_deploy"))
        self.assertIsNone(da._mode_gate("run_command", "apt install nginx"))

    def test_setup_allows_everything(self):
        da._CURRENT_MODE = "setup"
        self.assertIsNone(da._mode_gate("laravel_deploy"))

    def test_production_blocks_deploy(self):
        da._CURRENT_MODE = "production"
        r = da._mode_gate("laravel_deploy")
        self.assertIsNotNone(r)
        self.assertTrue(r["blocked_by_mode"])

    def test_production_blocks_apt_install(self):
        da._CURRENT_MODE = "production"
        r = da._mode_gate("run_command", "sudo apt install nginx")
        self.assertIsNotNone(r)
        self.assertIn("PRODUCTION", r["error"])

    def test_production_blocks_pip_install(self):
        da._CURRENT_MODE = "production"
        r = da._mode_gate("run_command", "pip3 install requests")
        self.assertIsNotNone(r)

    def test_production_allows_read(self):
        da._CURRENT_MODE = "production"
        self.assertIsNone(da._mode_gate("run_command", "cat /etc/nginx/nginx.conf"))
        self.assertIsNone(da._mode_gate("run_command", "git status"))

    def test_production_allows_service_status(self):
        da._CURRENT_MODE = "production"
        self.assertIsNone(da._mode_gate("service_action", "systemctl status nginx"))

    def test_production_blocks_npm_install_in_runbook(self):
        da._CURRENT_MODE = "production"
        r = da._mode_gate("run_command", "npm ci && npm run build")
        self.assertIsNotNone(r)


# ===========================================================================
# inspect_server tool
# ===========================================================================
class TestInspectServerTool(unittest.TestCase):

    def setUp(self):
        da._SESSION_LOG.clear()

    @patch.object(da, "_save_profile_summary")
    @patch.object(da, "_mem_fold", return_value={})
    @patch.object(da, "_run")
    def test_inspect_returns_profile(self, mock_run, mock_fold, mock_save):
        mock_run.side_effect = lambda cmd, *a, **kw: {
            "success": True, "exit_code": 0, "stderr": "",
            "stdout": "@@OS@@\nUbuntu\n@@KERNEL@@\n6.8\n@@UPTIME@@\n2026-01-01 00:00:00\n"
                      "@@DISK@@\n42%\n@@MEM_PCT@@\n50\n@@MEM@@\n1024/2048MB\n"
                      "@@UFW@@\nactive\n@@F2B@@\nactive\n@@SSHPORT@@\n22\n"
                      "@@SSHAUTH@@\nyes\n@@CRON@@\n0\n@@USERS@@\nroot,deploy\n"
                      "@@NGINX@@\nnone\n@@APACHE@@\nnone\n@@PHP@@\nnone\n"
                      "@@NODE@@\nnone\n@@MYSQL@@\nnone\n@@PGSQL@@\nnone\n"
                      "@@MONGOD@@\nnone\n@@DOCKER@@\nnone\n@@CONTAINERS@@\n0\n"
        }
        result = da.inspect_server()
        self.assertTrue(result["success"])
        self.assertIn("profile", result)
        self.assertIn("mode", result)
        self.assertEqual(result["profile"]["type"], "general")


# ===========================================================================
# _inspect_app
# ===========================================================================
class TestInspectApp(unittest.TestCase):

    def test_no_path(self):
        self.assertIsNone(da._inspect_app(""))

    @patch.object(da, "_run")
    def test_app_not_exists(self, mock_run):
        mock_run.return_value = {"success": True, "stdout":
            "@@EXISTS@@\nno\n@@ENV@@\nno\n@@VENDOR@@\nno\n@@NODEMOD@@\nno\n"
            "@@ARTISAN@@\nno\n@@MANAGE@@\nno\n@@PKGJSON@@\nno\n@@STORAGE@@\nno\n"
            "@@GIT@@\nnone\n@@BRANCH@@\nnone\n@@DIRTY@@\n0\n", "stderr": "", "exit_code": 0}
        r = da._inspect_app("/var/www/app")
        self.assertFalse(r["exists"])

    @patch.object(da, "_run")
    def test_laravel_app(self, mock_run):
        mock_run.return_value = {"success": True, "stdout":
            "@@EXISTS@@\nyes\n@@ENV@@\nyes\n@@VENDOR@@\nyes\n@@NODEMOD@@\nno\n"
            "@@ARTISAN@@\nyes\n@@MANAGE@@\nno\n@@PKGJSON@@\nyes\n@@STORAGE@@\nyes\n"
            "@@GIT@@\nabc1234 initial\n@@BRANCH@@\nmain\n@@DIRTY@@\n0\n",
            "stderr": "", "exit_code": 0}
        r = da._inspect_app("/var/www/simuru")
        self.assertTrue(r["exists"])
        self.assertEqual(r["framework"], "laravel")
        self.assertTrue(r["env_exists"])
        self.assertTrue(r["vendor_exists"])
        self.assertEqual(r["git_branch"], "main")

    @patch.object(da, "_run")
    def test_django_app(self, mock_run):
        mock_run.return_value = {"success": True, "stdout":
            "@@EXISTS@@\nyes\n@@ENV@@\nyes\n@@VENDOR@@\nno\n@@NODEMOD@@\nno\n"
            "@@ARTISAN@@\nno\n@@MANAGE@@\nyes\n@@PKGJSON@@\nno\n@@STORAGE@@\nno\n"
            "@@GIT@@\ndef456 django\n@@BRANCH@@\ndev\n@@DIRTY@@\n2\n",
            "stderr": "", "exit_code": 0}
        r = da._inspect_app("/var/www/mysite")
        self.assertEqual(r["framework"], "django")
        self.assertEqual(r["git_dirty"], 2)


# ===========================================================================
# Guard: _get_mode
# ===========================================================================
class TestGuardGetMode(unittest.TestCase):

    def test_default_deploy(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("builtins.open", side_effect=FileNotFoundError):
                self.assertEqual(guard._get_mode(), "deploy")

    def test_env_var(self):
        with patch.dict(os.environ, {"ODIN_MODE": "production"}):
            with patch("builtins.open", side_effect=FileNotFoundError):
                self.assertEqual(guard._get_mode(), "production")

    def test_invalid_env_var(self):
        with patch.dict(os.environ, {"ODIN_MODE": "invalid"}):
            with patch("builtins.open", side_effect=FileNotFoundError):
                self.assertEqual(guard._get_mode(), "deploy")


# ===========================================================================
# Guard: tier shifting
# ===========================================================================
class TestGuardTierShift(unittest.TestCase):

    def test_shift_rendah_to_sedang(self):
        self.assertEqual(guard._shift_tier("RENDAH"), "SEDANG")

    def test_shift_sedang_to_tinggi(self):
        self.assertEqual(guard._shift_tier("SEDANG"), "TINGGI")

    def test_shift_tinggi_to_kritis(self):
        self.assertEqual(guard._shift_tier("TINGGI"), "KRITIS")

    def test_shift_kritis_stays(self):
        self.assertEqual(guard._shift_tier("KRITIS"), "KRITIS")

    def test_risk_card_production_mentions_mode(self):
        card = guard.risk_card("rm -rf /tmp/old", mode="production")
        self.assertIn("PRODUCTION", card)

    def test_service_card_production_shifts(self):
        card = guard.service_card("nginx", "restart", mode="production")
        self.assertIn("PRODUCTION", card)
        self.assertIn("TINGGI", card)


# ===========================================================================
# Integration: run_command respects mode
# ===========================================================================
class TestRunCommandModeGate(unittest.TestCase):

    def setUp(self):
        self._orig = da._CURRENT_MODE

    def tearDown(self):
        da._CURRENT_MODE = self._orig

    @patch.object(da, "_run")
    def test_production_blocks_apt_install(self, mock_run):
        da._CURRENT_MODE = "production"
        result = da.run_command("sudo apt install nginx", "/tmp")
        self.assertFalse(result["success"])
        self.assertTrue(result.get("blocked_by_mode"))

    @patch.object(da, "_run")
    def test_deploy_allows_apt_install(self, mock_run):
        da._CURRENT_MODE = "deploy"
        mock_run.return_value = {"success": True, "stdout": "ok", "stderr": "",
                                 "exit_code": 0, "duration_sec": 0.1}
        result = da.run_command("sudo apt install nginx", "/tmp")
        self.assertTrue(result["success"])

    @patch.object(da, "_run")
    def test_production_allows_read(self, mock_run):
        da._CURRENT_MODE = "production"
        mock_run.return_value = {"success": True, "stdout": "ok", "stderr": "",
                                 "exit_code": 0, "duration_sec": 0.1}
        result = da.run_command("cat /etc/nginx/nginx.conf", "/tmp")
        self.assertTrue(result["success"])


# ===========================================================================
# Web-root sebagai NIAT (catatan memory), bukan prasyarat run.sh
# ===========================================================================
class TestWebRootNote(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = {"MEMORY_DIR": da.MEMORY_DIR, "MEMORY_FILE": da.MEMORY_FILE,
                      "PROJECT_ROOT": da.PROJECT_ROOT, "PROJECT_NAME": da.PROJECT_NAME,
                      "_CURRENT_MODE": da._CURRENT_MODE}
        da.MEMORY_DIR = self.tmpdir
        da.MEMORY_FILE = os.path.join(self.tmpdir, "memory.jsonl")
        da.PROJECT_ROOT = "/var/www/proyek-baru"
        da.PROJECT_NAME = "proyek-baru"
        da._fold_invalidate()

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(da, k, v)
        da._fold_invalidate()

    def _lines(self):
        if not os.path.exists(da.MEMORY_FILE):
            return 0
        return sum(1 for ln in open(da.MEMORY_FILE) if ln.strip())

    def _patch_root_isdir(self, present):
        """Patch isdir HANYA untuk PROJECT_ROOT; path lain pakai isdir asli
        (mem-patch global merusak os.makedirs(exist_ok=True))."""
        real = os.path.isdir
        return patch.object(da.os.path, "isdir",
                            side_effect=lambda p: present if p == da.PROJECT_ROOT else real(p))

    def test_seed_creates_pinned_note_when_dir_missing(self):
        with self._patch_root_isdir(False):
            da._seed_web_root_note()
        rec = da._mem_fold().get("server:web-root")
        self.assertIsNotNone(rec)
        self.assertTrue(rec["pinned"])
        self.assertIn("web-root", rec["tags"])
        self.assertIn("/var/www/proyek-baru", rec["text"])
        self.assertIn("BELUM dibuat", rec["text"])

    def test_seed_is_idempotent(self):
        with self._patch_root_isdir(False):
            da._seed_web_root_note()
            da._seed_web_root_note()
            da._seed_web_root_note()
        self.assertEqual(self._lines(), 1)

    def test_note_updates_when_dir_created(self):
        with self._patch_root_isdir(False):
            da._seed_web_root_note()
        with self._patch_root_isdir(True):
            da._seed_web_root_note()
        rec = da._mem_fold().get("server:web-root")
        self.assertIn("sudah ada", rec["text"])
        self.assertEqual(self._lines(), 2)  # 1 awal + 1 update

    def test_no_seed_without_project_root(self):
        da.PROJECT_ROOT = ""
        da._seed_web_root_note()
        self.assertEqual(self._lines(), 0)

    def test_setup_instructions_mention_webroot_and_nginx(self):
        da._CURRENT_MODE = "setup"
        with self._patch_root_isdir(False):
            ins = da._build_instructions()
        self.assertIn("/var/www/proyek-baru", ins)
        self.assertIn("mkdir", ins)
        self.assertIn("document root", ins)


# ===========================================================================
# P1 — verifikasi identitas server (anti mis-route)
# ===========================================================================
class TestServerIdentity(unittest.TestCase):

    def test_read_machine_id_returns_str(self):
        self.assertIsInstance(da._read_machine_id(), str)

    def test_skip_when_no_server_id(self):
        # SERVER_ID kosong (server lama / belum di-seed) → lewati, tidak exit
        with patch.dict(da.os.environ, {"SERVER_ID": "", "ODIN_MACHINE_ID": "abc"}, clear=False):
            da._assert_server_identity()

    def test_pass_when_match(self):
        with patch.dict(da.os.environ, {"SERVER_ID": "abc123", "ODIN_MACHINE_ID": "abc123"}, clear=False):
            da._assert_server_identity()

    def test_exit_when_mismatch(self):
        with patch.dict(da.os.environ, {"SERVER_ID": "server-A", "ODIN_MACHINE_ID": "server-B"}, clear=False):
            with self.assertRaises(SystemExit) as cm:
                da._assert_server_identity()
            self.assertEqual(cm.exception.code, 1)

    def test_skip_when_machine_id_unreadable(self):
        # SERVER_ID diset tapi machine-id tak terbaca → WARN + lewati (jangan brick)
        with patch.dict(da.os.environ, {"SERVER_ID": "expected", "ODIN_MACHINE_ID": ""}, clear=False), \
             patch.object(da, "_read_machine_id", return_value=""):
            da._assert_server_identity()

    def test_fallback_read_machine_id_match(self):
        # ODIN_MACHINE_ID kosong tapi _read_machine_id cocok → tidak exit
        with patch.dict(da.os.environ, {"SERVER_ID": "xyz", "ODIN_MACHINE_ID": ""}, clear=False), \
             patch.object(da, "_read_machine_id", return_value="xyz"):
            da._assert_server_identity()


if __name__ == "__main__":
    unittest.main(verbosity=2)
