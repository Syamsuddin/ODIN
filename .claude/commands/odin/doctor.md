Saat user menjalankan /odin:doctor, lakukan pemeriksaan diagnostik berikut secara berurutan. Tampilkan hasil tiap langkah langsung (jangan tunggu semua selesai).

## 1. Cek File Lokal

Periksa keberadaan file-file berikut menggunakan Bash (`test -f`):

- `~/.claude.json` — ada? Ada entry `mcpServers.odin`?
- Guard hook: cari path guard dari config settings.json (hooks → PreToolUse → matcher `mcp__odin__` → command). File guard ada?
- `~/.odin_mode` — ada? Isinya apa?

Tampilkan per item: ✓ OK atau ✗ Masalah (dengan penjelasan singkat).

## 2. Cek Config MCP

Baca `~/.claude.json` dan periksa:
- `mcpServers.odin` ada?
- `type` = `stdio`?
- `command` = `ssh`?
- `args` berisi [host, path]? Host dan path apa?

Tampilkan: ✓ Config MCP valid, atau ✗ detail masalah.

## 3. Cek Guard Hook

Cari settings.json (global: `~/.claude/settings.json`, atau project: `.claude/settings.json` di project directories). Periksa:
- Ada `hooks.PreToolUse` dengan matcher `mcp__odin__`?
- Ada `permissions.allow` dengan entry `mcp__odin__`?
- Path guard di hook command — file exist?

Tampilkan: ✓ Guard terpasang, atau ✗ detail masalah.

## 4. Cek Koneksi SSH

Dari config MCP (langkah 2), ambil SSH host. Jalankan:

```bash
ssh -o ConnectTimeout=5 -o BatchMode=yes <host> "echo ok" 2>&1
```

Tampilkan: ✓ SSH OK, atau ✗ Gagal (tampilkan error message).

## 5. Cek MCP Server

Panggil `mcp__odin__server_info`. Jika berhasil:
- Tampilkan: ✓ MCP server merespons
- Tampilkan versi ODIN dari respons (jika ada)

Jika gagal:
- Tampilkan: ✗ MCP server tidak merespons
- Saran: "Periksa apakah `/home/odin/run.sh` ada dan executable di server"

## 6. Ringkasan

Tampilkan satu baris summary:

- Jika semua OK: `Semua 5 pemeriksaan OK — ODIN siap digunakan.`
- Jika ada masalah: `[N] masalah ditemukan. Perbaiki item bertanda ✗ di atas.`

Jangan tambahkan penjelasan lain setelah ringkasan.
