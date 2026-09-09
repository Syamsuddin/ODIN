"""Test tiga inovasi v2.5: Gladi (rehearse), Serah-Terima (handover), Triase (triage).

Sifat yang dikunci di sini:
  GLADI       — tidak pernah menjalankan perintah aslinya, dan semua probe read-only.
  SERAH-TERIMA — nol subprocess di jalur startup (handshake MCP tak boleh tertahan).
  TRIASE      — hipotesis terurut menurut bukti, dan sapuannya read-only.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
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
_spec = importlib.util.spec_from_file_location("odin_agent", ROOT / "server" / "odin_agent.py")
da = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(da)

sys.path.insert(0, str(ROOT / "client"))
import odin_guard  # noqa: E402


class AgentBase(unittest.TestCase):
    """Arahkan seluruh state agent ke tmpdir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = {k: getattr(da, k) for k in (
            "MEMORY_DIR", "MEMORY_FILE", "AUDIT_FILE", "WATERMARK_FILE",
            "GLOBAL_MEMORY_DIR", "GLOBAL_MEMORY_FILE", "GLOBAL_EVENTS_FILE",
            "PROJECT_NAME", "PROJECT_ROOT")}
        da.MEMORY_DIR = os.path.join(self.tmp, "mem")
        da.MEMORY_FILE = os.path.join(da.MEMORY_DIR, "memory.jsonl")
        da.AUDIT_FILE = os.path.join(da.MEMORY_DIR, "audit.jsonl")
        da.WATERMARK_FILE = os.path.join(da.MEMORY_DIR, "last_seen")
        da.GLOBAL_MEMORY_DIR = os.path.join(self.tmp, "_cortex")
        da.GLOBAL_MEMORY_FILE = os.path.join(da.GLOBAL_MEMORY_DIR, "memory.jsonl")
        da.GLOBAL_EVENTS_FILE = os.path.join(da.GLOBAL_MEMORY_DIR, "events.jsonl")
        da.PROJECT_NAME = "demo"
        da.PROJECT_ROOT = os.path.join(self.tmp, "app")
        os.makedirs(da.MEMORY_DIR, exist_ok=True)
        os.makedirs(da.PROJECT_ROOT, exist_ok=True)
        da._fold_invalidate()
        da._cortex_fold_invalidate()

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(da, k, v)
        da._fold_invalidate()
        da._cortex_fold_invalidate()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spy_run(self, outputs=None):
        """Ganti _run: rekam tiap perintah, balas dari `outputs` (substring → teks)."""
        seen = []

        def fake(cmd, cwd=None, timeout=60, allow_dangerous=False):
            seen.append(cmd)
            text, rc = "", 0
            for needle, val in (outputs or {}).items():
                if needle in cmd:
                    text, rc = (val if isinstance(val, tuple) else (val, 0))
                    break
            return {"success": rc == 0, "exit_code": rc, "stdout": text,
                    "stderr": "" if rc == 0 else text, "command": cmd}

        return seen, patch.object(da, "_run", fake)


# ===========================================================================
# GLADI
# ===========================================================================
class TestRehearseNeverExecutes(AgentBase):
    """Sifat paling penting: gladi TIDAK BOLEH menjalankan perintah aslinya."""

    DESTRUCTIVE = [
        "rm -rf /var/www/lama",
        "git reset --hard origin/main",
        "php artisan migrate --force",
        "apt-get install -y nginx",
        "systemctl restart nginx",
        "composer install --no-dev",
        "npm ci",
        "certbot renew",
        'mysql -u root shopdb -e "DELETE FROM orders WHERE total < 10"',
    ]

    def test_original_command_is_never_run(self):
        for cmd in self.DESTRUCTIVE:
            seen, ctx = self.spy_run()
            with ctx:
                da._rehearse(cmd, self.tmp)
            for probe in seen:
                self.assertNotEqual(probe.strip(), cmd.strip(),
                                    f"gladi menjalankan perintah asli: {cmd}")

    def test_no_probe_is_destructive_by_guard(self):
        """Tiap probe harus lolos klasifikasi READ milik guard."""
        for cmd in self.DESTRUCTIVE:
            seen, ctx = self.spy_run()
            with ctx:
                da._rehearse(cmd, self.tmp)
            for probe in seen:
                self.assertEqual(
                    odin_guard.classify_command(probe), "allow",
                    f"probe gladi tidak read-only: {probe!r} (dari {cmd!r})")

    def test_rm_probe_only_inspects(self):
        seen, ctx = self.spy_run()
        with ctx:
            da._rehearse("rm -rf /var/www/lama", None)
        joined = " ".join(seen)
        self.assertIn("du -sh", joined)
        self.assertIn("find", joined)
        for probe in seen:
            self.assertFalse(probe.lstrip().startswith("rm "), probe)


class TestRehearseMatching(AgentBase):

    def test_each_pattern_is_recognised(self):
        cases = {
            "php artisan migrate --force": "migrate",
            "git reset --hard origin/main": "git-ref",
            "apt-get install -y nginx": "apt",
            "systemctl restart php8.3-fpm": "service",
            "rm -rf /tmp/x": "rm",
            "composer update": "composer",
            "npm ci": "npm",
            "pip install requests": "pip",
            "certbot renew": "certbot",
            "rsync -av /a/ /b/": "rsync",
            'mysql db -e "DELETE FROM t WHERE x=1"': "sql-write",
        }
        for cmd, kind in cases.items():
            spec, m = da._find_rehearsal(cmd)
            self.assertIsNotNone(spec, cmd)
            self.assertEqual(spec["name"], kind, cmd)

    def test_read_only_commands_have_no_rehearsal(self):
        for cmd in ("ls -la", "git status", "df -h", "php artisan migrate:status",
                    "certbot renew --dry-run", "rsync -n /a/ /b/"):
            spec, _ = da._find_rehearsal(cmd)
            self.assertIsNone(spec, f"{cmd} seharusnya tak punya gladi")

    def test_unknown_command_reports_unavailable(self):
        r = da._rehearse("echo halo", None)
        self.assertFalse(r["available"])
        self.assertIn("belum ada gladi", r["reason"])

    def test_git_probes_name_the_ref(self):
        seen, ctx = self.spy_run()
        with ctx:
            da._rehearse("git reset --hard origin/main", self.tmp)
        joined = " ".join(seen)
        self.assertIn("origin/main", joined)
        self.assertIn("git status --short", joined)   # perubahan lokal yang hilang

    def test_migrate_uses_pretend(self):
        seen, ctx = self.spy_run()
        with ctx:
            da._rehearse("php artisan migrate --force", self.tmp)
        self.assertIn("migrate --pretend", " ".join(seen))

    def test_apt_uses_simulate_and_drops_sudo(self):
        seen, ctx = self.spy_run()
        with ctx:
            da._rehearse("sudo apt-get install -y nginx", None)
        joined = " ".join(seen)
        self.assertIn("apt-get -s install", joined)
        self.assertNotIn("sudo", joined)


class TestRehearseSql(AgentBase):

    def test_delete_becomes_count_with_same_where(self):
        got = da._sql_to_count("DELETE FROM orders WHERE created_at < '2024-01-01'")
        self.assertIn("SELECT COUNT(*)", got)
        self.assertIn("FROM orders", got)
        self.assertIn("WHERE created_at < '2024-01-01'", got)

    def test_update_counts_rows_matching_where(self):
        got = da._sql_to_count("UPDATE users SET status='x' WHERE last_login < '2023-01-01'")
        self.assertIn("FROM users", got)
        self.assertIn("WHERE last_login < '2023-01-01'", got)
        self.assertNotIn("SET", got)

    def test_delete_without_where_still_counts(self):
        got = da._sql_to_count("DELETE FROM sessions")
        self.assertIn("FROM sessions", got)

    def test_select_has_no_count_rewrite(self):
        self.assertIsNone(da._sql_to_count("SELECT * FROM users"))

    def test_probe_keeps_connection_flags(self):
        cmd = 'mysql -u root -psecret shopdb -e "DELETE FROM orders WHERE total < 10"'
        spec, m = da._find_rehearsal(cmd)
        probes = da._probe_sql(m, cmd, None)
        self.assertTrue(probes)
        probe = probes[0][1]
        self.assertIn("-u root", probe)
        self.assertIn("shopdb", probe)
        self.assertIn("SELECT COUNT(*)", probe)
        self.assertNotIn("DELETE", probe)


class TestRehearseBlocking(AgentBase):

    def test_failed_config_test_blocks(self):
        """nginx -t merah = service tak akan naik lagi setelah restart."""
        seen, ctx = self.spy_run({"nginx -t": ("emerg: invalid directive", 1)})
        with ctx:
            r = da._rehearse("systemctl restart nginx", None)
        self.assertTrue(r["blocking"])
        self.assertIn("JANGAN lanjutkan", r["verdict"])

    def test_healthy_config_does_not_block(self):
        seen, ctx = self.spy_run({"nginx -t": "syntax is ok"})
        with ctx:
            r = da._rehearse("systemctl restart nginx", None)
        self.assertFalse(r["blocking"])

    def test_non_blocking_kind_stays_advisory(self):
        """Gagal pada gladi non-blocking bukan alasan membatalkan."""
        seen, ctx = self.spy_run({"du -sh": ("no such file", 1)})
        with ctx:
            r = da._rehearse("rm -rf /tidak/ada", None)
        self.assertFalse(r["blocking"])


class TestRunbookRehearsal(AgentBase):

    STEPS = [
        {"label": "backup", "command": "mysqldump db > /tmp/b.sql"},
        {"label": "restart", "command": "systemctl restart nginx"},
    ]

    def test_rehearsal_executes_no_step(self):
        seen, ctx = self.spy_run({"nginx -t": "syntax is ok"})
        with ctx:
            r = da.runbook("demo", self.STEPS, rehearse=True)
        self.assertTrue(r["rehearsal_only"])
        for probe in seen:
            self.assertNotIn("mysqldump", probe)
            self.assertNotIn("systemctl restart", probe)

    def test_blocking_step_is_reported(self):
        seen, ctx = self.spy_run({"nginx -t": ("emerg", 1)})
        with ctx:
            r = da.runbook("demo", self.STEPS, rehearse=True)
        self.assertFalse(r["success"])
        self.assertIn("restart", r["blockers"])

    def test_step_limit_respected(self):
        many = [{"label": f"s{i}", "command": "ls"} for i in range(21)]
        r = da.runbook("demo", many, rehearse=True)
        self.assertFalse(r["success"])
        self.assertIn("20 langkah", r["error"])


class TestGuardRehearsalHint(unittest.TestCase):

    def test_card_offers_rehearsal_for_risky_commands(self):
        card = odin_guard.risk_card("git reset --hard origin/main")
        self.assertIn("Gladi", card)

    def test_card_stays_quiet_when_nothing_to_rehearse(self):
        card = odin_guard.risk_card("echo halo")
        self.assertNotIn("Gladi", card)

    def test_guard_and_agent_patterns_agree(self):
        """_REHEARSABLE (guard) tak boleh menjanjikan gladi yang agent tak punya."""
        samples = ["php artisan migrate --force", "git reset --hard main",
                   "apt-get install -y x", "systemctl restart nginx", "rm -rf /tmp/x",
                   "composer install", "npm ci", "pip install x", "certbot renew",
                   "rsync -av /a/ /b/", 'mysql d -e "DELETE FROM t"']
        for cmd in samples:
            promised = bool(odin_guard._rehearsal_hint(cmd))
            available = da._find_rehearsal(cmd)[0] is not None
            self.assertEqual(promised, available,
                             f"guard dan agent tidak sepakat soal: {cmd}")


# ===========================================================================
# SERAH-TERIMA
# ===========================================================================
class TestHandover(AgentBase):

    def test_first_session_is_flagged(self):
        r = da._build_handover()
        self.assertTrue(r["first_session"])
        self.assertIn("Sesi pertama", r["lines"][0])

    def test_watermark_round_trip(self):
        da._write_watermark("2026-09-01T00:00:00+00:00")
        self.assertEqual(da._read_watermark(), "2026-09-01T00:00:00+00:00")

    def test_counts_deploys_services_and_failures(self):
        da._write_watermark("2026-09-01T00:00:00+00:00")
        da._audit("laravel_deploy", "branch=main", {"success": True})
        da._audit("service_action", "restart nginx", {"success": True})
        da._audit("service_action", "status nginx", {"success": True})
        da._audit("run_command", "artisan queue:work", {"success": False})
        r = da._build_handover()
        self.assertEqual(r["counts"]["deploys"], 1)
        self.assertEqual(r["counts"]["service_actions"], 1)   # 'status' tidak dihitung
        self.assertEqual(r["counts"]["failed_commands"], 1)
        blob = " ".join(r["lines"])
        self.assertIn("Deploy sukses", blob)
        self.assertIn("restart nginx", blob)

    def test_entries_before_watermark_are_excluded(self):
        da._audit("laravel_deploy", "deploy lama", {"success": True})
        da._write_watermark("2099-01-01T00:00:00+00:00")   # watermark di masa depan
        r = da._build_handover()
        self.assertEqual(r["counts"]["deploys"], 0)

    def test_startup_path_runs_no_subprocess(self):
        """Handshake MCP punya anggaran ~30 detik — startup tak boleh spawn apa pun."""
        da._write_watermark("2026-09-01T00:00:00+00:00")
        da._mem_append({"id": "server:deploy-fingerprint", "ns": "server",
                        "key": "deploy-fingerprint",
                        "text": json.dumps({"git_hash": "abc", "migration_count": 1}),
                        "tags": [], "created_at": da._now_iso(),
                        "pinned": True, "deleted": False})
        seen, ctx = self.spy_run()
        with ctx:
            da._build_handover(deep=False)
        self.assertEqual(seen, [], f"startup menjalankan subprocess: {seen}")

    def test_deep_mode_does_detect_drift(self):
        da._write_watermark("2026-09-01T00:00:00+00:00")
        da._mem_append({"id": "server:deploy-fingerprint", "ns": "server",
                        "key": "deploy-fingerprint",
                        "text": json.dumps({"git_hash": "abc", "migration_count": 1}),
                        "tags": [], "created_at": da._now_iso(),
                        "pinned": True, "deleted": False})
        seen, ctx = self.spy_run({"git rev-parse": "zzz9999"})
        with ctx:
            da._build_handover(deep=True)
        self.assertTrue(any("rev-parse" in c for c in seen))

    def test_text_is_empty_when_nothing_happened(self):
        da._write_watermark("2026-09-01T00:00:00+00:00")
        self.assertEqual(da._handover_text(da._build_handover()), "")

    def test_text_is_empty_on_first_session(self):
        self.assertEqual(da._handover_text(da._build_handover()), "")

    def test_text_renders_header_and_bullets(self):
        da._write_watermark("2026-09-01T00:00:00+00:00")
        da._audit("laravel_deploy", "branch=main", {"success": True})
        txt = da._handover_text(da._build_handover())
        self.assertIn("SERAH-TERIMA demo", txt)
        self.assertIn("•", txt)

    def test_gap_is_human_readable(self):
        self.assertIn("hari", da._humanize_gap("2026-09-01T00:00:00+00:00"))
        self.assertEqual(da._humanize_gap("bukan-tanggal"), "?")

    def test_missing_audit_file_is_not_fatal(self):
        da.AUDIT_FILE = os.path.join(self.tmp, "tidak-ada.jsonl")
        da._write_watermark("2026-09-01T00:00:00+00:00")
        r = da._build_handover()
        self.assertEqual(r["counts"]["audit_rows"], 0)


# ===========================================================================
# TRIASE
# ===========================================================================
class TestTriageMapping(unittest.TestCase):

    def test_known_symptoms_route_correctly(self):
        cases = {
            "502 bad gateway": "gateway-error",
            "situs tidak bisa diakses": "gateway-error",
            "disk penuh": "disk-penuh",
            "mysql tidak bisa connect": "database-down",
            "SQLSTATE error": "database-down",
            "situs lambat sejak deploy": "lambat",
            "sertifikat expired": "ssl",
            "deploy gagal": "deploy-gagal",
            "nginx mati": "service-mati",
            "kehabisan memori": "memory",
        }
        for symptom, kind in cases.items():
            self.assertEqual(da._match_symptom(symptom)["name"], kind, symptom)

    def test_unknown_symptom_falls_back_to_generic(self):
        self.assertEqual(da._match_symptom("warna tombol aneh")["name"], "umum")

    def test_every_triage_probe_is_read_only(self):
        """Sapuan triase harus auto-allow — kalau tidak, ia minta konfirmasi
        justru saat operator sedang panik."""
        specs = da._TRIAGE_MAP + [da._TRIAGE_GENERIC]
        for spec in specs:
            for label, cmd, timeout in spec["probes"]:
                self.assertEqual(
                    odin_guard.classify_command(cmd), "allow",
                    f"probe triase '{spec['name']}' tidak read-only: {cmd!r}")

    def test_every_hypothesis_has_signals_and_action(self):
        for spec in da._TRIAGE_MAP:
            for h in spec["hypotheses"]:
                self.assertTrue(h["signals"], f"{spec['name']}: {h['title']}")
                self.assertTrue(h["action"].strip(), f"{spec['name']}: {h['title']}")

    def test_probe_timeouts_keep_triage_fast(self):
        """Triase dipakai saat insiden — totalnya harus tetap pendek."""
        for spec in da._TRIAGE_MAP + [da._TRIAGE_GENERIC]:
            total = sum(t for _, _, t in spec["probes"])
            self.assertLessEqual(total, 180, f"{spec['name']} terlalu lambat: {total}s")


class TestTriageRanking(unittest.TestCase):

    def setUp(self):
        self.gw = [s for s in da._TRIAGE_MAP if s["name"] == "gateway-error"][0]

    def test_strongest_hypothesis_comes_first(self):
        blob = ("socket tidak ada php-fpm tidak aktif connect() failed "
                "no such file or directory upstream")
        ranked = da._rank_hypotheses(self.gw, blob, [])
        self.assertIn("php-fpm", ranked[0]["title"])
        self.assertEqual(ranked[0]["strength"], "kuat")

    def test_scores_are_monotonically_decreasing(self):
        blob = "oom-kill out of memory socket tidak ada upstream timed out 100%"
        ranked = da._rank_hypotheses(self.gw, blob, [])
        scores = [h["score"] for h in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_hypotheses_without_evidence_are_dropped(self):
        blob = "socket tidak ada"
        titles = [h["title"] for h in da._rank_hypotheses(self.gw, blob, [])]
        self.assertFalse(any("Disk penuh" in t for t in titles))

    def test_no_evidence_gives_honest_answer(self):
        ranked = da._rank_hypotheses(self.gw, "semuanya normal", [])
        self.assertEqual(len(ranked), 1)
        self.assertIn("Tak ada pola dikenali", ranked[0]["title"])
        self.assertEqual(ranked[0]["strength"], "tidak diketahui")

    def test_at_most_five_hypotheses(self):
        blob = " ".join(sig for h in self.gw["hypotheses"] for sig, _ in h["signals"])
        self.assertLessEqual(len(da._rank_hypotheses(self.gw, blob, [])), 5)


class TestTriageTool(AgentBase):

    def test_empty_symptom_is_rejected(self):
        r = da.triage("")
        self.assertFalse(r["success"])

    def test_runs_probes_and_never_acts(self):
        seen, ctx = self.spy_run({"df -h": "/dev/sda1  50G  49G  0  100% /"})
        with ctx:
            r = da.triage("disk penuh")
        self.assertTrue(r["success"])
        self.assertEqual(r["matched"], "disk-penuh")
        self.assertTrue(r["evidence"])
        # next_action hanya SARAN — tak boleh ikut dijalankan
        self.assertNotIn(r["next_action"], seen)
        self.assertIn("BELUM dijalankan", r["note"])

    def test_incident_is_written_to_memory(self):
        seen, ctx = self.spy_run({"df -h": "100% /"})
        with ctx:
            da.triage("disk penuh")
        incidents = [r for r in da._mem_fold().values()
                     if "incident" in (r.get("tags") or [])]
        self.assertEqual(len(incidents), 1)
        self.assertIn("TRIASE", incidents[0]["text"])
        self.assertIn("disk penuh", incidents[0]["text"])

    def test_incident_also_reaches_cortex_events(self):
        seen, ctx = self.spy_run()
        with ctx:
            da.triage("502 bad gateway")
        events = da._events_read(hours=1)
        self.assertTrue(any(e.get("event") == "triage" for e in events))

    def test_context_includes_recent_deploy(self):
        da._audit("laravel_deploy", "branch=main", {"success": False})
        seen, ctx = self.spy_run()
        with ctx:
            r = da.triage("502")
        self.assertTrue(any("laravel_deploy" in c for c in r["context"]))


if __name__ == "__main__":
    unittest.main()
