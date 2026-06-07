"""Test Fase 3 — UX & Tooling: audit_tail, risk card undo, runbook templates."""
import sys, types, os, json, tempfile, unittest
from pathlib import Path
from datetime import datetime, timezone

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
# 3.1 Audit Tail Tool
# ===========================================================================
class AuditTestBase(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_dir = da.MEMORY_DIR
        self._orig_file = da.AUDIT_FILE
        da.MEMORY_DIR = self.tmpdir
        da.AUDIT_FILE = os.path.join(self.tmpdir, "audit.jsonl")

    def tearDown(self):
        da.MEMORY_DIR = self._orig_dir
        da.AUDIT_FILE = self._orig_file
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_audit(self, entries):
        os.makedirs(da.MEMORY_DIR, mode=0o700, exist_ok=True)
        with open(da.AUDIT_FILE, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")


class TestAuditTail(AuditTestBase):

    def test_empty_file(self):
        r = da.audit_tail()
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 0)

    def test_returns_entries(self):
        self._write_audit([
            {"ts": "2026-06-07T10:00:00+00:00", "tool": "run_command",
             "summary": "ls", "success": True, "exit_code": 0},
            {"ts": "2026-06-07T10:01:00+00:00", "tool": "run_command",
             "summary": "rm file", "success": False, "exit_code": 1},
        ])
        r = da.audit_tail(last=10)
        self.assertEqual(r["count"], 2)

    def test_last_limit(self):
        entries = [{"ts": f"2026-06-07T10:0{i}:00+00:00", "tool": "run_command",
                    "summary": f"cmd{i}", "success": True} for i in range(5)]
        self._write_audit(entries)
        r = da.audit_tail(last=2)
        self.assertEqual(r["count"], 2)
        self.assertIn("cmd3", r["entries"][0]["summary"])

    def test_tool_filter(self):
        self._write_audit([
            {"ts": "2026-06-07T10:00:00+00:00", "tool": "run_command",
             "summary": "ls", "success": True},
            {"ts": "2026-06-07T10:01:00+00:00", "tool": "laravel_deploy",
             "summary": "deploy", "success": True},
            {"ts": "2026-06-07T10:02:00+00:00", "tool": "run_command",
             "summary": "cat", "success": True},
        ])
        r = da.audit_tail(tool_filter="laravel_deploy")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["entries"][0]["tool"], "laravel_deploy")

    def test_success_only(self):
        self._write_audit([
            {"ts": "2026-06-07T10:00:00+00:00", "tool": "run_command",
             "summary": "ok", "success": True},
            {"ts": "2026-06-07T10:01:00+00:00", "tool": "run_command",
             "summary": "fail", "success": False},
        ])
        r = da.audit_tail(success_only=True)
        self.assertEqual(r["count"], 1)
        self.assertTrue(r["entries"][0]["success"])

    def test_since_filter(self):
        self._write_audit([
            {"ts": "2026-06-06T10:00:00+00:00", "tool": "run_command",
             "summary": "old", "success": True},
            {"ts": "2026-06-07T10:00:00+00:00", "tool": "run_command",
             "summary": "new", "success": True},
        ])
        r = da.audit_tail(since="2026-06-07T00:00:00")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["entries"][0]["summary"], "new")

    def test_max_100(self):
        entries = [{"ts": f"2026-06-07T10:00:{i:02d}+00:00", "tool": "x",
                    "summary": f"c{i}", "success": True} for i in range(200)]
        self._write_audit(entries)
        r = da.audit_tail(last=200)
        self.assertLessEqual(r["count"], 100)

    def test_corrupt_line_skipped(self):
        os.makedirs(da.MEMORY_DIR, mode=0o700, exist_ok=True)
        with open(da.AUDIT_FILE, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"ts": "2026-06-07T10:00:00", "tool": "x",
                                "summary": "ok", "success": True}) + "\n")
        r = da.audit_tail()
        self.assertEqual(r["count"], 1)


# ===========================================================================
# 3.2 Risk Card — Undo Hint
# ===========================================================================
class TestUndoHint(unittest.TestCase):

    def test_git_reset_hard(self):
        hint = guard._undo_hint("git reset --hard origin/main")
        self.assertIn("reflog", hint)

    def test_git_clean(self):
        hint = guard._undo_hint("git clean -fd")
        self.assertIn("permanen", hint)

    def test_artisan_migrate(self):
        hint = guard._undo_hint("php artisan migrate --force")
        self.assertIn("rollback", hint)

    def test_systemctl_restart(self):
        hint = guard._undo_hint("systemctl restart nginx")
        self.assertIn("restart", hint)

    def test_systemctl_stop(self):
        hint = guard._undo_hint("systemctl stop mysql")
        self.assertIn("start", hint)

    def test_rm(self):
        hint = guard._undo_hint("rm -rf /var/www/old")
        self.assertIn("backup", hint)

    def test_mv(self):
        hint = guard._undo_hint("mv old.conf new.conf")
        self.assertIn("balik", hint)

    def test_composer_install(self):
        hint = guard._undo_hint("composer install --no-dev")
        self.assertIn("composer", hint)

    def test_no_hint_for_unknown(self):
        hint = guard._undo_hint("echo hello")
        self.assertEqual(hint, "")

    def test_no_hint_for_read(self):
        hint = guard._undo_hint("ls -la")
        self.assertEqual(hint, "")


class TestRiskCardWithUndo(unittest.TestCase):

    def test_card_contains_undo(self):
        card = guard.risk_card("git reset --hard HEAD")
        self.assertIn("Undo", card)
        self.assertIn("reflog", card)

    def test_card_no_undo_for_unknown(self):
        card = guard.risk_card("some_unknown_write_tool")
        self.assertNotIn("Undo", card)

    def test_card_undo_for_migrate(self):
        card = guard.risk_card("php artisan migrate --force")
        self.assertIn("Undo", card)
        self.assertIn("rollback", card)

    def test_card_undo_for_rm(self):
        card = guard.risk_card("rm -rf /tmp/old")
        self.assertIn("Undo", card)


# ===========================================================================
# 3.3 Runbook Templates
# ===========================================================================
class TestRunbookTemplatesBuiltin(unittest.TestCase):

    def test_list_all(self):
        r = da.runbook_templates()
        self.assertTrue(r["success"])
        self.assertIn("ssl-renew", r["templates"])
        self.assertIn("db-backup", r["templates"])
        self.assertIn("log-cleanup", r["templates"])
        self.assertIn("health-check", r["templates"])

    def test_get_specific(self):
        r = da.runbook_templates(name="ssl-renew")
        self.assertTrue(r["success"])
        self.assertEqual(r["name"], "ssl-renew")
        self.assertIn("steps", r)
        self.assertGreater(len(r["steps"]), 0)

    def test_get_nonexistent(self):
        r = da.runbook_templates(name="nonexistent")
        self.assertFalse(r["success"])
        self.assertIn("tidak ada", r["error"])

    def test_builtin_templates_have_description(self):
        r = da.runbook_templates()
        for name, desc in r["templates"].items():
            self.assertTrue(desc, f"template '{name}' has no description")

    def test_builtin_steps_have_label_and_command(self):
        for name in ("ssl-renew", "db-backup", "log-cleanup", "health-check"):
            r = da.runbook_templates(name=name)
            for step in r["steps"]:
                self.assertIn("label", step, f"{name} step missing label")
                self.assertIn("command", step, f"{name} step missing command")

    def test_list_separates_builtin_and_custom(self):
        r = da.runbook_templates()
        self.assertIn("builtin", r)
        self.assertIn("custom", r)
        self.assertIsInstance(r["builtin"], list)
        self.assertIsInstance(r["custom"], list)


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


class TestRunbookTemplatesCustom(MemoryTestBase):

    def test_custom_template_from_memory(self):
        tpl = {"description": "My custom workflow", "steps": [
            {"label": "check", "command": "echo ok", "timeout": 10}]}
        da._mem_append({
            "id": "server:runbook-my-workflow", "ns": "server",
            "key": "runbook-my-workflow", "text": json.dumps(tpl),
            "tags": ["runbook"], "created_at": da._now_iso(),
            "pinned": False, "deleted": False,
        })
        r = da.runbook_templates()
        self.assertIn("my-workflow", r["templates"])
        self.assertIn("my-workflow", r["custom"])

    def test_custom_template_get(self):
        tpl = {"description": "Test tpl", "steps": [
            {"label": "s1", "command": "ls", "timeout": 10}]}
        da._mem_append({
            "id": "server:runbook-test-tpl", "ns": "server",
            "key": "runbook-test-tpl", "text": json.dumps(tpl),
            "tags": [], "created_at": da._now_iso(),
            "pinned": False, "deleted": False,
        })
        r = da.runbook_templates(name="test-tpl")
        self.assertTrue(r["success"])
        self.assertEqual(len(r["steps"]), 1)

    def test_custom_overrides_builtin(self):
        tpl = {"description": "Override ssl-renew", "steps": [
            {"label": "custom-step", "command": "echo custom", "timeout": 10}]}
        da._mem_append({
            "id": "server:runbook-ssl-renew", "ns": "server",
            "key": "runbook-ssl-renew", "text": json.dumps(tpl),
            "tags": [], "created_at": da._now_iso(),
            "pinned": False, "deleted": False,
        })
        r = da.runbook_templates(name="ssl-renew")
        self.assertTrue(r["success"])
        self.assertEqual(r["steps"][0]["label"], "custom-step")

    def test_corrupt_custom_skipped(self):
        da._mem_append({
            "id": "server:runbook-bad", "ns": "server",
            "key": "runbook-bad", "text": "not json",
            "tags": [], "created_at": da._now_iso(),
            "pinned": False, "deleted": False,
        })
        r = da.runbook_templates()
        self.assertNotIn("bad", r["templates"])


if __name__ == "__main__":
    unittest.main()
