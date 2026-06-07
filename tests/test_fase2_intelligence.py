"""Test Fase 2 — Intelligence: error tracking + command suggestion,
deploy config persistence, trend detection."""
import sys, types, os, json, tempfile, unittest
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
import importlib.util
spec = importlib.util.spec_from_file_location("odin_agent", ROOT / "server" / "odin_agent.py")
da = importlib.util.module_from_spec(spec)
spec.loader.exec_module(da)


def _fail(stderr="", stdout="", exit_code=1):
    return {"success": False, "exit_code": exit_code, "stdout": stdout, "stderr": stderr}


# ===========================================================================
# 2.1 Command Suggestion Engine — suggested_commands di _analyze_output
# ===========================================================================
class TestCommandSuggestion(unittest.TestCase):

    def test_db_conn_has_suggestions(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[HY000] [2002] Connection refused"))
        self.assertIn("suggested_commands", a)
        cmds = [s["cmd"] for s in a["suggested_commands"]]
        self.assertTrue(any("systemctl status mysql" in c for c in cmds))

    def test_db_auth_has_suggestions(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[HY000] [1045] Access denied"))
        self.assertIn("suggested_commands", a)

    def test_disk_full_has_suggestions(self):
        a = da._analyze_output(_fail(stderr="No space left on device"))
        self.assertIn("suggested_commands", a)
        cmds = [s["cmd"] for s in a["suggested_commands"]]
        self.assertTrue(any("df -h" in c for c in cmds))

    def test_nginx_config_has_suggestions(self):
        a = da._analyze_output(_fail(stderr="nginx: [emerg] unexpected end"))
        self.assertIn("suggested_commands", a)
        cmds = [s["cmd"] for s in a["suggested_commands"]]
        self.assertTrue(any("nginx -t" in c for c in cmds))

    def test_npm_error_has_suggestions(self):
        a = da._analyze_output(_fail(stderr="npm ERR! code ELIFECYCLE"))
        self.assertIn("suggested_commands", a)

    def test_class_not_found_has_suggestions(self):
        a = da._analyze_output(_fail(stderr="Class 'App\\X' not found"))
        self.assertIn("suggested_commands", a)
        cmds = [s["cmd"] for s in a["suggested_commands"]]
        self.assertTrue(any("composer" in c for c in cmds))

    def test_pattern_without_suggestions(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[23000] Duplicate entry"))
        self.assertNotIn("suggested_commands", a)

    def test_suggestion_has_risk_field(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[HY000] [2002] Connection refused"))
        for s in a["suggested_commands"]:
            self.assertIn("risk", s)
            self.assertIn(s["risk"], ("AMAN", "RENDAH", "SEDANG", "TINGGI"))

    def test_backward_compat_3_tuple(self):
        a = da._analyze_output(_fail(stderr="SQLSTATE[23000] Integrity constraint"))
        self.assertEqual(a["error_type"], "db_constraint")
        self.assertNotIn("suggested_commands", a)


# ===========================================================================
# 2.1 Error Frequency Tracking — _error_counts + recurring
# ===========================================================================
class TestErrorFrequencyTracking(unittest.TestCase):

    def setUp(self):
        da._error_counts.clear()

    def test_first_error_no_recurring(self):
        a = da._analyze_output(_fail(stderr="Permission denied"))
        self.assertNotIn("recurring", a)

    def test_second_error_no_recurring(self):
        da._analyze_output(_fail(stderr="Permission denied"))
        a = da._analyze_output(_fail(stderr="Permission denied"))
        self.assertNotIn("recurring", a)

    def test_third_error_recurring(self):
        da._analyze_output(_fail(stderr="Permission denied"))
        da._analyze_output(_fail(stderr="Permission denied"))
        a = da._analyze_output(_fail(stderr="Permission denied"))
        self.assertTrue(a.get("recurring"))
        self.assertIn("recurring_hint", a)
        self.assertIn("3x", a["recurring_hint"])

    def test_different_errors_counted_separately(self):
        da._analyze_output(_fail(stderr="Permission denied"))
        da._analyze_output(_fail(stderr="Permission denied"))
        da._analyze_output(_fail(stderr="No space left on device"))
        a = da._analyze_output(_fail(stderr="Permission denied"))
        self.assertTrue(a.get("recurring"))
        self.assertEqual(da._error_counts.get("disk_full"), 1)

    def test_counter_increments(self):
        for _ in range(5):
            da._analyze_output(_fail(stderr="Permission denied"))
        self.assertEqual(da._error_counts["permission"], 5)


# ===========================================================================
# 2.2 Deploy Config Persistence
# ===========================================================================
class MemoryTestBase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_dir = da.MEMORY_DIR
        self._orig_file = da.MEMORY_FILE
        da.MEMORY_DIR = self.tmpdir
        da.MEMORY_FILE = os.path.join(self.tmpdir, "memory.jsonl")
        da._fold_invalidate()

    def tearDown(self):
        da.MEMORY_DIR = self._orig_dir
        da.MEMORY_FILE = self._orig_file
        da._fold_invalidate()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestDeployConfigPersistence(MemoryTestBase):

    def test_load_empty(self):
        defaults = da._load_deploy_defaults()
        self.assertEqual(defaults, {})

    def test_save_and_load(self):
        da._save_deploy_config("/var/www/app", "main", "php8.3-fpm", False)
        defaults = da._load_deploy_defaults()
        self.assertEqual(defaults["app_path"], "/var/www/app")
        self.assertEqual(defaults["branch"], "main")
        self.assertEqual(defaults["fpm_service"], "php8.3-fpm")
        self.assertFalse(defaults["npm_build"])

    def test_upsert_overwrites(self):
        da._save_deploy_config("/var/www/app", "main", "php8.2-fpm", False)
        da._save_deploy_config("/var/www/app", "develop", "php8.3-fpm", True)
        defaults = da._load_deploy_defaults()
        self.assertEqual(defaults["branch"], "develop")
        self.assertEqual(defaults["fpm_service"], "php8.3-fpm")
        self.assertTrue(defaults["npm_build"])

    def test_saved_as_pinned(self):
        da._save_deploy_config("/var/www/app", "main", "", False)
        fold = da._mem_fold()
        rec = fold.get("server:deploy-config")
        self.assertIsNotNone(rec)
        self.assertTrue(rec.get("pinned"))


# ===========================================================================
# 2.3 Trend Detection
# ===========================================================================
class TestTrendDetection(MemoryTestBase):

    def test_compute_trend_empty_history(self):
        trend = da._compute_trend({"disk_pct": 50, "memory_pct": 60}, [])
        self.assertEqual(trend, {})

    def test_compute_trend_stable(self):
        current = {"disk_pct": 50, "memory_pct": 60}
        history = [{"disk_pct": 50, "memory_pct": 61, "ts": "2026-06-01"}]
        trend = da._compute_trend(current, history)
        self.assertEqual(trend["disk_pct"]["direction"], "stabil")
        self.assertEqual(trend["memory_pct"]["direction"], "stabil")

    def test_compute_trend_disk_rising(self):
        current = {"disk_pct": 75, "memory_pct": 60}
        history = [{"disk_pct": 60, "memory_pct": 60, "ts": "2026-06-01"}]
        trend = da._compute_trend(current, history)
        self.assertEqual(trend["disk_pct"]["direction"], "naik")
        self.assertEqual(trend["disk_pct"]["delta"], 15)
        self.assertIn("+15%", trend["disk_pct"]["summary"])

    def test_compute_trend_memory_dropping(self):
        current = {"disk_pct": 50, "memory_pct": 40}
        history = [{"disk_pct": 50, "memory_pct": 55, "ts": "2026-06-01"}]
        trend = da._compute_trend(current, history)
        self.assertEqual(trend["memory_pct"]["direction"], "turun")
        self.assertEqual(trend["memory_pct"]["delta"], -15)

    def test_save_metrics_snapshot(self):
        da._save_metrics_snapshot({"disk_pct": 55, "memory_pct": 45, "uptime_days": 30})
        fold = da._mem_fold()
        rec = fold.get("server:metrics-history")
        self.assertIsNotNone(rec)
        history = json.loads(rec["text"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["disk_pct"], 55)

    def test_ring_buffer_max_7(self):
        for i in range(10):
            da._save_metrics_snapshot({"disk_pct": 50 + i, "memory_pct": 40, "uptime_days": i})
        fold = da._mem_fold()
        rec = fold.get("server:metrics-history")
        history = json.loads(rec["text"])
        self.assertEqual(len(history), 7)
        self.assertEqual(history[0]["disk_pct"], 53)
        self.assertEqual(history[-1]["disk_pct"], 59)

    def test_snapshot_appends(self):
        da._save_metrics_snapshot({"disk_pct": 50, "memory_pct": 40, "uptime_days": 1})
        da._save_metrics_snapshot({"disk_pct": 55, "memory_pct": 42, "uptime_days": 2})
        fold = da._mem_fold()
        history = json.loads(fold["server:metrics-history"]["text"])
        self.assertEqual(len(history), 2)


if __name__ == "__main__":
    unittest.main()
