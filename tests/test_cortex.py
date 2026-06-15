"""Test Cortex — global consciousness layer: storage, events, digest, routing."""
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


class CortexBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cortex_dir = os.path.join(self.tmpdir, "_cortex")
        self.project_dir = os.path.join(self.tmpdir, "simuru")
        self._orig = {
            "MEMORY_DIR": da.MEMORY_DIR,
            "MEMORY_FILE": da.MEMORY_FILE,
            "GLOBAL_MEMORY_DIR": da.GLOBAL_MEMORY_DIR,
            "GLOBAL_MEMORY_FILE": da.GLOBAL_MEMORY_FILE,
            "GLOBAL_EVENTS_FILE": da.GLOBAL_EVENTS_FILE,
            "PROJECT_NAME": da.PROJECT_NAME,
        }
        da.MEMORY_DIR = self.project_dir
        da.MEMORY_FILE = os.path.join(self.project_dir, "memory.jsonl")
        da.GLOBAL_MEMORY_DIR = self.cortex_dir
        da.GLOBAL_MEMORY_FILE = os.path.join(self.cortex_dir, "memory.jsonl")
        da.GLOBAL_EVENTS_FILE = os.path.join(self.cortex_dir, "events.jsonl")
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

    def _write(self, ns, text, key="", **kw):
        return da.memory_write(ns, text, key, **kw)

    def _recall(self, **kw):
        return da.memory_recall(**kw)

    def _forget(self, **kw):
        return da.memory_forget(**kw)


# ===========================================================================
# Namespace routing
# ===========================================================================
class TestNamespaceRouting(CortexBase):
    def test_profile_writes_to_cortex(self):
        r = self._write("profile", "Syams admin", key="owner")
        self.assertTrue(r["success"])
        self.assertIn("cortex", r["scope"])
        self.assertTrue(os.path.exists(da.GLOBAL_MEMORY_FILE))
        cortex = da._cortex_fold()
        self.assertIn("profile:owner", cortex)

    def test_cross_writes_to_cortex(self):
        r = self._write("cross", "SIMURU API -> ERP frontend", key="dep-erp")
        self.assertTrue(r["success"])
        self.assertIn("cortex", r["scope"])
        cortex = da._cortex_fold()
        self.assertIn("cross:dep-erp", cortex)

    def test_server_writes_to_project(self):
        r = self._write("server", "php8.3-fpm", key="fpm")
        self.assertTrue(r["success"])
        self.assertIn("project", r["scope"])
        project = da._mem_fold()
        self.assertIn("server:fpm", project)

    def test_instruction_writes_to_project(self):
        r = self._write("instruction", "jangan edit kode di server", key="no-edit")
        self.assertTrue(r["success"])
        self.assertIn("project", r["scope"])

    def test_cortex_and_project_isolated(self):
        self._write("profile", "Syams", key="owner")
        self._write("server", "nginx 1.24", key="nginx")
        cortex = da._cortex_fold()
        project = da._mem_fold()
        self.assertIn("profile:owner", cortex)
        self.assertNotIn("server:nginx", cortex)
        self.assertIn("server:nginx", project)
        self.assertNotIn("profile:owner", project)


# ===========================================================================
# Cortex storage layer
# ===========================================================================
class TestCortexStorage(CortexBase):
    def test_append_creates_dir(self):
        self.assertFalse(os.path.exists(self.cortex_dir))
        da._cortex_append({"id": "test:x", "ns": "cross", "text": "hi"})
        self.assertTrue(os.path.exists(da.GLOBAL_MEMORY_FILE))

    def test_fold_returns_appended(self):
        da._cortex_append({"id": "cross:a", "ns": "cross", "text": "fact A", "deleted": False})
        result = da._cortex_fold()
        self.assertIn("cross:a", result)

    def test_fold_cache(self):
        da._cortex_append({"id": "cross:a", "ns": "cross", "text": "fact A", "deleted": False})
        f1 = da._cortex_fold()
        f2 = da._cortex_fold()
        self.assertIs(f1, f2)

    def test_compact(self):
        for i in range(5):
            da._cortex_append({"id": "cross:item", "ns": "cross", "text": f"v{i}", "deleted": False})
        live = da._cortex_fold()
        self.assertEqual(len(live), 1)
        da._cortex_compact(live)
        with open(da.GLOBAL_MEMORY_FILE) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)


# ===========================================================================
# Event journal
# ===========================================================================
class TestEventJournal(CortexBase):
    def test_event_append_and_read(self):
        da._event_append("simuru", "deploy", "branch main")
        da._event_append("erp", "service_restart", "nginx")
        events = da._events_read(hours=1)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["project"], "simuru")
        self.assertEqual(events[1]["project"], "erp")

    def test_events_exclude_project(self):
        da._event_append("simuru", "deploy", "branch main")
        da._event_append("erp", "restart", "nginx")
        events = da._events_read(hours=1, exclude_project="simuru")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["project"], "erp")

    def test_events_filter_by_project(self):
        da._event_append("simuru", "deploy", "ok")
        da._event_append("erp", "restart", "nginx")
        events = da._events_read(hours=1, project_filter="erp")
        self.assertEqual(len(events), 1)

    def test_events_age_filter(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(microsecond=0).isoformat()
        da._cortex_ensure_store()
        line = json.dumps({"ts": old_ts, "project": "old", "event": "x", "detail": "", "severity": "info"}) + "\n"
        with open(da.GLOBAL_EVENTS_FILE, "w") as f:
            f.write(line)
        da._event_append("new", "deploy", "recent")
        events = da._events_read(hours=24)
        projects = [e["project"] for e in events]
        self.assertNotIn("old", projects)
        self.assertIn("new", projects)

    def test_empty_project_skips(self):
        with patch.object(da, "PROJECT_NAME", ""):
            da._event_append("", "deploy", "x")
        self.assertFalse(os.path.exists(da.GLOBAL_EVENTS_FILE))


# ===========================================================================
# cortex_log and cortex_events tools
# ===========================================================================
class TestCortexTools(CortexBase):
    def test_cortex_log(self):
        r = da.cortex_log("deploy", "branch hotfix deployed", "info")
        self.assertTrue(r["success"])
        self.assertEqual(r["project"], "simuru")
        events = da._events_read(hours=1)
        self.assertEqual(len(events), 1)

    def test_cortex_events_tool(self):
        da._event_append("erp", "restart", "nginx")
        da._event_append("simuru", "deploy", "ok")
        r = da.cortex_events(hours=1)
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 2)

    def test_cortex_events_filter(self):
        da._event_append("erp", "restart", "nginx")
        da._event_append("simuru", "deploy", "ok")
        r = da.cortex_events(hours=1, project="erp")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["events"][0]["project"], "erp")


# ===========================================================================
# Recall searches both stores
# ===========================================================================
class TestRecallMerge(CortexBase):
    def test_recall_all_merges(self):
        self._write("profile", "Syams admin server", key="owner")
        self._write("server", "php8.3-fpm service", key="fpm")
        r = self._recall()
        self.assertEqual(r["count"], 2)

    def test_recall_ns_filter_cortex(self):
        self._write("profile", "Syams", key="owner")
        self._write("server", "nginx", key="nginx")
        r = self._recall(ns="profile")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["entries"][0]["ns"], "profile")

    def test_recall_ns_filter_project(self):
        self._write("profile", "Syams", key="owner")
        self._write("server", "nginx", key="nginx")
        r = self._recall(ns="server")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["entries"][0]["ns"], "server")

    def test_recall_semantic_across_stores(self):
        self._write("cross", "deploy SIMURU selalu backup database dulu", key="deploy-rule")
        self._write("server", "mysql 8.0 berjalan di port 3306 backup /var/backups", key="mysql")
        r = self._recall(query="backup database")
        self.assertGreater(r["count"], 0)


# ===========================================================================
# Forget routes correctly
# ===========================================================================
class TestForgetRouting(CortexBase):
    def test_forget_cortex_entry(self):
        self._write("profile", "Syams", key="owner")
        r = self._forget(ns="profile", key="owner")
        self.assertTrue(r["success"])
        self.assertTrue(r["existed"])
        cortex = da._cortex_fold()
        self.assertNotIn("profile:owner", cortex)

    def test_forget_project_entry(self):
        self._write("server", "nginx", key="nginx")
        r = self._forget(ns="server", key="nginx")
        self.assertTrue(r["success"])
        project = da._mem_fold()
        self.assertNotIn("server:nginx", project)

    def test_forget_by_id_auto_routes(self):
        self._write("cross", "shared info", key="shared")
        r = self._forget(id="cross:shared")
        self.assertTrue(r["success"])
        self.assertTrue(r["existed"])


# ===========================================================================
# Digest includes cortex + project + events
# ===========================================================================
class TestCortexDigest(CortexBase):
    def test_digest_has_cortex_header(self):
        self._write("profile", "Syams sysadmin", key="owner")
        digest = da._build_memory_digest()
        self.assertIn("ODIN CORTEX", digest)
        self.assertIn("PROFIL OPERATOR", digest)
        self.assertIn("Syams sysadmin", digest)

    def test_digest_has_project_header(self):
        self._write("instruction", "jangan edit kode server", key="no-edit")
        digest = da._build_memory_digest()
        self.assertIn("MEMORY PROJECT", digest)
        self.assertIn("simuru", digest)

    def test_digest_has_events(self):
        da._event_append("erp", "deploy", "branch main deployed")
        digest = da._build_memory_digest()
        self.assertIn("AKTIVITAS PROJECT LAIN", digest)
        self.assertIn("erp", digest)
        self.assertIn("deploy", digest)

    def test_digest_excludes_own_events(self):
        da._event_append("simuru", "deploy", "self deploy")
        da._event_append("erp", "restart", "nginx")
        digest = da._build_memory_digest()
        self.assertIn("erp", digest)

    def test_digest_shows_project_goal(self):
        self._write("instruction", "SIMURU: Sistem Informasi Penerimaan Murid Baru berbasis web", key="project-goal", pinned=True)
        self._write("instruction", "jangan edit kode di server", key="no-edit")
        digest = da._build_memory_digest()
        self.assertIn("TUJUAN PROJECT", digest)
        self.assertIn("Sistem Informasi Penerimaan Murid", digest)
        goal_pos = digest.index("TUJUAN PROJECT")
        instr_pos = digest.index("INSTRUKSI DURABLE")
        self.assertLess(goal_pos, instr_pos)

    def test_digest_no_goal_no_section(self):
        self._write("instruction", "aturan biasa", key="rule1")
        digest = da._build_memory_digest()
        self.assertNotIn("TUJUAN PROJECT", digest)
        self.assertIn("INSTRUKSI DURABLE", digest)

    def test_digest_combined(self):
        self._write("profile", "Syams", key="owner")
        self._write("cross", "SIMURU API dikonsumsi ERP", key="dep")
        self._write("instruction", "backup sebelum deploy", key="backup")
        self._write("server", "php8.3-fpm", key="fpm", pinned=True)
        da._event_append("erp", "restart", "mysql restarted")
        digest = da._build_memory_digest()
        self.assertIn("CORTEX", digest)
        self.assertIn("Syams", digest)
        self.assertIn("SIMURU API", digest)
        self.assertIn("AKTIVITAS", digest)
        self.assertIn("erp", digest)
        self.assertIn("PROJECT", digest)
        self.assertIn("backup", digest)
        self.assertIn("php8.3-fpm", digest)

    def test_empty_memory_returns_empty(self):
        self.assertEqual(da._build_memory_digest(), "")


# ===========================================================================
# memory_health includes cortex
# ===========================================================================
class TestHealthWithCortex(CortexBase):
    def test_health_counts_both(self):
        self._write("profile", "Syams", key="owner")
        self._write("cross", "shared fact", key="shared")
        self._write("server", "nginx", key="nginx")
        r = da.memory_health()
        self.assertTrue(r["success"])
        self.assertEqual(r["cortex_active"], 2)
        self.assertEqual(r["project_active"], 1)
        self.assertEqual(r["total_active"], 3)
        self.assertEqual(r["by_namespace"]["profile"], 1)
        self.assertEqual(r["by_namespace"]["cross"], 1)
        self.assertEqual(r["by_namespace"]["server"], 1)

    def test_health_events_count(self):
        da._event_append("erp", "deploy", "ok")
        da._event_append("simuru", "restart", "nginx")
        r = da.memory_health()
        self.assertEqual(r["events_last_24h"], 2)


# ===========================================================================
# memory_digest includes cortex
# ===========================================================================
class TestDigestTool(CortexBase):
    def test_digest_tool_counts(self):
        self._write("profile", "Syams", key="owner")
        self._write("server", "nginx", key="nginx")
        r = da.memory_digest()
        self.assertTrue(r["success"])
        self.assertEqual(r["cortex_active"], 1)
        self.assertEqual(r["project_active"], 1)
        self.assertEqual(r["total_active"], 2)


# ===========================================================================
# Backward compat: old profile in project file still readable
# ===========================================================================
class TestBackwardCompat(CortexBase):
    def test_old_profile_in_project_readable(self):
        da._mem_append({
            "id": "profile:owner", "ns": "profile", "key": "owner",
            "text": "Syams legacy", "deleted": False,
        })
        r = self._recall(ns="profile")
        self.assertGreater(r["count"], 0)

    def test_new_namespace_cross_valid(self):
        r = self._write("cross", "shared fact", key="test")
        self.assertTrue(r["success"])

    def test_invalid_ns_rejected(self):
        r = self._write("invalid_ns", "text")
        self.assertFalse(r["success"])


# ===========================================================================
# resource routes cortex ns
# ===========================================================================
class TestResourceRouting(CortexBase):
    def test_resource_cross(self):
        self._write("cross", "shared infra fact", key="infra")
        result = da.memory_resource("cross")
        self.assertIn("shared infra fact", result)

    def test_resource_server(self):
        self._write("server", "nginx 1.24", key="nginx")
        result = da.memory_resource("server")
        self.assertIn("nginx 1.24", result)


if __name__ == "__main__":
    unittest.main()
