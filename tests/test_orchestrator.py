"""Test Orchestrator — _enrich_context, _suggest_next, _check_attention, _orchestrate."""
import sys, types, os, json, tempfile, unittest
from datetime import datetime, timedelta, timezone
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


class OrchestratorBase(unittest.TestCase):
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

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(da, k, v)
        da._fold_invalidate()
        da._cortex_fold_invalidate()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ===========================================================================
# _enrich_context
# ===========================================================================
class TestEnrichContext(OrchestratorBase):
    def test_recalls_deploy_instructions(self):
        da.memory_write("instruction", "selalu backup database sebelum deploy", key="backup-rule")
        ctx = da._enrich_context("laravel_deploy", {"branch": "main"})
        self.assertGreater(len(ctx), 0)
        texts = " ".join(c["text"] for c in ctx)
        self.assertIn("backup", texts.lower())

    def test_recalls_cross_project_for_service(self):
        da.memory_write("cross", "restart nginx berdampak ke semua project di vps-app", key="shared-nginx")
        ctx = da._enrich_context("service_action", {"service": "nginx", "action": "restart"})
        self.assertGreater(len(ctx), 0)
        texts = " ".join(c["text"] for c in ctx)
        self.assertIn("nginx", texts.lower())

    def test_recalls_for_command(self):
        da.memory_write("instruction", "jangan rm -rf di production tanpa backup", key="rm-warning")
        ctx = da._enrich_context("run_command", {"command": "rm -rf /tmp/cache"})
        self.assertGreater(len(ctx), 0)

    def test_no_context_for_unrelated(self):
        da.memory_write("instruction", "deploy harus backup dulu", key="deploy-rule")
        ctx = da._enrich_context("run_command", {"command": "ls -la"})
        self.assertEqual(len(ctx), 0)

    def test_empty_memory(self):
        ctx = da._enrich_context("laravel_deploy", {"branch": "main"})
        self.assertEqual(ctx, [])

    def test_max_three_results(self):
        for i in range(10):
            da.memory_write("instruction", f"deploy rule {i} backup migration artisan", key=f"rule-{i}")
        ctx = da._enrich_context("laravel_deploy", {"branch": "main"})
        self.assertLessEqual(len(ctx), 3)


# ===========================================================================
# _suggest_next
# ===========================================================================
class TestSuggestNext(unittest.TestCase):
    def test_after_deploy_success(self):
        result = {"success": True}
        suggestions = da._suggest_next("laravel_deploy", {"branch": "main"}, result)
        self.assertGreater(len(suggestions), 0)
        tools = [s["tool"] for s in suggestions]
        self.assertIn("http_health_check", tools)

    def test_after_deploy_fail(self):
        result = {"success": False}
        suggestions = da._suggest_next("laravel_deploy", {"branch": "main"}, result)
        tools = [s["tool"] for s in suggestions]
        self.assertIn("rollback_plan", tools)
        self.assertIn("tail_log", tools)

    def test_after_service_restart(self):
        result = {"success": True}
        suggestions = da._suggest_next("service_action", {"service": "nginx", "action": "restart"}, result)
        tools = [s["tool"] for s in suggestions]
        self.assertIn("http_health_check", tools)

    def test_after_health_check_fail(self):
        result = {"success": False}
        suggestions = da._suggest_next("http_health_check", {"url": "http://example.com"}, result)
        tools = [s["tool"] for s in suggestions]
        self.assertIn("tail_log", tools)

    def test_after_db_error(self):
        result = {"success": False, "_analysis": {"error_type": "db_conn"}}
        suggestions = da._suggest_next("run_command", {"command": "php artisan migrate"}, result)
        tools = [s["tool"] for s in suggestions]
        self.assertIn("service_action", tools)

    def test_after_permission_error(self):
        result = {"success": False, "_analysis": {"error_type": "permission_denied"}}
        suggestions = da._suggest_next("run_command", {"command": "cat /etc/shadow"}, result)
        self.assertGreater(len(suggestions), 0)

    def test_no_suggestion_for_normal(self):
        result = {"success": True}
        suggestions = da._suggest_next("run_command", {"command": "ls"}, result)
        self.assertEqual(suggestions, [])

    def test_max_three_suggestions(self):
        result = {"success": False, "_analysis": {"error_type": "db_sql_permission_disk_nginx"}}
        suggestions = da._suggest_next("run_command", {"command": "x"}, result)
        self.assertLessEqual(len(suggestions), 3)


# ===========================================================================
# _check_attention
# ===========================================================================
class TestCheckAttention(OrchestratorBase):
    def test_recurring_error_flagged(self):
        result = {"_analysis": {"error_type": "db_conn", "recurring": True, "recurring_hint": "x"}}
        flags = da._check_attention("run_command", {}, result)
        self.assertGreater(len(flags), 0)
        self.assertEqual(flags[0]["level"], "warn")
        self.assertIn("BERULANG", flags[0]["msg"])

    def test_cross_project_warn_events(self):
        da._event_append("erp", "health_fail", "HTTP 502", severity="error")
        flags = da._check_attention("run_command", {}, {"success": True})
        event_flags = [f for f in flags if f["level"] == "info"]
        self.assertGreater(len(event_flags), 0)
        self.assertIn("erp", event_flags[0]["msg"])

    def test_no_attention_normal(self):
        flags = da._check_attention("run_command", {}, {"success": True})
        self.assertEqual(flags, [])


# ===========================================================================
# _orchestrate (integration)
# ===========================================================================
class TestOrchestrate(OrchestratorBase):
    def test_enriches_result(self):
        da.memory_write("instruction", "backup database sebelum deploy wajib", key="backup-rule")
        result = {"success": True}
        da._orchestrate("laravel_deploy", {"branch": "main"}, result)
        self.assertIn("_memory_context", result)
        self.assertIn("_suggested_next", result)

    def test_no_crash_on_empty(self):
        result = {"success": True}
        da._orchestrate("run_command", {"command": "ls"}, result)

    def test_suggest_after_health_fail(self):
        result = {"success": False, "http_status": 502}
        da._orchestrate("http_health_check", {"url": "http://x.com"}, result)
        self.assertIn("_suggested_next", result)
        tools = [s["tool"] for s in result["_suggested_next"]]
        self.assertIn("tail_log", tools)

    def test_attention_for_recurring(self):
        result = {"success": False, "_analysis": {"error_type": "db_conn", "recurring": True}}
        da._orchestrate("run_command", {"command": "php artisan migrate"}, result)
        self.assertIn("_attention", result)

    def test_no_keys_when_nothing(self):
        result = {"success": True}
        da._orchestrate("run_command", {"command": "echo hi"}, result)
        self.assertNotIn("_memory_context", result)
        self.assertNotIn("_suggested_next", result)
        self.assertNotIn("_attention", result)


if __name__ == "__main__":
    unittest.main()
