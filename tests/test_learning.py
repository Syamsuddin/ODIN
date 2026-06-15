"""Test Continuous Learning — error→lesson, cross-session errors, success patterns."""
import sys, types, os, json, tempfile, unittest
from datetime import datetime, timedelta, timezone
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


class LearningBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = {
            "MEMORY_DIR": da.MEMORY_DIR, "MEMORY_FILE": da.MEMORY_FILE,
            "GLOBAL_MEMORY_DIR": da.GLOBAL_MEMORY_DIR,
            "GLOBAL_MEMORY_FILE": da.GLOBAL_MEMORY_FILE,
            "GLOBAL_EVENTS_FILE": da.GLOBAL_EVENTS_FILE,
            "PROJECT_NAME": da.PROJECT_NAME,
        }
        da.MEMORY_DIR = self.tmpdir
        da.MEMORY_FILE = os.path.join(self.tmpdir, "memory.jsonl")
        cortex_dir = os.path.join(self.tmpdir, "_cortex")
        da.GLOBAL_MEMORY_DIR = cortex_dir
        da.GLOBAL_MEMORY_FILE = os.path.join(cortex_dir, "memory.jsonl")
        da.GLOBAL_EVENTS_FILE = os.path.join(cortex_dir, "events.jsonl")
        da.PROJECT_NAME = "simuru"
        da._fold_invalidate()
        da._cortex_fold_invalidate()
        da._error_counts.clear()
        da._error_counts_at_start.clear()
        da._learned_this_session.clear()
        da._error_freq_save_counter = 0

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(da, k, v)
        da._fold_invalidate()
        da._cortex_fold_invalidate()
        da._error_counts.clear()
        da._error_counts_at_start.clear()
        da._learned_this_session.clear()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# Loop 1: Error → Lesson
# ===========================================================================
class TestErrorLesson(LearningBase):
    def test_auto_learn_on_recurring(self):
        analysis = {
            "error_type": "db_conn",
            "recurring": True,
            "hints": ["MySQL connection refused, cek service"],
            "suggested_commands": [{"cmd": "systemctl status mysql", "risk": "AMAN"}],
        }
        result = da._learn_from_error(analysis, "php artisan migrate")
        self.assertIsNotNone(result)
        self.assertIn("db_conn", result)
        fold = da._mem_fold()
        self.assertIn("server:error-lesson-db-conn", fold)
        lesson = fold["server:error-lesson-db-conn"]
        self.assertIn("db_conn", lesson["text"])
        self.assertIn("MySQL", lesson["text"])
        self.assertIn("auto-learn", lesson["tags"])

    def test_learn_once_per_session(self):
        analysis = {"error_type": "db_conn", "recurring": True, "hints": ["x"]}
        r1 = da._learn_from_error(analysis, "cmd1")
        r2 = da._learn_from_error(analysis, "cmd2")
        self.assertIsNotNone(r1)
        self.assertIsNone(r2)

    def test_no_learn_without_recurring(self):
        analysis = {"error_type": "db_conn", "hints": ["x"]}
        result = da._learn_from_error(analysis, "cmd")
        self.assertIsNone(result)

    def test_learn_includes_command(self):
        analysis = {"error_type": "timeout", "recurring": True, "hints": ["timed out"]}
        da._learn_from_error(analysis, "long_running_query")
        fold = da._mem_fold()
        lesson = fold.get("server:error-lesson-timeout")
        self.assertIn("long_running_query", lesson["text"])

    def test_learn_includes_cross_session_count(self):
        da._error_counts = {"db_conn": 5}
        da._error_counts_at_start = {"db_conn": 2}
        analysis = {"error_type": "db_conn", "recurring": True, "hints": ["x"]}
        da._learn_from_error(analysis, "cmd")
        fold = da._mem_fold()
        self.assertIn("sesi sebelumnya", fold["server:error-lesson-db-conn"]["text"])


# ===========================================================================
# Loop 2: Cross-Session Error Tracking
# ===========================================================================
class TestCrossSessionErrors(LearningBase):
    def test_save_and_load_error_freq(self):
        da._error_counts = {"db_conn": 3, "timeout": 1}
        da._save_error_freq()
        da._error_counts.clear()
        da._error_counts_at_start.clear()
        da._fold_invalidate()
        da._load_error_freq()
        self.assertEqual(da._error_counts["db_conn"], 3)
        self.assertEqual(da._error_counts["timeout"], 1)
        self.assertEqual(da._error_counts_at_start["db_conn"], 3)

    def test_cross_session_recurring(self):
        da._error_counts = {"db_conn": 2}
        da._error_counts_at_start = {"db_conn": 2}
        da._save_error_freq()
        result = {"success": False, "exit_code": 1,
                  "stdout": "", "stderr": "SQLSTATE[HY000] [2002] Connection refused"}
        analysis = da._analyze_output(result)
        self.assertTrue(analysis.get("recurring"))
        self.assertTrue(analysis.get("cross_session"))
        self.assertIn("sesi sebelumnya", analysis.get("recurring_hint", ""))

    def test_empty_load(self):
        da._load_error_freq()
        self.assertEqual(da._error_counts, {})

    def test_save_periodic(self):
        da._error_counts = {"db_conn": 5}
        da._error_freq_save_counter = 4
        analysis = {"error_type": "db_conn"}
        result = {"_analysis": analysis}
        da._auto_learn("run_command", {"command": "x"}, result)
        fold = da._mem_fold()
        self.assertIn("server:error-freq", fold)

    def test_shutdown_saves_freq(self):
        da._error_counts = {"timeout": 2}
        da._shutdown_save()
        da._fold_invalidate()
        fold = da._mem_fold()
        self.assertIn("server:error-freq", fold)
        data = json.loads(fold["server:error-freq"]["text"])
        self.assertEqual(data["timeout"], 2)


# ===========================================================================
# Loop 3: Success → Pattern
# ===========================================================================
class TestSuccessPattern(LearningBase):
    def test_deploy_success_recorded(self):
        da._SESSION_LOG.clear()
        da._SESSION_LOG.extend([
            {"tool": "memory_recall", "summary": "recall deploy", "success": True},
            {"tool": "run_command", "summary": "mysqldump backup", "success": True},
            {"tool": "laravel_deploy", "summary": "deploy main", "success": True},
        ])
        result = da._learn_from_success("laravel_deploy", {"branch": "main"}, {"success": True})
        self.assertIsNotNone(result)
        self.assertIn("sukses", result)
        self.assertIn("backup", result.lower())
        fold = da._mem_fold()
        rec = fold.get("server:last-successful-deploy")
        self.assertIsNotNone(rec)
        self.assertIn("Backup dilakukan", rec["text"])
        self.assertIn("auto-learn", rec["tags"])

    def test_deploy_without_backup_noted(self):
        da._SESSION_LOG.clear()
        da._SESSION_LOG.extend([
            {"tool": "laravel_deploy", "summary": "deploy main", "success": True},
        ])
        result = da._learn_from_success("laravel_deploy", {"branch": "main"}, {"success": True})
        self.assertIsNotNone(result)
        fold = da._mem_fold()
        rec = fold["server:last-successful-deploy"]
        self.assertIn("TANPA backup", rec["text"])

    def test_deploy_fail_not_recorded(self):
        result = da._learn_from_success("laravel_deploy", {"branch": "main"}, {"success": False})
        self.assertIsNone(result)

    def test_runbook_success_recorded(self):
        result = da._learn_from_success(
            "runbook", {"name": "ssl-renew"},
            {"success": True, "executed": 3, "total": 3})
        self.assertIsNotNone(result)
        self.assertIn("ssl-renew", result)
        fold = da._mem_fold()
        self.assertTrue(any("success-runbook" in k for k in fold))

    def test_runbook_partial_not_recorded(self):
        result = da._learn_from_success(
            "runbook", {"name": "ssl-renew"},
            {"success": True, "executed": 2, "total": 3})
        self.assertIsNone(result)

    def test_other_tool_not_recorded(self):
        result = da._learn_from_success("run_command", {"command": "ls"}, {"success": True})
        self.assertIsNone(result)

    def test_success_has_expiry(self):
        da._SESSION_LOG.clear()
        da._learn_from_success("laravel_deploy", {"branch": "main"}, {"success": True})
        fold = da._mem_fold()
        rec = fold.get("server:last-successful-deploy")
        self.assertIsNotNone(rec.get("expires_at"))


# ===========================================================================
# _auto_learn integration
# ===========================================================================
class TestAutoLearn(LearningBase):
    def test_auto_learn_returns_lessons(self):
        analysis = {"error_type": "db_conn", "recurring": True, "hints": ["connection refused"]}
        result = {"success": False, "_analysis": analysis}
        learned = da._auto_learn("run_command", {"command": "migrate"}, result)
        self.assertGreater(len(learned), 0)
        self.assertIn("db_conn", learned[0])

    def test_auto_learn_returns_success_pattern(self):
        da._SESSION_LOG.clear()
        da._SESSION_LOG.append({"tool": "laravel_deploy", "summary": "deploy", "success": True})
        result = {"success": True}
        learned = da._auto_learn("laravel_deploy", {"branch": "main"}, result)
        self.assertGreater(len(learned), 0)

    def test_auto_learn_empty_for_normal(self):
        result = {"success": True}
        learned = da._auto_learn("run_command", {"command": "ls"}, result)
        self.assertEqual(learned, [])


# ===========================================================================
# _orchestrate integration with learning
# ===========================================================================
class TestOrchestrateWithLearning(LearningBase):
    def test_learned_in_result(self):
        da._error_counts = {"db_conn": 3}
        result = {
            "success": False,
            "_analysis": {"error_type": "db_conn", "recurring": True, "hints": ["x"]},
        }
        da._orchestrate("run_command", {"command": "test"}, result)
        self.assertIn("_learned", result)
        self.assertGreater(len(result["_learned"]), 0)

    def test_no_learned_key_when_nothing(self):
        result = {"success": True}
        da._orchestrate("run_command", {"command": "ls"}, result)
        self.assertNotIn("_learned", result)


# ===========================================================================
# _analyze_output cross-session awareness
# ===========================================================================
class TestAnalyzeOutputCrossSession(LearningBase):
    def test_cross_session_flag(self):
        da._error_counts = {"db_conn": 2}
        da._error_counts_at_start = {"db_conn": 2}
        result = {"success": False, "exit_code": 1,
                  "stdout": "", "stderr": "SQLSTATE[HY000] [2002] Connection refused"}
        analysis = da._analyze_output(result)
        self.assertTrue(analysis.get("cross_session"))

    def test_session_only_no_cross_flag(self):
        da._error_counts = {"db_conn": 2}
        da._error_counts_at_start = {}
        result = {"success": False, "exit_code": 1,
                  "stdout": "", "stderr": "SQLSTATE[HY000] [2002] Connection refused"}
        analysis = da._analyze_output(result)
        self.assertFalse(analysis.get("cross_session", False))


if __name__ == "__main__":
    unittest.main()
