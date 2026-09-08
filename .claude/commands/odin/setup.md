Saat user menjalankan /odin:setup, PANDU user memakai CLI `odin` — JANGAN mengedit
`~/.claude.json` atau `settings.json` secara manual. Sejak v2.0 seluruh konfigurasi
(server, project, hook guard, allow-list, entry SSH) dihasilkan oleh CLI, dan editan
tangan akan tertimpa/berkonflik pada `odin project sync` berikutnya.

## Langkah 1 — Lihat keadaan sekarang

Jalankan:

```bash
odin server list
odin project list
```

Jika perintah `odin` tidak ditemukan, install dulu:

```bash
curl -fsSL https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.sh | bash
```

## Langkah 2 — Tentukan yang dibutuhkan user

| Kondisi | Perintah |
|---|---|
| Belum ada server | `odin server add` (interaktif: host, port, user sudoers, password) |
| Server ada, project belum | `odin project add` |
| Ingin non-interaktif | `odin project add --name <nama> --server <alias> --remote-root /var/www/<nama> --workdir <path> --yes` |
| Config lokal rusak/hilang | `odin project sync --all` |
| Server perlu update agent | `odin update <alias>` |
| Ingin MCP tersedia di semua project | `odin global enable` |

`odin server add` melakukan sendiri: membuat user `odin`, memasang sudoers
(divalidasi `visudo -cf`, dengan rollback bila tidak valid), membuat venv +
`mcp[cli]`, mengunggah `odin_agent.py`/`run.sh`/`odin-dispatch.sh`, memasang kunci
SSH ber-forced-command, lalu MEMBUKTIKAN hasilnya dengan handshake MCP
(`initialize` + `tools/list`). Bila ada langkah yang gagal, config lokal tidak
ditulis dan penyebabnya dilaporkan.

## Langkah 3 — Verifikasi

```bash
odin project status
odin doctor <alias>
```

`odin doctor` melakukan handshake MCP sungguhan per project — bukan sekadar
memeriksa keberadaan file.

## Langkah 4 — Beritahu user

Setelah setup, MCP dimuat saat Claude Code START. Katakan pada user untuk membuka
sesi baru di workdir project, lalu jalankan `/odin:status`.
