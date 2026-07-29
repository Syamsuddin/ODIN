# Instalasi MCP ODIN di Laptop Lokal

Panduan ini menjelaskan cara memasang dan menjalankan MCP ODIN **langsung di laptop** (tanpa server VPS/SSH). Cocok untuk:

- Development lokal (Laravel, PHP, Node.js di laptop)
- Testing ODIN sebelum deploy ke server
- Workflow di mana laptop = mesin kerja utama

---

## Prasyarat

| Komponen | Minimum | Cek |
|----------|---------|-----|
| Python | 3.10+ | `python3 --version` |
| pip | terbaru | `python3 -m pip --version` |
| Claude Code CLI | terinstall | `claude --version` |
| Git | terinstall | `git --version` |

---

## Instalasi Cepat (1 Menit)

### Langkah 1: Clone ODIN ke `~/.odin`

```bash
git clone --depth 1 https://github.com/Syamsuddin/ODIN.git ~/.odin
```

Atau jika sudah punya file ZIP:

```bash
unzip ODIN-main.zip -d /tmp/odin-tmp
mv /tmp/odin-tmp/ODIN-main ~/.odin
```

### Langkah 2: Buat Virtual Environment & Install Dependensi

```bash
cd ~/.odin/server
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install "mcp[cli]"
```

### Langkah 3: Buat Config Project

Buat file konfigurasi project:

```bash
mkdir -p ~/.odin/server/projects

cat > ~/.odin/server/projects/myproject.conf << 'EOF'
# ODIN Local Project Config
PROJECT_NAME="myproject"
PROJECT_ROOT="/path/ke/project/kamu"
DEPLOY_MODE="local"
ALLOWED_LOG_DIRS="/var/log,/tmp,$PROJECT_ROOT/storage/logs"
EOF
```

Ganti `/path/ke/project/kamu` dengan path absolut project kamu (misal `$HOME/Projects/myapp`).

### Langkah 4: Buat `run.sh` Executable

```bash
chmod +x ~/.odin/server/run.sh
```

### Langkah 5: Konfigurasi Claude Code

Buat file `.claude/settings.json` di root folder project kamu:

```bash
mkdir -p /path/ke/project/kamu/.claude

cat > /path/ke/project/kamu/.claude/settings.json << 'SETTINGS'
{
  "mcpServers": {
    "odin": {
      "type": "stdio",
      "command": "$HOME/.odin/server/run.sh",
      "args": ["--project", "myproject"]
    }
  },
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
            "command": "python3 '$HOME/.odin/client/odin_guard.py'",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "mcp__odin__inspect_server",
        "hooks": [
          {
            "type": "command",
            "command": "python3 '$HOME/.odin/client/odin_guard.py'",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
SETTINGS
```

> **Penting**: Ganti `$HOME` dengan path absolut home directory kamu (misal `/Users/john` di macOS, `/home/john` di Linux).

### Langkah 6: Test

```bash
cd /path/ke/project/kamu
claude
```

Lalu ketik `/odin:status` untuk verifikasi ODIN berjalan.

---

## Konfigurasi MCP Global (Opsional)

Jika ingin ODIN tersedia di **semua project** (tanpa harus config per project):

```bash
# Tambahkan ke ~/.claude/settings.json (scope user)
cat > ~/.claude/settings.json << 'SETTINGS'
{
  "mcpServers": {
    "odin": {
      "type": "stdio",
      "command": "/absolute/path/ke/.odin/server/run.sh",
      "args": []
    }
  }
}
SETTINGS
```

Tanpa `--project`, `run.sh` akan auto-detect jika hanya ada 1 file `.conf` di `projects/`.

---

## Konfigurasi Project (.conf)

File `~/.odin/server/projects/<nama>.conf` adalah shell script yang di-source oleh `run.sh`. Variabel yang didukung:

| Variable | Default | Keterangan |
|----------|---------|------------|
| `PROJECT_NAME` | (wajib) | Nama project |
| `PROJECT_ROOT` | cwd | Root folder project |
| `DEPLOY_MODE` | `local` | Selalu `local` untuk laptop |
| `ALLOWED_LOG_DIRS` | `/var/log,/var/www,...` | Folder log yang boleh dibaca |
| `MEMORY_DIR` | `<odin>/memory/<project>/` | Folder memory per project |
| `LOCK_CWD_TO_PROJECT` | `0` | `1` = kunci perintah ke PROJECT_ROOT saja |
| `DEFAULT_TIMEOUT` | `180` | Timeout perintah (detik) |
| `MAX_TIMEOUT` | `900` | Timeout maksimum (detik) |

### Contoh Config Laravel Lokal

```bash
# ~/.odin/server/projects/mylaravel.conf
PROJECT_NAME="mylaravel"
PROJECT_ROOT="$HOME/Projects/mylaravel"
DEPLOY_MODE="local"
ALLOWED_LOG_DIRS="/var/log,$HOME/Projects/mylaravel/storage/logs,/tmp"
DEFAULT_TIMEOUT="120"
```

### Contoh Config Node.js Lokal

```bash
# ~/.odin/server/projects/mynode.conf
PROJECT_NAME="mynode"
PROJECT_ROOT="$HOME/Projects/mynode"
DEPLOY_MODE="local"
ALLOWED_LOG_DIRS="/tmp,$HOME/Projects/mynode/logs,$HOME/.pm2/logs"
```

---

## Struktur Folder Setelah Instalasi

```
~/.odin/                          # Hasil clone/extract
├── server/
│   ├── odin_agent.py             # MCP server (berjalan di laptop)
│   ├── run.sh                    # Launcher
│   ├── .venv/                    # Python venv (dibuat manual)
│   │   └── bin/python
│   ├── projects/                 # Config project (dibuat manual)
│   │   └── myproject.conf
│   └── memory/                   # Memory persisten (auto-created)
│       ├── myproject/
│       │   ├── memory.jsonl
│       │   └── audit.jsonl
│       └── _cortex/              # Global memory
│           ├── memory.jsonl
│           └── events.jsonl
├── client/
│   ├── odin_cli.py               # CLI tool
│   ├── odin_guard.py             # Security guard hook
│   ├── odin_mcp_launch.py        # Dynamic MCP launcher
│   └── update_checker.py
├── examples/
│   ├── mcp.local.json.example    # Contoh config MCP lokal
│   └── local-project.conf.example
└── requirements.txt              # mcp[cli]
```

---

## Perbedaan Mode Lokal vs SSH

| Aspek | Lokal | SSH (VPS) |
|-------|-------|-----------|
| `DEPLOY_MODE` | `local` | `ssh` |
| Koneksi | Langsung (stdio) | SSH stdio |
| Perintah dieksekusi di | Laptop | Server remote |
| Keamanan | Guard + hard-block | Guard + hard-block + OS user |
| Performa | Instan | +latency SSH |
| Use case | Development | Production |

> **Perhatian**: Di mode lokal, ODIN menjalankan perintah **di laptop kamu**. Guard hook dan hard-block tetap aktif, tapi perintah berbahaya (rm -rf /, dll.) berdampak langsung ke mesin lokal. Pastikan `LOCK_CWD_TO_PROJECT=1` jika ingin membatasi scope.

---

## Troubleshooting

### ODIN tidak terdeteksi di Claude Code

1. Pastikan `run.sh` executable: `chmod +x ~/.odin/server/run.sh`
2. Pastikan path di `settings.json` absolut (bukan `~` atau `$HOME`)
3. Cek venv: `~/.odin/server/.venv/bin/python -c "import mcp; print('OK')"`
4. Test manual: `~/.odin/server/run.sh --project myproject` (harus output JSON-RPC ke stdout)

### Error "project tidak ditemukan"

- Pastikan file `.conf` ada di `~/.odin/server/projects/`
- Nama project di `--project` harus cocok dengan nama file (tanpa `.conf`)

### Memory tidak tersimpan

- Pastikan folder `~/.odin/server/memory/` writable
- Cek: `ls -la ~/.odin/server/memory/`

### Guard hook error

- Install dependensi CLI: `pip3 install paramiko pyyaml`
- Pastikan path guard di `settings.json` benar dan absolute

---

## Update

```bash
cd ~/.odin && git pull origin main
```

Atau jika diinstall via installer:

```bash
odin-update
```

---

## Catatan Keamanan

Mode lokal berarti ODIN mengeksekusi perintah **langsung di sistem operasi laptop kamu** dengan hak akses user yang menjalankan Claude Code. Meskipun guard hook dan hard-block aktif:

1. **Jangan matikan guard** — selalu gunakan `odin_guard.py` di hooks
2. **Gunakan `LOCK_CWD_TO_PROJECT=1`** jika ingin membatasi akses ke folder project saja
3. **Review setiap kartu risiko** — di mode lokal, dampaknya langsung ke laptop
4. Guard hook membutuhkan Python 3 di PATH
