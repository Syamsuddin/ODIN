# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Installer — Windows (PowerShell 5.1+)
# Usage: irm https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.ps1 | iex
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$ErrorActionPreference = "Stop"

$Repo      = "Syamsuddin/ODIN"
$RepoUrl   = "https://github.com/$Repo.git"
$Branch    = "main"
$InstallDir = if ($env:ODIN_INSTALL_DIR) { $env:ODIN_INSTALL_DIR } else { "$env:USERPROFILE\.odin" }
$MinPython = "3.10"

# ── Warna & simbol ──────────────────────────────────────────────────────────
function Write-Info  { param($m) Write-Host "  ▸ " -ForegroundColor Blue -NoNewline; Write-Host $m }
function Write-Ok    { param($m) Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host $m }
function Write-Warn  { param($m) Write-Host "  ⚠ " -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Write-Err   { param($m) Write-Host "  ✗ " -ForegroundColor Red -NoNewline; Write-Host $m }
function Write-Fatal { param($m) Write-Err $m; exit 1 }

function Show-Banner {
    Write-Host ""
    Write-Host "    ╔═══════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "    ║                                       ║" -ForegroundColor Cyan
    Write-Host "    ║     ⚡  O D I N  Installer  ⚡       ║" -ForegroundColor Cyan
    Write-Host "    ║     MCP Deploy Agent for Claude Code  ║" -ForegroundColor Cyan
    Write-Host "    ║                                       ║" -ForegroundColor Cyan
    Write-Host "    ╚═══════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
}

# ── Cek prasyarat ───────────────────────────────────────────────────────────
function Test-Command { param($cmd) return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Test-Python {
    $py = $null
    foreach ($candidate in @("python3", "python", "py")) {
        if (Test-Command $candidate) { $py = $candidate; break }
    }
    if (-not $py) { Write-Fatal "Python 3 tidak ditemukan. Install Python >= $MinPython dari https://python.org" }

    $ver = & $py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    $ok  = & $py -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Fatal "Python >= $MinPython diperlukan (ditemukan: $ver)." }

    Write-Ok "Python $ver ditemukan ($py)"
    return $py
}

function Test-Prereqs {
    Write-Info "Memeriksa prasyarat..."
    if (-not (Test-Command "git")) { Write-Fatal "'git' tidak ditemukan. Install Git dari https://git-scm.com" }
    $script:Python = Test-Python
    if (-not (Test-Command "ssh")) { Write-Warn "'ssh' tidak ditemukan — koneksi ke server memerlukan OpenSSH" }
    Write-Ok "Semua prasyarat terpenuhi"
}

# ── Install / Update ────────────────────────────────────────────────────────
function Install-Odin {
    if (Test-Path "$InstallDir\.git") {
        Write-Info "Instalasi ODIN sudah ada di $InstallDir — memperbarui..."
        git -C $InstallDir fetch origin $Branch --quiet 2>$null
        git -C $InstallDir reset --hard "origin/$Branch" --quiet 2>$null
        Write-Ok "ODIN diperbarui ke versi terbaru"
    } else {
        Write-Info "Mengunduh ODIN dari GitHub..."
        if (Test-Path $InstallDir) {
            $bak = "$InstallDir.bak.$(Get-Date -Format 'yyyyMMddHHmmss')"
            Write-Warn "$InstallDir sudah ada — backup ke $bak"
            Move-Item $InstallDir $bak
        }
        git clone --depth 1 --branch $Branch $RepoUrl $InstallDir --quiet 2>$null
        Write-Ok "ODIN berhasil diunduh"
    }
}

function Install-Venv {
    $venvDir = "$InstallDir\.venv"
    $venvPy  = "$venvDir\Scripts\python.exe"
    $venvPip = "$venvDir\Scripts\pip.exe"

    if ((Test-Path $venvPy) -and (& $venvPy -c "import mcp" 2>$null; $LASTEXITCODE -eq 0)) {
        Write-Ok "Virtual environment sudah ada & valid"
        return
    }
    Write-Info "Membuat virtual environment..."
    & $script:Python -m venv $venvDir
    & $venvPip install --quiet --upgrade pip
    & $venvPip install --quiet -r "$InstallDir\requirements.txt"
    Write-Ok "Dependensi terinstall (mcp[cli])"
}

# ── Updater script ──────────────────────────────────────────────────────────
function Install-Updater {
    $updater = "$InstallDir\odin-update.ps1"
    @'
$ErrorActionPreference = "Stop"
$OdinDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "  ▸ " -ForegroundColor Blue -NoNewline; Write-Host "Memeriksa update ODIN..."
Set-Location $OdinDir
git fetch origin main --quiet 2>$null
$local  = git rev-parse HEAD
$remote = git rev-parse origin/main
if ($local -eq $remote) {
    $tag = git describe --tags 2>$null
    if (-not $tag) { $tag = $local.Substring(0,7) }
    Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host "ODIN sudah versi terbaru ($tag)"
    exit 0
}
Write-Host "  ⚠ " -ForegroundColor Yellow -NoNewline; Write-Host "Update tersedia! Memperbarui..."
git reset --hard origin/main --quiet 2>$null
if (Test-Path ".venv\Scripts\pip.exe") {
    & .venv\Scripts\pip.exe install --quiet -r requirements.txt
}
$ver = (Select-String -Path "server\deploy_agent.py" -Pattern '__version__\s*=\s*"(.+)"' | ForEach-Object { $_.Matches.Groups[1].Value })
Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host "ODIN diperbarui ke v$ver"
'@ | Set-Content -Path $updater -Encoding UTF8

    # Tambah ke PATH via user environment jika belum ada
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$InstallDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
        Write-Ok "Ditambahkan ke PATH user: $InstallDir"
        Write-Info "Restart terminal agar 'odin-update' tersedia di PATH"
    }
    Write-Ok "Updater siap: $updater"
}

# ── Panduan konfigurasi ────────────────────────────────────────────────────
function Show-ConfigGuide {
    $guardPath = "$InstallDir\client\deploy_agent_guard.py"
    $ver = (Select-String -Path "$InstallDir\server\deploy_agent.py" -Pattern '__version__\s*=\s*"(.+)"' |
            ForEach-Object { $_.Matches.Groups[1].Value })

    Write-Host ""
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host "    ✓ ODIN v$ver berhasil diinstall!" -ForegroundColor Green
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green

    Write-Host "`n  📁 Lokasi:" -ForegroundColor White
    Write-Host "     Instalasi : " -NoNewline; Write-Host $InstallDir -ForegroundColor Cyan
    Write-Host "     Guard     : " -NoNewline; Write-Host $guardPath -ForegroundColor Cyan

    Write-Host "`n  📋 Langkah selanjutnya:" -ForegroundColor White

    Write-Host "`n  1." -ForegroundColor Yellow -NoNewline
    Write-Host " Setup server (di VPS):" -ForegroundColor White
    Write-Host "     scp $InstallDir\server\deploy_agent.py  user@server:/home/deploy/agent/"
    Write-Host "     scp $InstallDir\server\run.sh            user@server:/home/deploy/agent/"

    Write-Host "`n  2." -ForegroundColor Yellow -NoNewline
    Write-Host " Konfigurasi MCP (Claude Code):" -ForegroundColor White
    Write-Host "     Tambahkan ke " -NoNewline; Write-Host "~/.claude.json" -ForegroundColor Cyan -NoNewline; Write-Host ":"
    Write-Host @"

     {
       "mcpServers": {
         "odin": {
           "type": "stdio",
           "command": "ssh",
           "args": ["your-server-alias", "/home/deploy/agent/run.sh"]
         }
       }
     }
"@

    Write-Host "`n  3." -ForegroundColor Yellow -NoNewline
    Write-Host " Pasang guard hook:" -ForegroundColor White
    Write-Host "     Tambahkan ke " -NoNewline; Write-Host ".claude/settings.json" -ForegroundColor Cyan -NoNewline; Write-Host " project:"
    Write-Host @"

     {
       "hooks": {
         "PreToolUse": [
           {
             "matcher": "mcp__odin__(run_command|service_action|laravel_deploy|run_tests|runbook|inspect_server|memory_write|memory_forget)",
             "hooks": [
               {
                 "type": "command",
                 "command": "python3 '$guardPath'",
                 "timeout": 10
               }
             ]
           }
         ]
       }
     }
"@

    Write-Host "`n  4." -ForegroundColor Yellow -NoNewline
    Write-Host " Update ODIN:" -ForegroundColor White
    Write-Host "     Jalankan: " -NoNewline; Write-Host "odin-update" -ForegroundColor Cyan
    Write-Host "     Atau   : " -NoNewline; Write-Host "powershell $InstallDir\odin-update.ps1" -ForegroundColor Cyan

    Write-Host "`n  📖 Dokumentasi: " -NoNewline; Write-Host "https://github.com/$Repo" -ForegroundColor Cyan
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
    Write-Host ""
}

# ── Main ────────────────────────────────────────────────────────────────────
Show-Banner
Write-Info "Terdeteksi: Windows $([System.Environment]::OSVersion.Version)"
Test-Prereqs
Install-Odin
Install-Venv
Install-Updater
Show-ConfigGuide
