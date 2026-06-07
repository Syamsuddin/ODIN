"""Test Memory — _mem_append, _mem_fold, TTL, tombstone, compaction, secret detection,
_build_memory_digest, _slug, _validate_ns, _is_expired."""
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


class MemoryTestBase(unittest.TestCase):
    """Base class: buat tmpdir untuk isolasi memory tiap test."""

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

    def _now_iso(self):
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _record(self, rid="server:test", ns="server", key="test", text="hello",
                pinned=False, deleted=False, expires_at=None, tags=None):
        return {
            "id": rid, "ns": ns, "key": key, "text": text,
            "tags": tags or [], "source": "test",
            "created_at": self._now_iso(),
            "expires_at": expires_at, "pinned": pinned, "deleted": deleted,
        }


# ===========================================================================
# _slug
# ===========================================================================
class TestSlug(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(da._slug("FPM Service"), "fpm-service")

    def test_special_chars(self):
        self.assertEqual(da._slug("key!@#$%val"), "key-val")

    def test_empty(self):
        self.assertEqual(da._slug(""), "x")

    def test_long_truncated(self):
        result = da._slug("a" * 100)
        self.assertLessEqual(len(result), 60)


# ===========================================================================
# _validate_ns
# ===========================================================================
class TestValidateNs(unittest.TestCase):

    def test_valid(self):
        for ns in ("server", "instruction", "profile"):
            self.assertIsNone(da._validate_ns(ns))

    def test_invalid(self):
        self.assertIsNotNone(da._validate_ns("global"))
        self.assertIsNotNone(da._validate_ns(""))
        self.assertIsNotNone(da._validate_ns("user"))


# ===========================================================================
# _is_expired
# ===========================================================================
class TestIsExpired(unittest.TestCase):

    def test_no_expiry(self):
        self.assertFalse(da._is_expired({"text": "hi"}, datetime.now(timezone.utc)))

    def test_future_not_expired(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.assertFalse(da._is_expired({"expires_at": future}, datetime.now(timezone.utc)))

    def test_past_expired(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertTrue(da._is_expired({"expires_at": past}, datetime.now(timezone.utc)))

    def test_invalid_date(self):
        self.assertFalse(da._is_expired({"expires_at": "not-a-date"}, datetime.now(timezone.utc)))


# ===========================================================================
# _mem_append + _mem_fold
# ===========================================================================
class TestMemAppendFold(MemoryTestBase):

    def test_append_creates_file(self):
        rec = self._record()
        da._mem_append(rec)
        self.assertTrue(os.path.exists(da.MEMORY_FILE))

    def test_fold_returns_appended(self):
        rec = self._record()
        da._mem_append(rec)
        fold = da._mem_fold()
        self.assertIn("server:test", fold)
        self.assertEqual(fold["server:test"]["text"], "hello")

    def test_last_write_wins(self):
        da._mem_append(self._record(text="first"))
        da._mem_append(self._record(text="second"))
        fold = da._mem_fold()
        self.assertEqual(fold["server:test"]["text"], "second")

    def test_multiple_ids(self):
        da._mem_append(self._record(rid="server:a", key="a", text="aaa"))
        da._mem_append(self._record(rid="server:b", key="b", text="bbb"))
        fold = da._mem_fold()
        self.assertEqual(len(fold), 2)
        self.assertEqual(fold["server:a"]["text"], "aaa")
        self.assertEqual(fold["server:b"]["text"], "bbb")

    def test_empty_file(self):
        fold = da._mem_fold()
        self.assertEqual(fold, {})

    def test_corrupt_line_skipped(self):
        os.makedirs(da.MEMORY_DIR, mode=0o700, exist_ok=True)
        with open(da.MEMORY_FILE, "w") as f:
            f.write("not json\n")
            f.write(json.dumps(self._record()) + "\n")
        da._fold_invalidate()
        fold = da._mem_fold()
        self.assertEqual(len(fold), 1)


# ===========================================================================
# Tombstone (logical delete)
# ===========================================================================
class TestTombstone(MemoryTestBase):

    def test_tombstone_hides_record(self):
        da._mem_append(self._record())
        da._mem_append({"id": "server:test", "deleted": True, "created_at": self._now_iso()})
        fold = da._mem_fold()
        self.assertNotIn("server:test", fold)

    def test_tombstone_then_rewrite(self):
        da._mem_append(self._record(text="v1"))
        da._mem_append({"id": "server:test", "deleted": True, "created_at": self._now_iso()})
        da._mem_append(self._record(text="v2", deleted=False))
        fold = da._mem_fold()
        self.assertIn("server:test", fold)
        self.assertEqual(fold["server:test"]["text"], "v2")


# ===========================================================================
# TTL (expires_at)
# ===========================================================================
class TestTTL(MemoryTestBase):

    def test_expired_filtered_out(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        da._mem_append(self._record(expires_at=past))
        fold = da._mem_fold()
        self.assertNotIn("server:test", fold)

    def test_future_kept(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        da._mem_append(self._record(expires_at=future))
        fold = da._mem_fold()
        self.assertIn("server:test", fold)


# ===========================================================================
# Fold cache
# ===========================================================================
class TestFoldCache(MemoryTestBase):

    def test_cache_reused(self):
        da._mem_append(self._record())
        fold1 = da._mem_fold()
        fold2 = da._mem_fold()
        self.assertIs(fold1, fold2)

    def test_cache_invalidated_on_append(self):
        da._mem_append(self._record(rid="server:a", key="a", text="a"))
        fold1 = da._mem_fold()
        da._mem_append(self._record(rid="server:b", key="b", text="b"))
        fold2 = da._mem_fold()
        self.assertIsNot(fold1, fold2)
        self.assertEqual(len(fold2), 2)


# ===========================================================================
# Compaction
# ===========================================================================
class TestCompaction(MemoryTestBase):

    def test_compact_reduces_file(self):
        for i in range(10):
            da._mem_append(self._record(text=f"v{i}"))
        da._fold_invalidate()
        live = da._mem_fold()
        da._mem_compact(live)
        with open(da.MEMORY_FILE) as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), 1)

    def test_compact_preserves_data(self):
        da._mem_append(self._record(rid="server:a", key="a", text="alpha"))
        da._mem_append(self._record(rid="server:b", key="b", text="beta"))
        da._fold_invalidate()
        live = da._mem_fold()
        da._mem_compact(live)
        da._fold_invalidate()
        fold = da._mem_fold()
        self.assertEqual(fold["server:a"]["text"], "alpha")
        self.assertEqual(fold["server:b"]["text"], "beta")

    def test_compact_removes_tombstones(self):
        da._mem_append(self._record())
        da._mem_append({"id": "server:test", "deleted": True, "created_at": self._now_iso()})
        da._fold_invalidate()
        live = da._mem_fold()
        da._mem_compact(live)
        with open(da.MEMORY_FILE) as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), 0)


# ===========================================================================
# Secret detection
# ===========================================================================
class TestSecretDetection(unittest.TestCase):

    def test_password_detected(self):
        self.assertTrue(da._SECRET_RE.search("password: abc123"))

    def test_token_detected(self):
        self.assertTrue(da._SECRET_RE.search("token=mysecrettoken"))

    def test_api_key_detected(self):
        self.assertTrue(da._SECRET_RE.search("api_key: sk-1234567890"))

    def test_aws_key_detected(self):
        self.assertTrue(da._SECRET_RE.search("AKIAIOSFODNN7EXAMPLE"))

    def test_github_token_detected(self):
        self.assertTrue(da._SECRET_RE.search("ghp_ABCDEFGHIJKLMNOPQRSTuvwxyz"))

    def test_jwt_detected(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        self.assertTrue(da._SECRET_RE.search(jwt))

    def test_private_key_detected(self):
        self.assertTrue(da._SECRET_RE.search("-----BEGIN RSA PRIVATE KEY-----"))

    def test_normal_text_not_detected(self):
        self.assertIsNone(da._SECRET_RE.search("nginx versi 1.24.0"))
        self.assertIsNone(da._SECRET_RE.search("deploy ke branch main"))

    def test_env_var_name_not_value(self):
        self.assertIsNone(da._SECRET_RE.search("Cek variabel DB_PASSWORD di .env"))


# ===========================================================================
# memory_write tool — integration
# ===========================================================================
class TestMemoryWriteTool(MemoryTestBase):

    def test_write_success(self):
        r = da.memory_write(ns="server", text="nginx aktif", key="nginx")
        self.assertTrue(r["success"])
        self.assertIn("server:nginx", r["id"])

    def test_write_invalid_ns(self):
        r = da.memory_write(ns="invalid", text="test")
        self.assertFalse(r["success"])

    def test_write_empty_text(self):
        r = da.memory_write(ns="server", text="")
        self.assertFalse(r["success"])

    def test_write_too_long(self):
        r = da.memory_write(ns="server", text="x" * (da.MEMORY_MAX_TEXT + 1))
        self.assertFalse(r["success"])

    def test_write_secret_blocked(self):
        r = da.memory_write(ns="server", text="password: mysecret123")
        self.assertFalse(r["success"])
        self.assertTrue(r.get("blocked_secret"))

    def test_write_secret_allowed(self):
        r = da.memory_write(ns="server", text="password: mysecret123", allow_secret=True)
        self.assertTrue(r["success"])

    def test_upsert_by_key(self):
        da.memory_write(ns="server", text="v1", key="fpm")
        da.memory_write(ns="server", text="v2", key="fpm")
        fold = da._mem_fold()
        self.assertEqual(fold["server:fpm"]["text"], "v2")

    def test_tags_stored(self):
        r = da.memory_write(ns="server", text="test", key="t", tags=["deploy", "nginx"])
        self.assertEqual(r["entry"]["tags"], ["deploy", "nginx"])

    def test_pinned_stored(self):
        r = da.memory_write(ns="server", text="test", key="p", pinned=True)
        self.assertTrue(r["entry"]["pinned"])

    def test_expires_in_days(self):
        r = da.memory_write(ns="server", text="temp", key="tmp", expires_in_days=7)
        self.assertIsNotNone(r["entry"]["expires_at"])


# ===========================================================================
# memory_recall tool
# ===========================================================================
class TestMemoryRecallTool(MemoryTestBase):

    def setUp(self):
        super().setUp()
        da.memory_write(ns="server", text="nginx aktif", key="nginx", tags=["web"])
        da.memory_write(ns="server", text="mysql 8.0", key="mysql", tags=["db"])
        da.memory_write(ns="instruction", text="selalu backup", key="backup")

    def test_recall_all(self):
        r = da.memory_recall()
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 3)

    def test_recall_by_ns(self):
        r = da.memory_recall(ns="server")
        self.assertEqual(r["count"], 2)

    def test_recall_by_query(self):
        r = da.memory_recall(query="nginx")
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["entries"][0]["key"], "nginx")

    def test_recall_by_tag(self):
        r = da.memory_recall(tag="db")
        self.assertEqual(r["count"], 1)

    def test_recall_invalid_ns(self):
        r = da.memory_recall(ns="invalid")
        self.assertFalse(r["success"])


# ===========================================================================
# memory_forget tool
# ===========================================================================
class TestMemoryForgetTool(MemoryTestBase):

    def test_forget_by_id(self):
        da.memory_write(ns="server", text="test", key="nginx")
        r = da.memory_forget(id="server:nginx")
        self.assertTrue(r["success"])
        self.assertTrue(r["existed"])
        fold = da._mem_fold()
        self.assertNotIn("server:nginx", fold)

    def test_forget_by_ns_key(self):
        da.memory_write(ns="server", text="test", key="mysql")
        r = da.memory_forget(ns="server", key="mysql")
        self.assertTrue(r["success"])

    def test_forget_nonexistent(self):
        r = da.memory_forget(id="server:nonexistent")
        self.assertTrue(r["success"])
        self.assertFalse(r["existed"])

    def test_forget_no_args(self):
        r = da.memory_forget()
        self.assertFalse(r["success"])


# ===========================================================================
# _build_memory_digest
# ===========================================================================
class TestBuildMemoryDigest(MemoryTestBase):

    def test_empty_memory(self):
        self.assertEqual(da._build_memory_digest(), "")

    def test_digest_contains_profile(self):
        da.memory_write(ns="profile", text="Syams, admin", key="owner")
        digest = da._build_memory_digest()
        self.assertIn("PROFIL USER", digest)
        self.assertIn("Syams", digest)

    def test_digest_contains_instruction(self):
        da.memory_write(ns="instruction", text="selalu backup", key="backup")
        digest = da._build_memory_digest()
        self.assertIn("INSTRUKSI", digest)
        self.assertIn("backup", digest)

    def test_digest_server_only_pinned(self):
        da.memory_write(ns="server", text="not pinned", key="a", pinned=False)
        da.memory_write(ns="server", text="is pinned", key="b", pinned=True)
        digest = da._build_memory_digest()
        self.assertIn("is pinned", digest)
        self.assertNotIn("not pinned", digest)

    def test_digest_header(self):
        da.memory_write(ns="profile", text="test", key="t")
        digest = da._build_memory_digest()
        self.assertIn("MEMORY ODIN", digest)


if __name__ == "__main__":
    unittest.main()
