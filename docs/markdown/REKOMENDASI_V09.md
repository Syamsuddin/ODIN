# ODIN v0.9 — Rekomendasi Teknis dari Review Arsitektur

Dokumen ini merangkum temuan review menyeluruh terhadap arsitektur ODIN v0.9.0 dan 14 rekomendasi perbaikan untuk versi selanjutnya. Review mencakup seluruh source code (2318 baris), 80 test, dan interaksi lengkap antara User → Claude Code → Guard → MCP Server → VPS.

2 ide awal (Collaborative Memory dan Health Endpoint HTTP) dicoret setelah analisis dampak arsitektural — alasan lengkap di bagian akhir dokumen.

---

## Peta Interaksi Saat Ini

```
   USER (operator)
     │  ketik di terminal / IDE
     ▼
  CLAUDE CODE (AI Brain)
     │  memutuskan tool mana dipanggil
     │
     ├──▶ deploy_agent_guard.py [PreToolUse Hook]
     │      ├─ classify_command() → allow / ask
     │      ├─ assess_command()  → risk card (5 tier)
     │      └─ _get_mode()      → tier shift (production)
     │              │
     │         ◄────┘ kartu risiko → USER approve/reject
     │
     ├──▶ SSH stdio MCP ──▶ VPS (deploy_agent.py)
     │                        ├─ _mode_gate()         → block/pass
     │                        ├─ _DANGER_RE           → hard block
     │                        ├─ _run()               → subprocess
     │                        ├─ _analyze_output()    → hints (22 pola)
     │                        ├─ _capture_pre_state() → rollback
     │                        ├─ _audit()             → forensik
     │                        └─ memory (JSONL)       → persisten
     │                              │
     │         ◄──────────────────┘ result dict
     │
  CLAUDE CODE
     │  baca result, _analysis, _rollback_hint
     │  putuskan langkah berikutnya
     ▼
   USER (lihat jawaban Claude)
```

---

## Skor Review

| Aspek | Skor | Catatan |
|-------|------|---------|
| Arsitektur | 9/10 | Minimalis, defense-in-depth, separation of concerns. Guard-server mode sync masih manual |
| Keamanan | 9/10 | 4 layer pertahanan, regex realistis (boundary = OS). Quote stripping naif |
| Kecerdasan | 7/10 | Error intelligence & memory bagus, tapi reaktif. Belum ada trend, drift, proactive |
| Efisiensi | 7/10 | Subprocess overhead di startup (7 calls), _mem_fold() redundan. Bisa dipotong 50% |
| Kemudahan | 8/10 | Risk card, Bahasa Indonesia, rollback plan. Belum ada audit reader, mode sync manual |
| Testability | 7/10 | 80 tests ada, tapi hanya Fase 3 & profile/mode. Fase 0-2 belum punya unit test |
| Inovasi | 8/10 | Konsep MCP agent + risk engine unik. Ruang besar untuk kecerdasan proaktif |
| **Overall** | **7.9/10** | Fondasi sangat kuat. Peluang terbesar: kecerdasan proaktif dan efisiensi |

---

## A. Efisiensi — Mengurangi Overhead

### A1. Startup Inspection Cache

**Masalah**: `_full_inspect()` di `deploy_agent.py:841-865` menjalankan 4-7 subprocess calls (`_inspect_base`, `_detect_type`, stack scan, app scan) setiap kali server di-spawn. Setiap call fork bash + SSH. Untuk server yang tidak berubah antar-sesi, ini redundan.

**Rekomendasi**: Load profile dari memory jika masih fresh (< 1 jam). Full inspect hanya jika: (a) profile tidak ada di memory, (b) profile lebih tua dari 1 jam, (c) user panggil `inspect_server()` eksplisit.

```python
def _startup_inspect() -> dict:
    fold = _mem_fold()
    cached = fold.get("server:stack-profile")
    if cached:
        age_str = cached.get("created_at", "")
        if age_str:
            try:
                age = datetime.fromisoformat(age_str)
                if (datetime.now(timezone.utc) - age).total_seconds() < 3600:
                    log.info("Profile dari memory masih fresh (%s), skip inspect", age_str)
                    return _reconstruct_profile_from_summary(cached["text"])
            except (ValueError, TypeError):
                pass
    return _full_inspect()
```

**Dampak**: Menghilangkan 4-7 subprocess calls di mayoritas startup. Waktu startup bisa turun dari ~3-5 detik ke < 0.5 detik.

### A2. _mem_fold() Cache Per-Session

**Masalah**: `_mem_fold()` membaca dan parse seluruh JSONL file setiap kali dipanggil. Dalam satu startup saja, dipanggil 3-4 kali: `_build_memory_digest()`, `_derive_mode()` (2x — dari `_full_inspect` dan override check), dan `memory_write()` compaction check.

**Rekomendasi**: Cache hasil fold dengan invalidation saat `_mem_append()`.

```python
_FOLD_CACHE: dict[str, dict] | None = None

def _mem_fold_cached() -> dict[str, dict]:
    global _FOLD_CACHE
    if _FOLD_CACHE is None:
        _FOLD_CACHE = _mem_fold()
    return _FOLD_CACHE

def _invalidate_fold_cache() -> None:
    global _FOLD_CACHE
    _FOLD_CACHE = None

def _mem_append(record: dict) -> None:
    _invalidate_fold_cache()
    # ... existing append logic
```

Ganti semua pemanggilan `_mem_fold()` yang bukan dari `_mem_fold_cached` itu sendiri menjadi `_mem_fold_cached()`.

**Dampak**: File JSONL dibaca sekali per sesi, bukan 3-4 kali per startup + sekali per memory operation.

### A3. Guard Python Startup Overhead

**Masalah**: Guard adalah PreToolUse hook — Python interpreter di-spawn fresh per tool call. Untuk sesi aktif dengan 20-30 tool calls, itu 20-30 Python startup (~50-100ms masing-masing).

**Status**: Ini limitasi arsitektur hook Claude Code (stdin/stdout per invocation). Tidak bisa diubah saat ini.

**Mitigasi jangka panjang**: Pertimbangkan memindahkan sebagian logic guard ke MCP server side. Server sudah punya `_mode_gate`; bisa diperluas dengan risk assessment yang dikirim sebagai field tambahan di response, sehingga Claude bisa menampilkan risk info tanpa menunggu guard.

---

## B. Kecerdasan — Membuat Claude Lebih Pintar

### B1. Error Frequency Tracking

**Masalah**: `_analyze_output()` mengembalikan hints per-command, tapi tidak melacak apakah error yang sama berulang. Jika `db_conn` muncul 3x dalam satu sesi, Claude tidak punya sinyal bahwa ini masalah sistemik, bukan insidental.

**Rekomendasi**: Tambah counter error per-session dan attach sinyal ke result.

```python
_ERROR_COUNTS: dict[str, int] = {}

def _track_error(analysis: dict) -> str | None:
    etype = analysis.get("error_type")
    if not etype:
        return None
    _ERROR_COUNTS[etype] = _ERROR_COUNTS.get(etype, 0) + 1
    if _ERROR_COUNTS[etype] == 3:
        return (f"Error '{etype}' sudah terjadi 3x sesi ini. "
                "Pertimbangkan memory_write untuk catat quirk ini.")
    return None
```

Di `run_command()`, setelah `_analyze_output()`:

```python
if analysis:
    result["_analysis"] = analysis
    hint = _track_error(analysis)
    if hint:
        result["_analysis"]["recurring_hint"] = hint
```

**Dampak**: Claude bisa mendeteksi masalah berulang dan menyarankan investigasi root cause, bukan hanya memberikan hint yang sama berulang.

### B2. Deploy Config Persistence

**Masalah**: Setiap deploy, Claude harus menentukan `app_path`, `fpm_service`, `branch`, `npm_build` dari memory atau percakapan. Parameter ini hampir selalu identik untuk server yang sama.

**Rekomendasi**: Auto-load dan auto-save deploy configuration.

```python
def _load_deploy_defaults() -> dict:
    fold = _mem_fold_cached()
    cfg = fold.get("server:deploy-config")
    if cfg:
        try:
            return json.loads(cfg.get("text", "{}"))
        except json.JSONDecodeError:
            pass
    return {}

def _save_deploy_config(app_path, branch, fpm_service, npm_build) -> None:
    config = json.dumps({
        "app_path": app_path, "branch": branch,
        "fpm_service": fpm_service, "npm_build": npm_build,
    })
    _mem_append({
        "id": "server:deploy-config", "ns": "server", "key": "deploy-config",
        "text": config, "tags": ["deploy", "auto"],
        "created_at": _now_iso(), "pinned": True, "deleted": False,
    })
```

Di `laravel_deploy()`, setelah deploy sukses, panggil `_save_deploy_config()`. Di sesi berikutnya, Claude bisa baca default dari memory tanpa tanya user.

**Dampak**: Deploy jadi satu langkah — user cukup bilang "deploy", Claude sudah tahu semua parameter.

### B3. Command Suggestion Engine

**Masalah**: Saat `_analyze_output()` mendeteksi error, hints berupa teks naratif. Claude harus "menerjemahkan" hints ke command yang bisa dieksekusi.

**Rekomendasi**: Tambahkan `suggested_commands` di `_analysis`.

```python
_ERROR_PATTERNS_V2 = [
    (r"SQLSTATE\[HY000\] \[2002\]", "db_conn",
     "Tidak bisa konek ke database.",
     [
         {"command": "systemctl status mysql", "risk": "AMAN", "purpose": "Cek status MySQL"},
         {"command": "cat .env | grep DB_", "risk": "AMAN", "purpose": "Cek config DB"},
         {"command": "systemctl restart mysql", "risk": "SEDANG", "purpose": "Restart MySQL"},
     ]),
    # ... pattern lain
]
```

Response jadi:

```json
{
    "_analysis": {
        "error_type": "db_conn",
        "hints": ["Tidak bisa konek ke database."],
        "suggested_commands": [
            {"command": "systemctl status mysql", "risk": "AMAN"},
            {"command": "systemctl restart mysql", "risk": "SEDANG"}
        ]
    }
}
```

**Dampak**: Claude bisa langsung chain ke diagnostic command tanpa menebak. Mengurangi bolak-balik user-Claude dari 3-4 turn jadi 1-2 turn per troubleshoot.

### B4. Trend Detection pada Inspect

**Masalah**: `server_info()` dan `inspect_server()` hanya snapshot satu titik waktu. Tidak ada perbandingan historis ("disk naik 15% sejak minggu lalu", "memory usage trending up").

**Rekomendasi**: Simpan metric ring-buffer di memory dan bandingkan saat inspect.

```python
def _save_metrics_snapshot(base: dict) -> None:
    fold = _mem_fold_cached()
    history_rec = fold.get("server:metrics-history")
    history = []
    if history_rec:
        try:
            history = json.loads(history_rec.get("text", "[]"))
        except json.JSONDecodeError:
            pass
    snapshot = {
        "ts": _now_iso(),
        "disk_pct": base.get("disk_pct", 0),
        "memory_pct": base.get("memory_pct", 0),
        "uptime_days": base.get("uptime_days", 0),
    }
    history.append(snapshot)
    history = history[-7:]  # simpan 7 terakhir
    _mem_append({
        "id": "server:metrics-history", "ns": "server",
        "key": "metrics-history", "text": json.dumps(history),
        "tags": ["metrics", "auto"], "created_at": _now_iso(),
        "pinned": False, "deleted": False,
    })

def _compute_trend(current: dict, history: list[dict]) -> dict:
    if not history:
        return {}
    prev = history[0]  # tertua
    trend = {}
    for key in ("disk_pct", "memory_pct"):
        diff = current.get(key, 0) - prev.get(key, 0)
        if abs(diff) >= 3:
            trend[key] = f"{'+' if diff > 0 else ''}{diff}% vs {len(history)} snapshots lalu"
        else:
            trend[key] = "stabil"
    return trend
```

Panggil di `_full_inspect()`, attach `_trend` ke profile result.

**Dampak**: ODIN bisa memperingatkan "disk naik 12% dalam 5 hari terakhir" sebelum masalah terjadi.

---

## C. Kemudahan Pemakaian — UX Operator

### C1. Risk Card dengan Opsi Rollback

**Masalah**: Risk card menampilkan tier/aksi/efek/saran, tapi tidak menunjukkan "kalau gagal, bisa undo dengan X". Operator approve tanpa tahu opsi mundur.

**Rekomendasi**: Tambahkan baris "Undo" di risk card. Karena guard berjalan di laptop (tidak bisa query server state), implementasi paling realistis adalah lewat skill `/odin` yang menjelaskan rollback sebelum Claude memanggil tool. Alternatif: server mengembalikan `_pre_state` preview di response awal.

Contoh risk card yang ditingkatkan:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟠 RISIKO: TINGGI
Cmd   : git reset --hard origin/main
Dir   : /var/www/simuru
Aksi  : Buang semua perubahan lokal
Efek  : Perubahan belum commit hilang
Saran : Stash dulu jika ada kerja penting
Undo  : git reset --hard <commit-saat-ini>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### C2. Mode Sync Otomatis

**Masalah**: Mode operasi ada di 2 tempat: server (auto-derived dari inspect) dan guard (baca `~/.odin_mode`). Server mengembalikan hint manual `echo 'production' > ~/.odin_mode`, tapi ini sering terlupakan. Akibatnya guard bisa menampilkan risk card tier normal padahal server dalam mode production.

**Rekomendasi**: Dua opsi.

**Opsi A (minimal, rekomendasi)**: Skill `/odin` menyertakan step "sync mode ke laptop" setelah setiap inspect. Claude secara otomatis menjalankan `echo '<mode>' > ~/.odin_mode`.

**Opsi B (arsitektural)**: Tambahkan field `_odin_mode` di setiap MCP response. Guard membaca field ini dari result tool call sebelumnya (jika tersedia di context) untuk menentukan mode, bukan dari file statis.

### C3. Audit Log Reader Tool

**Masalah**: `audit.jsonl` append-only di server, tapi tidak ada tool untuk membacanya. Operator harus `run_command("tail agent/memory/audit.jsonl")` dan parse JSON mentah secara manual.

**Rekomendasi**: Tambahkan tool `audit_tail`.

```python
@mcp.tool()
def audit_tail(last: int = 20, tool_filter: str = "", success_only: bool = False) -> dict:
    """Baca N entry terakhir dari audit log (forensik). Read-only.

    Args:
        last: jumlah entry terakhir (maks 100).
        tool_filter: filter nama tool, mis. "run_command" atau "laravel_deploy".
        success_only: True = hanya tampilkan yang berhasil.
    """
    if not os.path.exists(AUDIT_FILE):
        return {"success": True, "count": 0, "entries": []}
    entries = []
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if tool_filter and rec.get("tool") != tool_filter:
                continue
            if success_only and not rec.get("success"):
                continue
            entries.append(rec)
    entries = entries[-max(1, min(int(last), 100)):]
    return {"success": True, "count": len(entries), "entries": entries}
```

Guard: auto-allow (read-only).

**Dampak**: Operator bisa melihat histori operasi lintas-sesi tanpa parse JSONL manual.

### C4. Test Coverage untuk Fase 0-2

**Masalah**: Test suite (80 tests) hanya mencakup Fase 3 (runbook, rollback) dan profile/mode. Komponen inti — `_run()`, `_analyze_output()`, `classify_command()`, `seg_is_read()`, `memory_write/recall/forget`, `_preflight_deploy()` — tidak punya unit test.

**Rekomendasi**: Tambahkan test files:

- `tests/test_guard.py` — test `classify_command()`, `seg_is_read()`, `assess_command()`, `risk_card()` untuk seluruh 26+ shell rules
- `tests/test_output_intelligence.py` — test `_analyze_output()` untuk semua 22 error patterns
- `tests/test_memory.py` — test `_mem_append()`, `_mem_fold()`, TTL, tombstone, compaction, secret detection
- `tests/test_core.py` — test `_run()`, `_truncate()`, `_path_inside()`, `_build_invocation()`

**Dampak**: Regresi pada komponen inti langsung terdeteksi. Saat ini, perubahan di `seg_is_read()` bisa meloloskan command berbahaya tanpa test yang menangkap.

---

## D. Inovasi Baru

### D1. Watchdog Mode — ODIN yang Proaktif

**Konsep**: Saat ini ODIN 100% reaktif — menunggu Claude memanggil tool. ODIN bisa diubah menjadi "penjaga aktif" yang melapor proaktif saat ada anomali.

**Implementasi**: Kombinasi MCP resource + skill + loop.

1. Tambahkan MCP resource `health://live`:

```python
@mcp.resource("health://live")
def health_live() -> str:
    base = _inspect_base()
    alerts = []
    if base.get("disk_pct", 0) > 90:
        alerts.append(f"DISK KRITIS: {base['disk_pct']}%")
    if base.get("memory_pct", 0) > 90:
        alerts.append(f"MEMORY TINGGI: {base['memory_pct']}%")
    for svc in ("nginx", "mysql"):
        r = _run(f"systemctl is-active {svc} 2>/dev/null", None, 5)
        if r.get("stdout", "").strip() != "active":
            alerts.append(f"SERVICE DOWN: {svc}")
    if not alerts:
        return "OK — semua normal"
    return "ALERT:\n" + "\n".join(alerts)
```

2. Operator bisa aktifkan monitoring berkala via skill:

```
/loop 5m /odin-ops health-check
```

**Dampak**: ODIN berubah dari "tangan yang menunggu perintah" menjadi "penjaga yang aktif memantau". Masalah terdeteksi lebih awal.

### D2. Deploy Fingerprint — Deteksi Drift

**Konsep**: Setelah setiap deploy sukses, simpan "fingerprint" state yang diketahui baik. Sebelum deploy berikutnya, bandingkan dengan fingerprint terakhir untuk mendeteksi drift.

**Implementasi**:

```python
def _capture_deploy_fingerprint(app_path: str) -> dict:
    fp = {"captured_at": _now_iso()}
    r = _run("git rev-parse HEAD 2>/dev/null", app_path, 10)
    if r.get("success"):
        fp["git_hash"] = r["stdout"].strip()
    r = _run("md5sum composer.lock 2>/dev/null | awk '{print $1}'", app_path, 10)
    if r.get("success"):
        fp["composer_lock_md5"] = r["stdout"].strip()
    r = _run("grep -c '^' .env 2>/dev/null", app_path, 5)
    if r.get("success"):
        fp["env_line_count"] = int(r["stdout"].strip() or "0")
    r = _run("php artisan migrate:status 2>/dev/null | grep -c 'Ran'", app_path, 10)
    if r.get("success"):
        fp["migration_count"] = int(r["stdout"].strip() or "0")
    return fp

def _compare_fingerprint(current: dict, previous: dict) -> list[str]:
    changes = []
    if current.get("git_hash") != previous.get("git_hash"):
        changes.append("Git HEAD berubah")
    if current.get("composer_lock_md5") != previous.get("composer_lock_md5"):
        changes.append("composer.lock berubah — vendor perlu update")
    mc = current.get("migration_count", 0) - previous.get("migration_count", 0)
    if mc > 0:
        changes.append(f"{mc} migration baru sejak deploy terakhir")
    ec = current.get("env_line_count", 0) - previous.get("env_line_count", 0)
    if ec != 0:
        changes.append(f".env berubah ({'+' if ec > 0 else ''}{ec} baris)")
    return changes
```

Simpan fingerprint ke memory (`server:deploy-fingerprint`) setelah deploy sukses. Sebelum deploy berikutnya, bandingkan dan tampilkan drift report.

**Dampak**: ODIN bisa mendeteksi perubahan yang tidak terdokumentasi ("siapa yang edit .env langsung di server?", "ada migration yang belum di-deploy").

### D3. Smart Runbook Templates

**Konsep**: Saat ini Claude harus membangun step list runbook dari nol setiap kali. ODIN bisa menyediakan template bawaan untuk workflow umum.

**Implementasi**:

```python
_RUNBOOK_TEMPLATES = {
    "ssl-renew": {
        "description": "Perpanjang sertifikat SSL Let's Encrypt",
        "steps": [
            {"label": "renew", "command": "sudo -n certbot renew", "timeout": 300},
            {"label": "test-nginx", "command": "sudo -n nginx -t", "timeout": 30},
            {"label": "reload-nginx", "command": "sudo -n systemctl reload nginx", "timeout": 60},
        ],
    },
    "db-backup": {
        "description": "Backup database MySQL/MariaDB",
        "steps": [
            {"label": "dump", "command": "mysqldump --single-transaction --no-tablespaces {db} > /var/backups/{app}/$(date +%Y%m%d_%H%M%S).sql", "timeout": 600},
            {"label": "verify", "command": "ls -lh /var/backups/{app}/*.sql | tail -1", "timeout": 10},
        ],
    },
    "log-cleanup": {
        "description": "Bersihkan log lama untuk bebaskan disk",
        "steps": [
            {"label": "check-disk", "command": "df -h /", "timeout": 10},
            {"label": "old-logs", "command": "find /var/log -name '*.gz' -mtime +30 -delete", "timeout": 60},
            {"label": "laravel-log", "command": "truncate -s 0 {app_path}/storage/logs/laravel.log", "timeout": 10},
            {"label": "verify-disk", "command": "df -h /", "timeout": 10},
        ],
    },
}

@mcp.tool()
def runbook_templates(name: str = "") -> dict:
    """Lihat template runbook bawaan. Tanpa argumen = daftar semua.
    Dengan nama = detail template (bisa dipakai langsung di tool runbook).

    Args:
        name: nama template, mis. "ssl-renew". Kosong = daftar semua.
    """
    if not name:
        listing = {k: v["description"] for k, v in _RUNBOOK_TEMPLATES.items()}
        return {"success": True, "templates": listing}
    tpl = _RUNBOOK_TEMPLATES.get(name)
    if not tpl:
        return {"success": False, "error": f"Template '{name}' tidak ada. "
                f"Pilihan: {list(_RUNBOOK_TEMPLATES.keys())}"}
    return {"success": True, "name": name, **tpl}
```

Template juga bisa disimpan dan ditambah via memory — Claude membuat runbook custom, user bilang "simpan sebagai template", ODIN persist ke memory.

**Dampak**: Workflow rutin jadi satu perintah. Mengurangi kesalahan karena Claude membangun step list yang kurang tepat.

### D4. Context Window Budget — Output Sadar Token

**Konsep**: Output ODIN bisa besar (tail 100 baris log, inspect server penuh). Semua ini masuk context window Claude. Output besar yang tidak relevan membuang token dan memperlambat respons.

**Implementasi**: "Smart output" mode yang menyertakan ringkasan di samping output penuh.

```python
def _smart_output(result: dict) -> dict:
    stdout = result.get("stdout", "")
    if len(stdout) > 5000:
        lines = stdout.splitlines()
        result["_output_meta"] = {
            "total_lines": len(lines),
            "total_chars": len(stdout),
            "head_5": "\n".join(lines[:5]),
            "tail_10": "\n".join(lines[-10:]),
        }
    return result
```

Claude bisa memilih: baca ringkasan dulu, minta output penuh kalau perlu. Ini mengurangi token usage tanpa menghilangkan informasi.

**Dampak**: Sesi lebih panjang sebelum context window penuh. Respons Claude lebih cepat karena lebih sedikit teks untuk diproses.

---

## Prioritas Implementasi

Berdasarkan dampak vs effort, dikelompokkan ke 4 fase rilis:

### Fase 1 — Fondasi Efisiensi (v0.10)

| # | Rekomendasi | Effort | Dampak |
|---|-------------|--------|--------|
| 1 | A2. _mem_fold() cache | Rendah | Tinggi — menghilangkan I/O redundan |
| 2 | A1. Startup inspection cache | Rendah | Tinggi — startup 36x lebih cepat (warm) |
| 3 | C4. Test coverage Fase 0-2 | Tinggi | Tinggi — keamanan regresi, wajib sebelum refactoring |

### Fase 2 — Intelligence (v0.11)

| # | Rekomendasi | Effort | Dampak |
|---|-------------|--------|--------|
| 4 | B1. Error frequency tracking | Rendah | Sedang — deteksi masalah berulang |
| 5 | B2. Deploy config persistence | Rendah | Sedang — deploy jadi 1 langkah |
| 6 | B3. Command suggestion engine | Sedang | Tinggi — troubleshoot lebih cepat |
| 7 | B4. Trend detection | Sedang | Sedang — peringatan dini |

### Fase 3 — UX & Tools (v0.12)

| # | Rekomendasi | Effort | Dampak |
|---|-------------|--------|--------|
| 8 | C3. Audit log reader tool | Rendah | Sedang — UX langsung membaik |
| 9 | C2. Mode sync otomatis | Rendah | Sedang — hilangkan inkonsistensi |
| 10 | C1. Risk card + rollback | Sedang | Sedang — operator lebih percaya diri |
| 11 | D3. Smart runbook templates | Sedang | Sedang — workflow instan |

### Fase 4 — Proactive (v0.13)

| # | Rekomendasi | Effort | Dampak |
|---|-------------|--------|--------|
| 12 | D1. Watchdog mode | Sedang | Tinggi — ODIN jadi proaktif |
| 13 | D2. Deploy fingerprint | Sedang | Tinggi — drift detection |
| 14 | D4. Context window budget | Rendah | Sedang — sesi lebih panjang |

### Estimasi Total

| Fase | Baris baru | Effort | Kumulatif |
|------|-----------|--------|-----------|
| Fase 1 (v0.10) | ~1070 (incl. tests) | 3-4 jam | 3-4 jam |
| Fase 2 (v0.11) | ~195 | 4-5 jam | 7-9 jam |
| Fase 3 (v0.12) | ~180 | 2-3 jam | 9-12 jam |
| Fase 4 (v0.13) | ~110 | 3-4 jam | 12-16 jam |
| **Total** | **~1555** | **12-16 jam** | |

---

## Catatan Keamanan

### Quote Stripping Naif

`_strip_quotes()` di `deploy_agent_guard.py:116-119` menghapus isi single/double quotes untuk mencegah operator SQL `>` dianggap redirect shell. Kasus `$'...'` (ANSI-C quoting) dan heredoc tidak di-handle. Dampak rendah — worst case adalah false positive (command aman salah diklasifikasi "ask"), bukan false negative. Tapi layak diperbaiki untuk akurasi.

### DANGER Pattern Sinkronisasi

`_DANGER_RE` (server) dan `DANGER` (guard) harus tetap sinkron. Saat ini guard punya tambahan `kill(all)?` dan `pkill` yang tidak ada di server. Ini by design (guard lebih ketat), tapi perlu didokumentasikan eksplisit agar perubahan di satu sisi tidak lupa direplikasi.

---

## Rekomendasi yang Dicoret (dan Alasannya)

Dua ide awal tidak masuk daftar implementasi setelah analisis dampak arsitektural menyeluruh:

### ~~D5. Collaborative Memory (Cross-Server)~~

**Ide awal**: Namespace ke-4 `global` disimpan di laptop, di-inject ke setiap sesi MCP. Instruksi universal ("selalu backup sebelum deploy") berlaku di semua server tanpa duplikasi.

**Alasan dicoret**:

1. **Memecah prinsip "memory = 1 file di 1 tempat"**. Memory ODIN saat ini 100% di server (JSONL lokal di `MEMORY_DIR`). Menambah memory di laptop menciptakan dua sumber kebenaran yang bisa konflik. Instruksi di `global` bisa bertentangan dengan instruksi di `server:instruction` — mana yang prioritas?

2. **Kompleksitas sync yang tidak sepadan**. Guard atau Claude Code harus membaca global memory, menggabungkan dengan server memory, dan menangani konflik — semua ini sebelum sesi dimulai. Ini menambah ~80 baris kode dan arsitektur baru untuk masalah yang jarang terjadi (saat ini hanya 1 server dikelola).

3. **Sudah ada solusi lebih sederhana**. Skill `/odin` yang sudah dibuat berjalan di laptop dan berlaku di semua project. Instruksi global cukup ditulis di `SKILL.md` sebagai operating principle — tidak perlu memory system baru. Jika suatu hari multi-server jadi kebutuhan nyata, bisa ditinjau ulang.

### ~~D6. Health Endpoint HTTP~~

**Ide awal**: Mode HTTP opsional (`python3 deploy_agent.py --http-health :9100`) sebagai sidecar agar monitoring eksternal (Uptime Kuma, Grafana) bisa query status ODIN.

**Alasan dicoret**:

1. **Mengubah model operasi fundamental**. ODIN saat ini "spawned fresh per session, no daemon, no port". D6 memerlukan proses yang berjalan terus (daemon) — ini MENGUBAH identitas ODIN. Perlu systemd unit baru, log rotation, port security, restart policy.

2. **Memperluas attack surface**. Port HTTP terbuka di server = vektor serangan baru. Meskipun bisa bind ke localhost, ini tetap risiko yang tidak perlu jika belum ada kebutuhan nyata.

3. **D1 (Watchdog resource) sudah cukup**. MCP resource `health://live` sudah menyediakan health check yang bisa diakses via Claude Code session. Dikombinasikan dengan `/loop 5m /odin-ops health-check`, monitoring proaktif sudah tercapai tanpa daemon tambahan. Jika integrasi ke Uptime Kuma/Grafana benar-benar dibutuhkan di kemudian hari, bisa ditinjau ulang — tapi saat ini prematur.

4. **Menambah dependency potensial**. HTTP server memerlukan framework (minimal `http.server` stdlib atau `flask`/`starlette`). Ini bisa merusak prinsip "1 dependency" ODIN.
