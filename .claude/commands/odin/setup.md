Saat user menjalankan /odin:setup, lakukan rekonfigurasi ODIN secara interaktif.

## Langkah 1 — Baca Config Saat Ini

Baca `~/.claude.json` dan cari `mcpServers.odin`. Jika ada, tampilkan config saat ini:

```
Config MCP saat ini:
  SSH host : [args[0]]
  Run path : [args[1]]
```

Jika tidak ada, tampilkan:

```
Config MCP belum ada — akan dibuat baru.
```

Cari juga settings.json (global `~/.claude/settings.json` atau project `.claude/settings.json`) untuk guard hook saat ini.

## Langkah 2 — Tanyakan Perubahan

Tanyakan ke user (satu per satu, tunggu jawaban tiap pertanyaan):

1. **SSH host/alias** — "SSH host atau alias untuk server (contoh: `vps-app`, `root@192.168.1.100`):"
   - Tampilkan nilai saat ini sebagai default jika ada
   - Jika user jawab kosong / enter saja, pakai nilai lama

2. **Path run.sh di server** — "Path run.sh di server (default: `/home/odin/run.sh`):"
   - Default: `/home/odin/run.sh` atau nilai saat ini

3. **Scope guard** — "Pasang guard hook di mana? (1) Global, (2) Project ini saja:"
   - Global = `~/.claude/settings.json`
   - Project = `.claude/settings.json` di working directory saat ini

## Langkah 3 — Tes Koneksi

Jalankan:

```bash
ssh -o ConnectTimeout=5 -o BatchMode=yes <host> "test -f <run_path> && echo ok"
```

- Jika OK: tampilkan `✓ Koneksi SSH OK, run.sh ditemukan`
- Jika gagal: tampilkan `⚠ Koneksi gagal — config tetap akan ditulis, perbaiki SSH nanti`

Jangan berhenti meskipun gagal.

## Langkah 4 — Tulis Config

**~/.claude.json** — Baca file, update/tambah `mcpServers.odin`:

```json
{
  "odin": {
    "type": "stdio",
    "command": "ssh",
    "args": ["<SSH_HOST>", "<RUN_PATH>"]
  }
}
```

Gunakan Python untuk merge JSON (jangan timpa key lain di file):

```bash
python3 -c "
import json
with open('$HOME/.claude.json') as f: data = json.load(f)
data.setdefault('mcpServers', {})['odin'] = {'type': 'stdio', 'command': 'ssh', 'args': ['<HOST>', '<PATH>']}
with open('$HOME/.claude.json', 'w') as f: json.dump(data, f, indent=2); f.write('\n')
print('ok')
"
```

**settings.json** — Tulis ke path sesuai pilihan scope (global atau project). Isi:

```json
{
  "permissions": {
    "allow": [
      "mcp__odin__server_info",
      "mcp__odin__tail_log",
      "mcp__odin__http_health_check",
      "mcp__odin__memory_recall",
      "mcp__odin__memory_digest",
      "mcp__odin__session_history",
      "mcp__odin__rollback_plan",
      "mcp__odin__inspect_server"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__odin__(run_command|service_action|laravel_deploy|run_tests|runbook|inspect_server|memory_write|memory_forget)",
        "hooks": [
          {
            "type": "command",
            "command": "python3 '<GUARD_PATH>'",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Dimana `<GUARD_PATH>` = `~/.odin/client/odin_guard.py` (atau path relatif dari project).

Jika file settings.json sudah ada, merge permissions.allow (tanpa duplikat) dan hooks.PreToolUse (replace matcher odin). Jangan timpa entry lain.

## Langkah 5 — Konfirmasi

Tampilkan:

```
✓ Config MCP ditulis ke ~/.claude.json
✓ Guard hook ditulis ke [path settings.json]

Restart sesi Claude Code agar config baru aktif.
```

Jangan tambahkan penjelasan lain.
