"""Test Memory Improvements — staleness, digest budget, similarity detection,
semantic search (TF-IDF), memory_health tool."""
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


class MemBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = {
            "MEMORY_DIR": da.MEMORY_DIR, "MEMORY_FILE": da.MEMORY_FILE,
            "GLOBAL_MEMORY_DIR": da.GLOBAL_MEMORY_DIR,
            "GLOBAL_MEMORY_FILE": da.GLOBAL_MEMORY_FILE,
            "GLOBAL_EVENTS_FILE": da.GLOBAL_EVENTS_FILE,
        }
        da.MEMORY_DIR = self.tmpdir
        da.MEMORY_FILE = os.path.join(self.tmpdir, "memory.jsonl")
        cortex_dir = os.path.join(self.tmpdir, "_cortex")
        da.GLOBAL_MEMORY_DIR = cortex_dir
        da.GLOBAL_MEMORY_FILE = os.path.join(cortex_dir, "memory.jsonl")
        da.GLOBAL_EVENTS_FILE = os.path.join(cortex_dir, "events.jsonl")
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

    def _health(self):
        return da.memory_health()


# ===========================================================================
# _tokenize
# ===========================================================================
class TestTokenize(unittest.TestCase):
    def test_basic(self):
        tokens = da._tokenize("PHP 8.3 FPM service restart")
        self.assertIn("php", tokens)
        self.assertIn("8.3", tokens)
        self.assertIn("fpm", tokens)
        self.assertIn("service", tokens)
        self.assertIn("restart", tokens)

    def test_stop_words_filtered(self):
        tokens = da._tokenize("ini adalah sebuah test untuk dari yang")
        self.assertNotIn("ini", tokens)
        self.assertNotIn("adalah", tokens)
        self.assertNotIn("untuk", tokens)
        self.assertNotIn("dari", tokens)
        self.assertNotIn("yang", tokens)
        self.assertIn("test", tokens)

    def test_preserves_versions(self):
        tokens = da._tokenize("php8.3-fpm nginx mysql5.7")
        self.assertTrue(any("php8.3-fpm" in t for t in tokens))

    def test_empty(self):
        self.assertEqual(da._tokenize(""), [])


# ===========================================================================
# TF-IDF scoring
# ===========================================================================
class TestTfIdf(unittest.TestCase):
    def test_exact_match_high_score(self):
        q = da._tokenize("php fpm restart")
        d = da._tokenize("restart php fpm service setelah deploy")
        idf = da._compute_idf([q, d])
        score = da._tfidf_score(q, d, idf)
        self.assertGreater(score, 0)

    def test_no_match_zero(self):
        q = da._tokenize("nginx config error")
        d = da._tokenize("mysql backup dump restore")
        idf = da._compute_idf([q, d])
        score = da._tfidf_score(q, d, idf)
        self.assertEqual(score, 0.0)

    def test_partial_match(self):
        q = da._tokenize("mysql backup")
        d1 = da._tokenize("mysql backup dump setiap malam ke /var/backups")
        d2 = da._tokenize("nginx config reload setelah ssl renew")
        idf = da._compute_idf([q, d1, d2])
        s1 = da._tfidf_score(q, d1, idf)
        s2 = da._tfidf_score(q, d2, idf)
        self.assertGreater(s1, s2)

    def test_empty_query(self):
        score = da._tfidf_score([], ["php", "fpm"], {})
        self.assertEqual(score, 0.0)

    def test_empty_doc(self):
        score = da._tfidf_score(["php"], [], {})
        self.assertEqual(score, 0.0)

    def test_idf_empty_corpus(self):
        idf = da._compute_idf([])
        self.assertEqual(idf, {})


# ===========================================================================
# _word_overlap
# ===========================================================================
class TestWordOverlap(unittest.TestCase):
    def test_identical(self):
        self.assertAlmostEqual(da._word_overlap("php fpm service", "php fpm service"), 1.0)

    def test_partial(self):
        overlap = da._word_overlap("php fpm service restart", "php fpm config nginx")
        self.assertGreater(overlap, 0.0)
        self.assertLess(overlap, 1.0)

    def test_no_overlap(self):
        self.assertEqual(da._word_overlap("mysql backup", "nginx ssl certbot"), 0.0)

    def test_empty(self):
        self.assertEqual(da._word_overlap("", "something"), 0.0)
        self.assertEqual(da._word_overlap("something", ""), 0.0)


# ===========================================================================
# _is_stale
# ===========================================================================
class TestIsStale(unittest.TestCase):
    def test_recent_not_stale(self):
        now = datetime.now(timezone.utc)
        rec = {"updated_at": now.isoformat()}
        self.assertFalse(da._is_stale(rec, now))

    def test_old_is_stale(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=60)).isoformat()
        rec = {"updated_at": old}
        self.assertTrue(da._is_stale(rec, now))

    def test_fallback_to_created_at(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=60)).isoformat()
        rec = {"created_at": old}
        self.assertTrue(da._is_stale(rec, now))

    def test_no_dates_not_stale(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(da._is_stale({}, now))

    def test_custom_stale_days(self):
        now = datetime.now(timezone.utc)
        rec = {"updated_at": (now - timedelta(days=15)).isoformat()}
        with patch.object(da, "STALE_DAYS", 10):
            self.assertTrue(da._is_stale(rec, now))
        with patch.object(da, "STALE_DAYS", 30):
            self.assertFalse(da._is_stale(rec, now))


# ===========================================================================
# Staleness in digest
# ===========================================================================
class TestDigestStaleness(MemBase):
    def test_stale_marker_in_digest(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).replace(microsecond=0).isoformat()
        da._mem_append({
            "id": "instruction:old-rule", "ns": "instruction", "key": "old-rule",
            "text": "selalu backup sebelum deploy", "tags": [],
            "created_at": old_ts, "updated_at": old_ts,
            "pinned": False, "deleted": False,
        })
        digest = da._build_memory_digest()
        self.assertIn("[STALE?]", digest)
        self.assertIn("selalu backup", digest)

    def test_fresh_no_stale_marker(self):
        now = da._now_iso()
        da._mem_append({
            "id": "instruction:new-rule", "ns": "instruction", "key": "new-rule",
            "text": "jangan edit kode di server", "tags": [],
            "created_at": now, "updated_at": now,
            "pinned": False, "deleted": False,
        })
        digest = da._build_memory_digest()
        self.assertNotIn("[STALE?]", digest)
        self.assertIn("jangan edit", digest)


# ===========================================================================
# Digest budget cap
# ===========================================================================
class TestDigestBudget(MemBase):
    def test_budget_truncates(self):
        for i in range(30):
            da._mem_append({
                "id": f"instruction:rule-{i}", "ns": "instruction",
                "key": f"rule-{i}",
                "text": f"Instruksi penting nomor {i} yang cukup panjang untuk memakan budget " * 3,
                "tags": [], "created_at": da._now_iso(), "updated_at": da._now_iso(),
                "pinned": False, "deleted": False,
            })
        with patch.object(da, "DIGEST_BUDGET", 1000):
            digest = da._build_memory_digest()
        self.assertLessEqual(len(digest), 1200)
        self.assertIn("entry lagi tidak ditampilkan", digest)

    def test_budget_no_truncation(self):
        da._mem_append({
            "id": "profile:owner", "ns": "profile", "key": "owner",
            "text": "Syams", "tags": [],
            "created_at": da._now_iso(), "updated_at": da._now_iso(),
            "pinned": False, "deleted": False,
        })
        with patch.object(da, "DIGEST_BUDGET", 3000):
            digest = da._build_memory_digest()
        self.assertNotIn("entry lagi", digest)


# ===========================================================================
# Similarity detection in memory_write
# ===========================================================================
class TestSimilarityDetection(MemBase):
    def test_similar_entry_detected(self):
        self._write("server", "php fpm service php8.3-fpm restart reload config", key="fpm-service", pinned=True)
        result = self._write("server", "php fpm service php8.3-fpm konfigurasi restart", key="php-fpm")
        self.assertIn("_similar_existing", result)
        self.assertIn("_hint", result)
        self.assertTrue(len(result["_similar_existing"]) > 0)

    def test_no_similar_when_different(self):
        self._write("server", "nginx versi 1.24 terinstall", key="nginx-version", pinned=True)
        result = self._write("server", "mysql 8.0 berjalan di port 3306", key="mysql-version")
        self.assertNotIn("_similar_existing", result)

    def test_upsert_same_key_no_false_positive(self):
        self._write("server", "php fpm = php8.3-fpm", key="fpm-service")
        result = self._write("server", "php fpm = php8.4-fpm (updated)", key="fpm-service")
        self.assertNotIn("_similar_existing", result)

    def test_cross_namespace_no_detection(self):
        self._write("server", "deploy harus backup dulu", key="deploy-rule")
        result = self._write("instruction", "deploy harus backup dulu", key="deploy-rule")
        self.assertNotIn("_similar_existing", result)


# ===========================================================================
# updated_at field
# ===========================================================================
class TestUpdatedAt(MemBase):
    def test_write_includes_updated_at(self):
        result = self._write("server", "test entry", key="test")
        self.assertIn("updated_at", result["entry"])
        self.assertEqual(result["entry"]["created_at"], result["entry"]["updated_at"])


# ===========================================================================
# Semantic search in memory_recall
# ===========================================================================
class TestSemanticSearch(MemBase):
    def setUp(self):
        super().setUp()
        self._write("server", "php-fpm service adalah php8.3-fpm, restart via systemctl", key="fpm")
        self._write("server", "nginx reverse proxy ke port 8080, config di /etc/nginx", key="nginx")
        self._write("server", "mysql 8.0 berjalan di port 3306, backup ke /var/backups", key="mysql")
        self._write("instruction", "selalu backup database sebelum deploy", key="backup-rule")
        self._write("instruction", "jangan edit kode langsung di server", key="no-edit")

    def test_semantic_ranking(self):
        result = self._recall(query="backup database")
        self.assertTrue(result["success"])
        self.assertGreater(result["count"], 0)
        self.assertEqual(result["_search_mode"], "semantic (TF-IDF)")
        texts = [e.get("text", "") for e in result["entries"]]
        backup_idx = next((i for i, t in enumerate(texts) if "backup" in t.lower()), 999)
        nginx_idx = next((i for i, t in enumerate(texts) if "nginx" in t.lower()), 999)
        self.assertLess(backup_idx, nginx_idx)

    def test_substring_fallback(self):
        result = self._recall(query="php8.3")
        self.assertTrue(result["success"])
        self.assertGreater(result["count"], 0)
        self.assertTrue(any("php8.3" in e.get("text", "") for e in result["entries"]))

    def test_no_query_returns_all(self):
        result = self._recall()
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 5)
        self.assertNotIn("_search_mode", result)

    def test_ns_filter_with_query(self):
        result = self._recall(ns="server", query="backup")
        self.assertTrue(result["success"])
        for e in result["entries"]:
            self.assertEqual(e["ns"], "server")

    def test_tag_filter_with_query(self):
        self._write("server", "redis cache di port 6379", key="redis", tags=["cache"])
        result = self._recall(tag="cache", query="redis")
        self.assertTrue(result["success"])
        for e in result["entries"]:
            self.assertIn("cache", [str(t).lower() for t in (e.get("tags") or [])])

    def test_no_results_for_unrelated(self):
        result = self._recall(query="kubernetes docker swarm")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)


# ===========================================================================
# memory_health tool
# ===========================================================================
class TestMemoryHealth(MemBase):

    def test_empty_memory(self):
        result = self._health()
        self.assertTrue(result["success"])
        self.assertEqual(result["total_active"], 0)
        self.assertEqual(result["stale_count"], 0)

    def test_counts_per_namespace(self):
        self._write("server", "fact 1", key="f1")
        self._write("server", "fact 2", key="f2")
        self._write("instruction", "rule 1", key="r1")
        result = self._health()
        self.assertEqual(result["by_namespace"]["server"], 2)
        self.assertEqual(result["by_namespace"]["instruction"], 1)
        self.assertEqual(result["by_namespace"]["profile"], 0)

    def test_stale_detection(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).replace(microsecond=0).isoformat()
        da._mem_append({
            "id": "server:old-fact", "ns": "server", "key": "old-fact",
            "text": "old fact here", "tags": [],
            "created_at": old_ts, "updated_at": old_ts,
            "pinned": False, "deleted": False,
        })
        result = self._health()
        self.assertEqual(result["stale_count"], 1)
        self.assertEqual(result["stale_entries"][0]["id"], "server:old-fact")
        self.assertGreaterEqual(result["stale_entries"][0]["age_days"], 59)

    def test_duplicate_detection(self):
        self._write("server", "php fpm service php8.3-fpm restart reload", key="fpm-service")
        self._write("server", "php fpm service php8.3-fpm konfigurasi reload", key="php-fpm-info")
        result = self._health()
        self.assertGreater(len(result["potential_duplicates"]), 0)

    def test_file_size(self):
        self._write("server", "some fact", key="f1")
        result = self._health()
        self.assertGreater(result["project_file_size"], 0)

    def test_no_false_duplicates_across_ns(self):
        self._write("server", "backup database sebelum deploy", key="backup")
        self._write("instruction", "backup database sebelum deploy", key="backup")
        result = self._health()
        self.assertEqual(len(result["potential_duplicates"]), 0)


# ===========================================================================
# _find_similar edge cases
# ===========================================================================
class TestFindSimilar(MemBase):
    def test_empty_memory(self):
        result = da._find_similar("server", "server:test", "test", "some text")
        self.assertEqual(result, [])

    def test_threshold(self):
        self._write("server", "alpha beta gamma delta epsilon", key="entry1")
        result = da._find_similar("server", "server:entry2", "entry2",
                                  "alpha beta gamma delta zeta", threshold=0.8)
        self.assertEqual(result, [])
        result = da._find_similar("server", "server:entry2", "entry2",
                                  "alpha beta gamma delta zeta", threshold=0.3)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
