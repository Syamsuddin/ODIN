"""Test Fase 4 — Proactive Intelligence: deploy fingerprint & drift,
context window budget, watchdog resource."""
import sys, types, os, json, tempfile, unittest
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


# ===========================================================================
# 4.1 Deploy Fingerprint & Drift Detection
# ===========================================================================
class TestCaptureDeployFingerprint(unittest.TestCase):

    @patch.object(da, "_run")
    def test_captures_git_hash(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "abc1234def\n"}
        fp = da._capture_deploy_fingerprint("/var/www/app")
        self.assertEqual(fp["git_hash"], "abc1234def")

    @patch.object(da, "_run")
    def test_captures_composer_lock_md5(self, mock_run):
        def side_effect(cmd, *a, **kw):
            if "md5sum" in cmd:
                return {"success": True, "stdout": "d41d8cd98f00b204e9800998ecf8427e\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        fp = da._capture_deploy_fingerprint("/var/www/app")
        self.assertEqual(fp["composer_lock_md5"], "d41d8cd98f00b204e9800998ecf8427e")

    @patch.object(da, "_run")
    def test_captures_migration_count(self, mock_run):
        def side_effect(cmd, *a, **kw):
            if "migrate:status" in cmd:
                return {"success": True, "stdout": "12\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        fp = da._capture_deploy_fingerprint("/var/www/app")
        self.assertEqual(fp["migration_count"], 12)

    @patch.object(da, "_run")
    def test_captures_env_lines(self, mock_run):
        def side_effect(cmd, *a, **kw):
            if "wc -l" in cmd:
                return {"success": True, "stdout": "45\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        fp = da._capture_deploy_fingerprint("/var/www/app")
        self.assertEqual(fp["env_lines"], 45)

    @patch.object(da, "_run")
    def test_has_captured_at(self, mock_run):
        mock_run.return_value = {"success": False, "stdout": ""}
        fp = da._capture_deploy_fingerprint("/var/www/app")
        self.assertIn("captured_at", fp)

    @patch.object(da, "_run")
    def test_handles_all_failures(self, mock_run):
        mock_run.return_value = {"success": False, "stdout": ""}
        fp = da._capture_deploy_fingerprint("/var/www/app")
        self.assertNotIn("git_hash", fp)
        self.assertNotIn("composer_lock_md5", fp)
        self.assertIn("captured_at", fp)


class TestDeployFingerprintPersistence(MemoryTestBase):

    def test_save_and_load(self):
        fp = {"git_hash": "abc123", "composer_lock_md5": "d41d8c",
              "migration_count": 10, "env_lines": 45, "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(fp)
        loaded = da._load_deploy_fingerprint()
        self.assertEqual(loaded["git_hash"], "abc123")
        self.assertEqual(loaded["migration_count"], 10)

    def test_load_empty(self):
        result = da._load_deploy_fingerprint()
        self.assertEqual(result, {})

    def test_upsert_overwrites(self):
        fp1 = {"git_hash": "aaa", "captured_at": da._now_iso()}
        fp2 = {"git_hash": "bbb", "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(fp1)
        da._save_deploy_fingerprint(fp2)
        loaded = da._load_deploy_fingerprint()
        self.assertEqual(loaded["git_hash"], "bbb")

    def test_saved_as_pinned(self):
        fp = {"git_hash": "abc", "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(fp)
        fold = da._mem_fold()
        rec = fold.get("server:deploy-fingerprint")
        self.assertTrue(rec.get("pinned"))


class TestDetectDrift(MemoryTestBase):

    def test_no_previous_fingerprint(self):
        drift = da._detect_drift("/var/www/app")
        self.assertEqual(drift, {})

    @patch.object(da, "_run")
    def test_no_drift(self, mock_run):
        prev = {"git_hash": "abc123", "composer_lock_md5": "d41d8c",
                "migration_count": 10, "env_lines": 45, "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(prev)

        def side_effect(cmd, *a, **kw):
            if "rev-parse" in cmd:
                return {"success": True, "stdout": "abc123\n"}
            if "md5sum" in cmd:
                return {"success": True, "stdout": "d41d8c\n"}
            if "migrate:status" in cmd:
                return {"success": True, "stdout": "10\n"}
            if "wc -l" in cmd:
                return {"success": True, "stdout": "45\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        drift = da._detect_drift("/var/www/app")
        self.assertFalse(drift["has_drift"])
        self.assertEqual(len(drift["changes"]), 0)

    @patch.object(da, "_run")
    def test_git_drift(self, mock_run):
        prev = {"git_hash": "abc123", "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(prev)

        def side_effect(cmd, *a, **kw):
            if "rev-parse" in cmd:
                return {"success": True, "stdout": "def456\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        drift = da._detect_drift("/var/www/app")
        self.assertTrue(drift["has_drift"])
        self.assertTrue(any("Git HEAD" in c for c in drift["changes"]))

    @patch.object(da, "_run")
    def test_composer_drift(self, mock_run):
        prev = {"composer_lock_md5": "aaa111", "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(prev)

        def side_effect(cmd, *a, **kw):
            if "md5sum" in cmd:
                return {"success": True, "stdout": "bbb222  composer.lock\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        drift = da._detect_drift("/var/www/app")
        self.assertTrue(drift["has_drift"])
        self.assertTrue(any("composer.lock" in c for c in drift["changes"]))

    @patch.object(da, "_run")
    def test_migration_drift(self, mock_run):
        prev = {"migration_count": 10, "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(prev)

        def side_effect(cmd, *a, **kw):
            if "migrate:status" in cmd:
                return {"success": True, "stdout": "13\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        drift = da._detect_drift("/var/www/app")
        self.assertTrue(drift["has_drift"])
        self.assertTrue(any("+3 migrasi" in c for c in drift["changes"]))

    @patch.object(da, "_run")
    def test_env_drift(self, mock_run):
        prev = {"env_lines": 45, "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(prev)

        def side_effect(cmd, *a, **kw):
            if "wc -l" in cmd:
                return {"success": True, "stdout": "48\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        drift = da._detect_drift("/var/www/app")
        self.assertTrue(drift["has_drift"])
        self.assertTrue(any(".env berubah" in c for c in drift["changes"]))

    @patch.object(da, "_run")
    def test_migration_rollback_detected(self, mock_run):
        prev = {"migration_count": 15, "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(prev)

        def side_effect(cmd, *a, **kw):
            if "migrate:status" in cmd:
                return {"success": True, "stdout": "12\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect
        drift = da._detect_drift("/var/www/app")
        self.assertTrue(drift["has_drift"])
        self.assertTrue(any("rollback" in c.lower() for c in drift["changes"]))


# ===========================================================================
# 4.2 Context Window Budget — _smart_output
# ===========================================================================
class TestSmartOutput(unittest.TestCase):

    def test_short_output_unchanged(self):
        result = {"stdout": "hello\nworld", "success": True}
        out = da._smart_output(result)
        self.assertEqual(out["stdout"], "hello\nworld")
        self.assertNotIn("_output_meta", out)

    def test_empty_output_unchanged(self):
        result = {"stdout": "", "success": True}
        out = da._smart_output(result)
        self.assertEqual(out["stdout"], "")
        self.assertNotIn("_output_meta", out)

    def test_no_stdout_unchanged(self):
        result = {"success": True}
        out = da._smart_output(result)
        self.assertNotIn("_output_meta", out)

    def test_large_output_truncated(self):
        lines = [f"line-{i:04d}" for i in range(1000)]
        big_stdout = "\n".join(lines)
        result = {"stdout": big_stdout, "success": True}
        out = da._smart_output(result)
        self.assertIn("_output_meta", out)
        meta = out["_output_meta"]
        self.assertTrue(meta["truncated"])
        self.assertEqual(meta["total_lines"], 1000)
        self.assertEqual(meta["total_chars"], len(big_stdout))
        self.assertEqual(meta["head_lines"], 5)
        self.assertEqual(meta["tail_lines"], 10)

    def test_truncated_keeps_head_and_tail(self):
        lines = [f"line-{i:04d}" for i in range(1000)]
        big_stdout = "\n".join(lines)
        result = {"stdout": big_stdout, "success": True}
        out = da._smart_output(result)
        self.assertIn("line-0000", out["stdout"])
        self.assertIn("line-0004", out["stdout"])
        self.assertIn("line-0999", out["stdout"])
        self.assertIn("line-0990", out["stdout"])
        self.assertNotIn("line-0500", out["stdout"])

    def test_truncated_has_summary_marker(self):
        lines = [f"line-{i:04d} {'x' * 50}" for i in range(200)]
        big_stdout = "\n".join(lines)
        self.assertGreater(len(big_stdout), da.CONTEXT_BUDGET)
        result = {"stdout": big_stdout, "success": True}
        out = da._smart_output(result)
        self.assertIn("baris diringkas", out["stdout"])
        self.assertIn("total 200 baris", out["stdout"])

    def test_exactly_at_budget_unchanged(self):
        text = "x" * da.CONTEXT_BUDGET
        result = {"stdout": text, "success": True}
        out = da._smart_output(result)
        self.assertEqual(out["stdout"], text)
        self.assertNotIn("_output_meta", out)

    def test_one_over_budget_truncated(self):
        text = "x\n" * (da.CONTEXT_BUDGET // 2 + 1)
        result = {"stdout": text, "success": True}
        if len(text) > da.CONTEXT_BUDGET:
            out = da._smart_output(result)
            self.assertIn("_output_meta", out)

    def test_other_fields_preserved(self):
        lines = [f"line-{i}" for i in range(500)]
        result = {"stdout": "\n".join(lines), "success": True,
                  "exit_code": 0, "command": "ls"}
        out = da._smart_output(result)
        self.assertTrue(out["success"])
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["command"], "ls")


# ===========================================================================
# 4.3 Watchdog Resource — health://live
# ===========================================================================
class TestWatchdogResource(unittest.TestCase):

    @patch.object(da, "_run")
    def test_healthy_returns_ok(self, mock_run):
        mock_run.return_value = {
            "success": True,
            "stdout": (
                "@@DISK@@\n 45%\n"
                "@@MEM@@\n60\n"
                "@@LOAD@@\n0.52\n"
                "@@NGINX@@\nactive\n"
                "@@MYSQL@@\nactive\n"
                "@@FPM@@\nphp8.3-fpm\n"
            ),
        }
        result = da.health_live()
        self.assertIn("status: OK", result)
        self.assertIn("disk: 45%", result)
        self.assertIn("memory: 60%", result)
        self.assertIn("nginx: active", result)
        self.assertIn("mysql: active", result)
        self.assertNotIn("issues", result)

    @patch.object(da, "_run")
    def test_high_disk_anomaly(self, mock_run):
        mock_run.return_value = {
            "success": True,
            "stdout": (
                "@@DISK@@\n 90%\n"
                "@@MEM@@\n50\n"
                "@@LOAD@@\n0.1\n"
                "@@NGINX@@\nactive\n"
                "@@MYSQL@@\nactive\n"
                "@@FPM@@\nnone\n"
            ),
        }
        result = da.health_live()
        self.assertIn("status: ANOMALI", result)
        self.assertIn("DISK 90%", result)

    @patch.object(da, "_run")
    def test_high_memory_anomaly(self, mock_run):
        mock_run.return_value = {
            "success": True,
            "stdout": (
                "@@DISK@@\n 50%\n"
                "@@MEM@@\n95\n"
                "@@LOAD@@\n0.1\n"
                "@@NGINX@@\nactive\n"
                "@@MYSQL@@\nactive\n"
                "@@FPM@@\nnone\n"
            ),
        }
        result = da.health_live()
        self.assertIn("status: ANOMALI", result)
        self.assertIn("MEMORY 95%", result)

    @patch.object(da, "_run")
    def test_service_down_anomaly(self, mock_run):
        mock_run.return_value = {
            "success": True,
            "stdout": (
                "@@DISK@@\n 50%\n"
                "@@MEM@@\n50\n"
                "@@LOAD@@\n0.1\n"
                "@@NGINX@@\ninactive\n"
                "@@MYSQL@@\nactive\n"
                "@@FPM@@\nnone\n"
            ),
        }
        result = da.health_live()
        self.assertIn("status: ANOMALI", result)
        self.assertIn("SERVICE nginx: inactive", result)

    @patch.object(da, "_run")
    def test_multiple_issues(self, mock_run):
        mock_run.return_value = {
            "success": True,
            "stdout": (
                "@@DISK@@\n 92%\n"
                "@@MEM@@\n95\n"
                "@@LOAD@@\n4.2\n"
                "@@NGINX@@\nfailed\n"
                "@@MYSQL@@\nactive\n"
                "@@FPM@@\nnone\n"
            ),
        }
        result = da.health_live()
        self.assertIn("status: ANOMALI", result)
        self.assertIn("DISK", result)
        self.assertIn("MEMORY", result)
        self.assertIn("SERVICE nginx", result)

    @patch.object(da, "_run")
    def test_load_shown(self, mock_run):
        mock_run.return_value = {
            "success": True,
            "stdout": (
                "@@DISK@@\n 30%\n"
                "@@MEM@@\n40\n"
                "@@LOAD@@\n2.15\n"
                "@@NGINX@@\nactive\n"
                "@@MYSQL@@\nactive\n"
                "@@FPM@@\nphp8.2-fpm\n"
            ),
        }
        result = da.health_live()
        self.assertIn("load: 2.15", result)
        self.assertIn("php-fpm: php8.2-fpm", result)

    @patch.object(da, "_run")
    def test_unknown_service_not_anomaly(self, mock_run):
        mock_run.return_value = {
            "success": True,
            "stdout": (
                "@@DISK@@\n 30%\n"
                "@@MEM@@\n40\n"
                "@@LOAD@@\n0.1\n"
                "@@NGINX@@\nunknown\n"
                "@@MYSQL@@\nunknown\n"
                "@@FPM@@\nnone\n"
            ),
        }
        result = da.health_live()
        self.assertIn("status: OK", result)


class TestWatchdogThresholds(unittest.TestCase):

    def test_thresholds_exist(self):
        self.assertIn("disk_pct", da._WATCHDOG_THRESHOLDS)
        self.assertIn("memory_pct", da._WATCHDOG_THRESHOLDS)

    def test_disk_threshold_reasonable(self):
        self.assertGreaterEqual(da._WATCHDOG_THRESHOLDS["disk_pct"], 70)
        self.assertLessEqual(da._WATCHDOG_THRESHOLDS["disk_pct"], 95)

    def test_memory_threshold_reasonable(self):
        self.assertGreaterEqual(da._WATCHDOG_THRESHOLDS["memory_pct"], 70)
        self.assertLessEqual(da._WATCHDOG_THRESHOLDS["memory_pct"], 95)


# ===========================================================================
# 4.1 Integration: drift detection di _preflight_deploy
# ===========================================================================
class TestPreflightDrift(MemoryTestBase):

    @patch.object(da, "_run")
    def test_preflight_includes_drift_when_present(self, mock_run):
        prev = {"git_hash": "old123", "captured_at": da._now_iso()}
        da._save_deploy_fingerprint(prev)

        call_count = {"n": 0}

        def side_effect(cmd, cwd=None, timeout=10, **kw):
            call_count["n"] += 1
            if "df " in cmd:
                return {"success": True, "stdout": "50%\n"}
            if "git status --porcelain" in cmd:
                return {"success": True, "stdout": "0\n"}
            if "git log --oneline" in cmd:
                return {"success": True, "stdout": "new456 latest\n"}
            if "phpversion" in cmd:
                return {"success": True, "stdout": "8.3.0"}
            if "rev-parse" in cmd:
                return {"success": True, "stdout": "new456\n"}
            return {"success": False, "stdout": ""}
        mock_run.side_effect = side_effect

        checks, blockers = da._preflight_deploy("/var/www/app")
        self.assertIn("drift", checks)
        self.assertTrue(checks["drift"]["has_drift"])

    @patch.object(da, "_run")
    def test_preflight_no_drift_key_when_no_fingerprint(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "50%\n"}
        checks, _ = da._preflight_deploy("/var/www/app")
        self.assertNotIn("drift", checks)


if __name__ == "__main__":
    unittest.main()
