"""Test Fase 3 — Workflow Intelligence: runbook engine + rollback tracking."""
import sys, types, os, json, importlib.util, unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# ---------------------------------------------------------------------------
# Mock MCP agar deploy_agent.py bisa diimpor tanpa dependensi mcp[cli]
# ---------------------------------------------------------------------------
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

spec = importlib.util.spec_from_file_location("deploy_agent", ROOT / "server" / "deploy_agent.py")
da = importlib.util.module_from_spec(spec)
spec.loader.exec_module(da)

guard_spec = importlib.util.spec_from_file_location("guard", ROOT / "client" / "deploy_agent_guard.py")
guard = importlib.util.module_from_spec(guard_spec)
guard_spec.loader.exec_module(guard)


# ===========================================================================
# ROLLBACK TRACKING — _capture_pre_state / _suggest_rollback
# ===========================================================================
class TestCapturePreState(unittest.TestCase):

    @patch.object(da, "_run")
    def test_git_reset_captures_head(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "abc1234\n", "stderr": "", "exit_code": 0}
        state = da._capture_pre_state("git reset --hard origin/main", "/var/www/app")
        self.assertEqual(state["git_head"], "abc1234")
        mock_run.assert_called_once()
        self.assertIn("rev-parse HEAD", mock_run.call_args[0][0])

    @patch.object(da, "_run")
    def test_git_pull_captures_head(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "def5678\n", "stderr": "", "exit_code": 0}
        state = da._capture_pre_state("git pull origin main", "/var/www/app")
        self.assertEqual(state["git_head"], "def5678")

    @patch.object(da, "_run")
    def test_artisan_migrate_captures_status(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "Ran  2024_01_01\n", "stderr": "", "exit_code": 0}
        state = da._capture_pre_state("php artisan migrate --force", "/var/www/app")
        self.assertIn("migrate_tail", state)

    @patch.object(da, "_run")
    def test_systemctl_restart_captures_status(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "active\n", "stderr": "", "exit_code": 0}
        state = da._capture_pre_state("sudo -n systemctl restart nginx", None)
        self.assertEqual(state["svc_was"], "nginx=active")

    def test_readonly_command_no_capture(self):
        state = da._capture_pre_state("cat /etc/nginx/nginx.conf", "/tmp")
        self.assertEqual(state, {})

    def test_git_status_no_capture(self):
        state = da._capture_pre_state("git status", "/var/www/app")
        self.assertEqual(state, {})

    def test_no_cwd_skips_git(self):
        state = da._capture_pre_state("git reset --hard HEAD", None)
        self.assertEqual(state, {})


class TestSuggestRollback(unittest.TestCase):

    def test_git_head_rollback(self):
        rb = da._suggest_rollback("git reset --hard origin/main", {"git_head": "abc123"})
        self.assertEqual(len(rb), 1)
        self.assertIn("abc123", rb[0])
        self.assertIn("git reset --hard", rb[0])

    def test_migrate_rollback(self):
        rb = da._suggest_rollback("php artisan migrate --force",
                                   {"migrate_tail": "Ran 2024_01_01"})
        self.assertTrue(any("migrate:rollback" in r for r in rb))

    def test_service_rollback(self):
        rb = da._suggest_rollback("sudo -n systemctl restart php8.3-fpm",
                                   {"svc_was": "php8.3-fpm=active"})
        self.assertTrue(any("systemctl restart" in r for r in rb))

    def test_composer_rollback(self):
        rb = da._suggest_rollback("composer install --no-dev", {})
        self.assertTrue(any("composer install" in r for r in rb))

    def test_rm_warns_no_undo(self):
        rb = da._suggest_rollback("rm -rf /tmp/old-build", {})
        self.assertTrue(any("tidak bisa di-undo" in r for r in rb))

    def test_empty_pre_state(self):
        rb = da._suggest_rollback("ls -la", {})
        self.assertEqual(rb, [])

    def test_combined_state(self):
        pre = {"git_head": "abc", "migrate_tail": "Ran stuff"}
        rb = da._suggest_rollback("git reset --hard && php artisan migrate --force", pre)
        self.assertGreaterEqual(len(rb), 2)


# ===========================================================================
# SESSION LOG — rollback fields
# ===========================================================================
class TestSessionLogRollback(unittest.TestCase):

    def setUp(self):
        da._SESSION_LOG.clear()

    def test_session_log_stores_pre_state(self):
        da._session_log("run_command", "git reset", {"success": True},
                        pre_state={"git_head": "abc"}, rollback=["git reset --hard abc"])
        entry = da._SESSION_LOG[-1]
        self.assertEqual(entry["_pre_state"]["git_head"], "abc")
        self.assertEqual(entry["_rollback"], ["git reset --hard abc"])

    def test_session_log_omits_empty_fields(self):
        da._session_log("run_command", "ls", {"success": True})
        entry = da._SESSION_LOG[-1]
        self.assertNotIn("_pre_state", entry)
        self.assertNotIn("_rollback", entry)


# ===========================================================================
# RUNBOOK TOOL
# ===========================================================================
class TestRunbook(unittest.TestCase):

    def setUp(self):
        da._SESSION_LOG.clear()

    def test_empty_steps_rejected(self):
        result = da.runbook("empty", [], "")
        self.assertFalse(result["success"])
        self.assertIn("kosong", result["error"])

    def test_too_many_steps_rejected(self):
        big = [{"label": f"s{i}", "command": "echo hi"} for i in range(21)]
        result = da.runbook("big", big, "")
        self.assertFalse(result["success"])
        self.assertIn("20", result["error"])

    @patch.object(da, "_run")
    def test_successful_runbook(self, mock_run):
        mock_run.side_effect = lambda *a, **kw: {"success": True, "stdout": "ok\n", "stderr": "",
                                                  "exit_code": 0, "duration_sec": 0.1}
        steps = [
            {"label": "check-disk", "command": "df -h /"},
            {"label": "list-files", "command": "ls -la"},
        ]
        result = da.runbook("test-run", steps, "/tmp")
        self.assertTrue(result["success"])
        self.assertEqual(result["name"], "test-run")
        self.assertEqual(result["executed"], 2)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["failed_steps"], [])
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["step"], "check-disk")

    @patch.object(da, "_run")
    def test_runbook_stops_on_failure(self, mock_run):
        mock_run.side_effect = [
            {"success": True, "stdout": "", "stderr": "", "exit_code": 0, "duration_sec": 0.1},
            {"success": False, "stdout": "", "stderr": "error", "exit_code": 1, "duration_sec": 0.1},
            {"success": True, "stdout": "", "stderr": "", "exit_code": 0, "duration_sec": 0.1},
        ]
        steps = [
            {"label": "ok-step", "command": "echo ok"},
            {"label": "fail-step", "command": "false"},
            {"label": "skip-step", "command": "echo skipped"},
        ]
        result = da.runbook("halt-test", steps, "")
        self.assertFalse(result["success"])
        self.assertEqual(result["executed"], 2)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed_steps"], ["fail-step"])

    @patch.object(da, "_run")
    def test_runbook_continue_on_fail(self, mock_run):
        mock_run.side_effect = [
            {"success": False, "stdout": "", "stderr": "warn", "exit_code": 1, "duration_sec": 0.1},
            {"success": True, "stdout": "done\n", "stderr": "", "exit_code": 0, "duration_sec": 0.1},
        ]
        steps = [
            {"label": "soft-fail", "command": "echo warn", "continue_on_fail": True},
            {"label": "must-run", "command": "echo done"},
        ]
        result = da.runbook("cont-test", steps, "")
        self.assertEqual(result["executed"], 2)
        self.assertEqual(len(result["steps"]), 2)

    @patch.object(da, "_run")
    def test_runbook_empty_command_skipped(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "", "stderr": "",
                                 "exit_code": 0, "duration_sec": 0.1}
        steps = [
            {"label": "empty", "command": ""},
        ]
        result = da.runbook("empty-cmd", steps, "")
        self.assertFalse(result["success"])
        self.assertEqual(result["failed_steps"], ["empty"])

    @patch.object(da, "_run")
    def test_runbook_with_rollback_tracking(self, mock_run):
        mock_run.side_effect = [
            # _capture_pre_state: git rev-parse HEAD
            {"success": True, "stdout": "abc123\n", "stderr": "", "exit_code": 0},
            # actual command: git reset
            {"success": True, "stdout": "HEAD is now at abc123\n", "stderr": "",
             "exit_code": 0, "duration_sec": 0.5},
        ]
        steps = [{"label": "git-reset", "command": "git reset --hard origin/main"}]
        result = da.runbook("deploy-git", steps, "/var/www/app")
        step = result["steps"][0]
        self.assertIn("_rollback_hint", step)
        self.assertTrue(any("abc123" in r for r in step["_rollback_hint"]))

    @patch.object(da, "_run")
    def test_runbook_audited_and_logged(self, mock_run):
        da._SESSION_LOG.clear()
        mock_run.return_value = {"success": True, "stdout": "", "stderr": "",
                                 "exit_code": 0, "duration_sec": 0.1}
        da.runbook("audit-test", [{"label": "echo", "command": "echo hi"}], "")
        self.assertTrue(any(e["tool"] == "runbook" for e in da._SESSION_LOG))

    @patch.object(da, "_run")
    def test_runbook_custom_timeout(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "", "stderr": "",
                                 "exit_code": 0, "duration_sec": 0.1}
        steps = [{"label": "slow", "command": "sleep 1", "timeout": 600}]
        da.runbook("timeout-test", steps, "")
        call_args = mock_run.call_args
        self.assertEqual(call_args[0][2], 600)

    def test_runbook_default_label(self):
        with patch.object(da, "_run") as mock_run:
            mock_run.return_value = {"success": True, "stdout": "", "stderr": "",
                                     "exit_code": 0, "duration_sec": 0.1}
            steps = [{"command": "echo hi"}]
            result = da.runbook("no-label", steps, "")
            self.assertEqual(result["steps"][0]["step"], "step-1")


# ===========================================================================
# ROLLBACK PLAN TOOL
# ===========================================================================
class TestRollbackPlan(unittest.TestCase):

    def setUp(self):
        da._SESSION_LOG.clear()

    def test_empty_session(self):
        result = da.rollback_plan()
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)

    def test_returns_only_rollback_entries(self):
        da._session_log("run_command", "ls", {"success": True})
        da._session_log("run_command", "git reset --hard", {"success": True},
                        pre_state={"git_head": "abc"}, rollback=["git reset --hard abc"])
        da._session_log("run_command", "cat file", {"success": True})
        result = da.rollback_plan()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["operations"][0]["summary"], "git reset --hard")

    def test_respects_last_param(self):
        for i in range(10):
            da._session_log("run_command", f"op-{i}", {"success": True},
                            pre_state={"git_head": f"h{i}"}, rollback=[f"undo-{i}"])
        result = da.rollback_plan(last=3)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["operations"][0]["summary"], "op-9")

    def test_includes_note(self):
        result = da.rollback_plan()
        self.assertIn("SARAN", result["note"])


# ===========================================================================
# INTEGRATION — run_command with rollback tracking
# ===========================================================================
class TestRunCommandRollback(unittest.TestCase):

    def setUp(self):
        da._SESSION_LOG.clear()

    @patch.object(da, "_run")
    def test_run_command_captures_rollback(self, mock_run):
        mock_run.side_effect = [
            {"success": True, "stdout": "abc123\n", "stderr": "", "exit_code": 0},
            {"success": True, "stdout": "reset done\n", "stderr": "",
             "exit_code": 0, "duration_sec": 0.2},
        ]
        result = da.run_command("git reset --hard origin/main", "/var/www/app")
        self.assertIn("_rollback_hint", result)
        self.assertTrue(any("abc123" in r for r in result["_rollback_hint"]))

    @patch.object(da, "_run")
    def test_run_command_no_rollback_for_read(self, mock_run):
        mock_run.return_value = {"success": True, "stdout": "stuff\n", "stderr": "",
                                 "exit_code": 0, "duration_sec": 0.1}
        result = da.run_command("git status", "/var/www/app")
        self.assertNotIn("_rollback_hint", result)


# ===========================================================================
# GUARD — runbook + rollback_plan handlers
# ===========================================================================
class TestGuardRunbook(unittest.TestCase):

    def test_runbook_handler_asks(self):
        with self.assertRaises(SystemExit) as ctx:
            data = {
                "tool_name": "mcp__odin__runbook",
                "tool_input": {
                    "name": "ssl-renew",
                    "steps": [
                        {"label": "certbot", "command": "sudo -n certbot renew"},
                        {"label": "nginx-reload", "command": "sudo -n systemctl reload nginx"},
                    ]
                }
            }
            with patch("sys.stdin", MagicMock()):
                with patch("json.load", return_value=data):
                    with patch("sys.stdout") as mock_stdout:
                        guard.main()

    def test_rollback_plan_auto_allows(self):
        data = {
            "tool_name": "mcp__odin__rollback_plan",
            "tool_input": {"last": 5}
        }
        with self.assertRaises(SystemExit):
            with patch("json.load", return_value=data):
                with patch("sys.stdout"):
                    guard.main()


# ===========================================================================
# GUARD — assess_command dipakai oleh runbook handler
# ===========================================================================
class TestGuardRunbookRiskTier(unittest.TestCase):

    def test_readonly_runbook_auto_allows(self):
        cls = guard.classify_command("df -h /")
        self.assertEqual(cls, "allow")

    def test_assess_write_step(self):
        tier, _, _, _ = guard.assess_command("sudo -n systemctl restart nginx")
        self.assertIn(tier, ("SEDANG", "TINGGI"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
