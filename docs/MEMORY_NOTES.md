# deploy-agent — Memory Management

Patch menambahkan sistem memory persisten ke MCP server `deploy-agent`. Memory bertahan
lintas sesi dan **otomatis termuat di konteks tiap sesi baru** (lewat `FastMCP(instructions=...)`,
karena server di-spawn fresh tiap sesi via SSH stdio).

## Namespace (allowlist, 3)

| ns | Isi | Sumber tulis |
|----|-----|--------------|
| `server` | Fakta infrastruktur: nama service, versi, quirk deploy | Agent (saat operasi) |
| `instruction` | Arahan/preferensi durable user ("selalu backup sebelum deploy") | Dari percakapan — **simpan aturan, bukan transkrip** |
| `profile` | Identitas user (nama, peran, kontak) | Sekali, jarang berubah |

## Tools

- `memory_write(ns, text, key="", tags=[], pinned=False, expires_in_days=0, allow_secret=False)` — upsert by `(ns,key)`.
- `memory_recall(ns="", query="", tag="", limit=50)` — cari/daftar (pinned dulu, terbaru dulu).
- `memory_forget(id="" | ns+key)` — hapus logis (tombstone).
- `memory_digest()` — ringkasan yang sama dengan yang disuntik saat startup.
- Resource `memory://{ns}` — intip read-only per namespace.

## Storage

- Append-only JSONL di `MEMORY_DIR` (default `/home/deploy/agent/memory/memory.jsonl`).
- Fold = last-write-wins per `id` (`id = ns:slug(key)`), tombstone untuk hapus, TTL via `expires_at`.
- Aman konkuren: `O_APPEND` + `fcntl.flock`. Compaction atomic (`temp + os.replace`) bila > `MEMORY_MAX_ENTRIES`.
- Perm ketat: dir `700`, file `600`. **Di luar `PROJECT_ROOT`** → tak terbaca/tertimpa via `run_command`/`tail_log`, tak ikut `git reset --hard`.

## Safety

- Namespace divalidasi (tolak selain 3).
- `text` ≤ `MEMORY_MAX_TEXT` (4000).
- **Secret-guard**: nilai mirip password/token/private-key/JWT ditolak kecuali `allow_secret=True`.

## ENV baru (di `run.sh`)

```
MEMORY_DIR=/home/deploy/agent/memory   # folder simpanan
MEMORY_MAX_TEXT=4000                    # panjang maks teks/entry
MEMORY_MAX_ENTRIES=2000                 # ambang compaction
```

## Cara pasang ke server (jalur version-control, BUKAN edit liar di produksi)

1. Review file ini + `deploy_agent.py` di lokal.
2. Salin ke server: `deploy_agent.py` → `/home/deploy/agent/`, `run.sh` → `/home/deploy/agent/run.sh`.
3. Restart sesi MCP (reconnect) agar 4 tool baru terdaftar & memory ter-load.
4. Seed awal (opsional), via tool `memory_write`:
   - `profile/owner`: "Syams — admin SIMURU"
   - `server/fpm_service` (pinned): "php-fpm = php8.3-fpm"
   - `server/db_backup` (pinned): "mysqldump butuh --single-transaction --no-tablespaces"
   - `server/php_cli` (pinned): "script live pakai php8.3 (php=8.5 tanpa pdo_mysql)"

## Catatan uji

Layer memory diuji terisolasi (stub `mcp`, `MEMORY_DIR` temp): upsert, validasi ns,
secret-guard, tombstone/forget, dan digest — semua sesuai harapan. `py_compile` lolos.
Dependensi hanya stdlib (`fcntl`, `json`, `secrets`, `datetime`) — tak ada paket baru.
