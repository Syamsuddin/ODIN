# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN v2.0 Installer — Windows (PowerShell 5.1+)
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
    Write-Host "    ║     MCP Agent AI for Claude Code      ║" -ForegroundColor Cyan
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

    if (Test-Command "claude") {
        Write-Ok "Claude Code CLI ditemukan"
    } else {
        Write-Fatal "Claude Code CLI belum terinstall. Install dulu: https://docs.anthropic.com/en/docs/claude-code/getting-started"
    }

    if (-not (Test-Command "ssh")) { Write-Warn "'ssh' tidak ditemukan — 'odin server add' memerlukan OpenSSH" }
    Write-Ok "Semua prasyarat terpenuhi"
}

# ── Install / Update repo ──────────────────────────────────────────────────
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

# ── Install CLI dependencies (paramiko, pyyaml) ───────────────────────────
function Install-CliDeps {
    $req = "$InstallDir\requirements-cli.txt"
    if (-not (Test-Path $req)) { return }

    Write-Info "Menginstall dependensi CLI (paramiko, pyyaml)..."
    try {
        & $script:Python -m pip install --quiet -r $req 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Dependensi CLI terinstall"
        } else {
            throw "pip failed"
        }
    } catch {
        Write-Warn "Gagal install dependensi CLI — jalankan manual: pip install paramiko pyyaml"
    }
}

# ── Install odin CLI command ──────────────────────────────────────────────
function Install-CliCommand {
    $cli = "$InstallDir\client\odin_cli.py"
    if (-not (Test-Path $cli)) { return }

    # Buat wrapper batch file agar 'odin' bisa dipanggil dari cmd/powershell
    $wrapper = "$InstallDir\odin.cmd"
    @"
@echo off
"$script:Python" "$cli" %*
"@ | Set-Content -Path $wrapper -Encoding ASCII

    # Buat wrapper PowerShell
    $psWrapper = "$InstallDir\odin.ps1"
    @"
& "$script:Python" "$cli" @args
"@ | Set-Content -Path $psWrapper -Encoding UTF8

    # Tambah ke PATH via user environment jika belum ada
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$InstallDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
        Write-Ok "Ditambahkan ke PATH user: $InstallDir"
        Write-Info "Restart terminal agar 'odin' tersedia di PATH"
    } else {
        Write-Ok "PATH sudah include $InstallDir"
    }
    Write-Ok "Perintah 'odin' siap"
}

# ── Guard hook ────────────────────────────────────────────────────────────
function Install-GuardHook {
    $guard = "$InstallDir\client\odin_guard.py"
    if (-not (Test-Path $guard)) { Write-Fatal "Guard tidak ditemukan di $guard" }
    Write-Ok "Guard hook siap: $guard"
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
$ver = (Select-String -Path "server\odin_agent.py" -Pattern '__version__\s*=\s*"(.+)"' | ForEach-Object { $_.Matches.Groups[1].Value })
Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host "ODIN diperbarui ke v$ver"
'@ | Set-Content -Path $updater -Encoding UTF8

    Write-Ok "Updater siap: odin-update.ps1"
}

# ── Main ────────────────────────────────────────────────────────────────────
Show-Banner
Write-Info "Terdeteksi: Windows $([System.Environment]::OSVersion.Version)"
Test-Prereqs
Install-Odin
Install-CliDeps
Install-CliCommand
Install-GuardHook
Install-Updater

$version = (Select-String -Path "$InstallDir\server\odin_agent.py" -Pattern '__version__\s*=\s*"(.+)"' |
            ForEach-Object { $_.Matches.Groups[1].Value })

Write-Host ""
Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host "    ✓ ODIN v$version terinstall" -ForegroundColor Green
Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green

# Cek apakah sudah ada server terdaftar
$hasServers = (Test-Path "$env:USERPROFILE\.odin\servers\*.yaml") -or (Test-Path "$env:USERPROFILE\.odin\servers\*.json")

if ($hasServers) {
    Write-Host ""
    Write-Ok "Server sudah terdaftar."
    Write-Host ""
    Write-Host "  Kelola server & project:" -ForegroundColor White
    Write-Host "    odin server list        " -ForegroundColor Cyan -NoNewline; Write-Host "— daftar server"
    Write-Host "    odin project list       " -ForegroundColor Cyan -NoNewline; Write-Host "— daftar project"
    Write-Host "    odin server add         " -ForegroundColor Cyan -NoNewline; Write-Host "— tambah server baru"
    Write-Host "    odin project add        " -ForegroundColor Cyan -NoNewline; Write-Host "— tambah project baru"
} else {
    Write-Host ""
    Write-Host "  Langkah selanjutnya:" -ForegroundColor White
    Write-Host ""
    Write-Host "    1. " -ForegroundColor Yellow -NoNewline
    Write-Host "Setup server       " -NoNewline; Write-Host "odin server add" -ForegroundColor Cyan
    Write-Host "    2. " -ForegroundColor Yellow -NoNewline
    Write-Host "Tambah project     " -NoNewline; Write-Host "odin project add" -ForegroundColor Cyan
    Write-Host "    3. " -ForegroundColor Yellow -NoNewline
    Write-Host "Mulai bekerja      " -NoNewline; Write-Host "cd ~\project; claude" -ForegroundColor Cyan
    Write-Host ""

    $doSetup = Read-Host "  Setup server sekarang? [Y/n]"
    if ($doSetup -ne "n" -and $doSetup -ne "N") {
        Write-Host ""
        $odinCmd = "$InstallDir\client\odin_cli.py"
        if (Test-Command "odin") {
            & odin server add
        } elseif (Test-Path $odinCmd) {
            & $script:Python $odinCmd server add
        } else {
            Write-Warn "CLI odin tidak ditemukan — jalankan manual: python $odinCmd server add"
        }
    }
}

Write-Host ""
Write-Host "  Update:  " -NoNewline; Write-Host "odin-update" -ForegroundColor Cyan
Write-Host "  Docs:    " -NoNewline; Write-Host "https://github.com/$Repo" -ForegroundColor Cyan
Write-Host ""
