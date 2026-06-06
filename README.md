# ODIN v0.9

MCP Agent AI untuk server Linux — jembatan dari Claude Code (otak) ke server live (tangan).
Perintah natural-language -> CLI Ubuntu -> eksekusi -> baca output -> ulangi sampai selesai.

## Arsitektur
- **Otak**: Claude Code (CLI) di laptop.
- **Tangan**: `server/deploy_agent.py` — MCP server ODIN (FastMCP, stdio), di-spawn fresh tiap
  sesi via `ssh <host> /home/deploy/agent/run.sh`.
- **Gerbang keamanan**: `client/deploy_agent_guard.py` — PreToolUse hook di sisi Claude Code.
  READ auto-jalan (file & DB), WRITE wajib konfirmasi + KARTU RISIKO (tier/efek/saran),
  katastrofik tertahan rem darurat ganda (`allow_dangerous` + `_DANGER_RE` server).

## Struktur
| Path | Jalan di | Fungsi |
|------|----------|--------|
| `server/deploy_agent.py` | Server (VPS) | MCP server ODIN: 15 tools (run_command, deploy, memory, inspect, dll) |
| `server/run.sh` | Server | Launcher: set env + exec venv python |
| `client/deploy_agent_guard.py` | Laptop | Risk engine + gerbang read/write |
| `docs/MEMORY_NOTES.md` | — | Catatan sistem memory persisten |
| `examples/` | — | Contoh registrasi MCP + wiring hook |
| `CHANGELOG.md` | — | Riwayat perubahan per versi |

## Pasang di server
    ssh <host>
    mkdir -p /home/deploy/agent && cd /home/deploy/agent
    python3 -m venv .venv && .venv/bin/pip install "mcp[cli]"
    # salin server/deploy_agent.py + server/run.sh ke sini
    chmod 600 deploy_agent.py && chmod 755 run.sh

## Pakai di Claude Code

- MCP: lihat `examples/mcp.json.example` (nama server: `odin`, prefix tool: `mcp__odin__`).
- Hook: salin `client/deploy_agent_guard.py` ke `.claude/hooks/` project (atau `~/.claude/hooks/`
  untuk semua project), lalu wiring seperti `examples/claude-settings.hooks.example.json`.

## Versi
Lihat [CHANGELOG.md](CHANGELOG.md) untuk riwayat perubahan lengkap.
Versi aktif: **0.9.0** — tersimpan di `__version__` pada kedua file Python.

## Keamanan
Batas sebenarnya = hak OS user `deploy` + sudoers. Hook + `_DANGER_RE` = jaring pengaman,
bukan sandbox. Keputusan akhir selalu di operator (konfirmasi WRITE).
