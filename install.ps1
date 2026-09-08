# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Installer — Windows (PowerShell 5.1+ / 7+)
#
#   irm https://raw.githubusercontent.com/Syamsuddin/ODIN/main/install.ps1 | iex
#
# Env (karena `irm | iex` tak bisa menerima parameter):
#   ODIN_VERSION   tag/branch yang dipasang (default: rilis terbaru, fallback main)
#   ODIN_HOME      lokasi state+kode (default: %USERPROFILE%\.odin)
#   ODIN_YES=1     non-interaktif
#   ODIN_NO_SETUP=1  jangan tawarkan `odin setup`
#
# Tata letak (kode dan state dipisah; installer tak pernah memindahkan state):
#   %USERPROFILE%\.odin\app\            kode (git checkout)
#   %USERPROFILE%\.odin\app\.venv\      dependensi CLI terisolasi
#   %USERPROFILE%\.odin\bin\odin.cmd    wrapper CLI  (bin\ masuk PATH user)
#   %USERPROFILE%\.odin\{keys,servers,projects,modes}   state milik user
#
# CATATAN: skrip ini mencerminkan install.sh; jalankan `odin doctor` setelahnya
# untuk memverifikasi. Tanpa hak Administrator.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$ErrorActionPreference = "Stop"

$Repo     = "Syamsuddin/ODIN"
$RepoUrl  = if ($env:ODIN_REPO_URL) { $env:ODIN_REPO_URL } else { "https://github.com/$Repo.git" }
$OdinHome = if ($env:ODIN_HOME) { $env:ODIN_HOME } else { Join-Path $env:USERPROFILE ".odin" }
$Version  = $env:ODIN_VERSION
$Yes      = $env:ODIN_YES -eq "1"
$NoSetup  = $env:ODIN_NO_SETUP -eq "1"
$AppDir   = Join-Path $OdinHome "app"
$VenvDir  = Join-Path $AppDir ".venv"
$BinDir   = Join-Path $OdinHome "bin"

function Write-Info  { param($m) Write-Host "▸ " -ForegroundColor Blue -NoNewline; Write-Host $m }
function Write-Ok    { param($m) Write-Host "✓ " -ForegroundColor Green -NoNewline; Write-Host $m }
function Write-Warn  { param($m) Write-Host "⚠ " -ForegroundColor Yellow -NoNewline; Write-Host $m }
function Write-Err   { param($m) Write-Host "✗ " -ForegroundColor Red -NoNewline; Write-Host $m }
function Write-Fatal { param($m) Write-Err $m; exit 1 }
function Write-Step  { param($n, $t, $m) Write-Host ""; Write-Host "[$n/$t] $m" -ForegroundColor White }
function Test-Cmd    { param($c) return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Ask-Yn {
    param($q, [bool]$default = $true)
    if ($Yes) { return $default }
    $hint = if ($default) { "[Y/n]" } else { "[y/N]" }
    $ans = Read-Host "$q $hint"
    if ([string]::IsNullOrWhiteSpace($ans)) { return $default }
    return $ans -match '^[Yy]'
}

Write-Host ""
Write-Host "   ⚡ ODIN — MCP Agent AI for Claude Code" -ForegroundColor Cyan
Write-Host "      Installer untuk Windows" -ForegroundColor Cyan
Write-Host ""

# ── 1. Prasyarat ────────────────────────────────────────────────────────────
Write-Step 1 6 "Prasyarat"
if (-not (Test-Cmd git)) { Write-Fatal "'git' tidak ditemukan. Install: https://git-scm.com" }
Write-Ok "git $((git --version) -replace 'git version ','')"

$Python = $null
foreach ($c in @("python3", "python", "py")) {
    if (Test-Cmd $c) {
        & $c -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $Python = (Get-Command $c).Source; break }
    }
}
if (-not $Python) { Write-Fatal "Python >= 3.10 tidak ditemukan. Install: https://python.org (centang 'Add to PATH')" }
Write-Ok "Python $(& $Python -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') ($Python)"
if (Test-Cmd ssh) { Write-Ok "ssh tersedia" } else { Write-Warn "'ssh' tidak ditemukan — aktifkan OpenSSH Client (Settings → Optional features)." }
if (Test-Cmd claude) { Write-Ok "Claude Code CLI tersedia" } else { Write-Warn "Claude Code CLI belum ada di PATH — install sebelum 'odin setup'." }

# ── 2. Versi ────────────────────────────────────────────────────────────────
Write-Step 2 6 "Versi"
if (-not $Version) {
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -TimeoutSec 6 -Headers @{ "User-Agent" = "ODIN-Installer" }
        if ($rel.tag_name) { $Version = $rel.tag_name; Write-Info "Versi rilis terbaru: $Version" }
    } catch { }
}
if (-not $Version) { $Version = "main"; Write-Info "Tidak ada info rilis — memakai branch main" }
else { Write-Info "Versi: $Version" }

# ── 3. Kode ─────────────────────────────────────────────────────────────────
Write-Step 3 6 "Kode"
New-Item -ItemType Directory -Force -Path $OdinHome | Out-Null
if (Test-Path (Join-Path $AppDir ".git")) {
    Write-Info "Memperbarui $AppDir → $Version"
    git -C $AppDir fetch --quiet --tags origin
    if ($LASTEXITCODE -ne 0) { Write-Fatal "Gagal fetch dari $RepoUrl. Cek koneksi internet." }
} else {
    Write-Info "Mengunduh ODIN → $AppDir"
    if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
    git clone --quiet $RepoUrl $AppDir
    if ($LASTEXITCODE -ne 0) { Write-Fatal "Gagal clone $RepoUrl. Cek koneksi internet." }
}
git -C $AppDir show-ref --verify --quiet "refs/remotes/origin/$Version"
if ($LASTEXITCODE -eq 0) {
    git -C $AppDir checkout --quiet -B $Version "origin/$Version"
} else {
    git -C $AppDir checkout --quiet --detach $Version
    if ($LASTEXITCODE -ne 0) { Write-Fatal "Ref '$Version' tidak ditemukan di $RepoUrl." }
}
Write-Ok "Kode ODIN @ $(git -C $AppDir describe --tags --always)"

# Migrasi tata letak lama (kode langsung di ~/.odin): hapus HANYA file yang
# dilacak git + .git; state (keys/servers/projects/modes) tidak dilacak → selamat.
if (Test-Path (Join-Path $OdinHome ".git")) {
    Write-Warn "Tata letak lama terdeteksi — memigrasikan (state dipertahankan)"
    $tracked = git -C $OdinHome ls-files
    foreach ($f in $tracked) {
        $p = Join-Path $OdinHome $f
        if (Test-Path $p) { Remove-Item -Force $p -ErrorAction SilentlyContinue }
    }
    Remove-Item -Recurse -Force (Join-Path $OdinHome ".git") -ErrorAction SilentlyContinue
    foreach ($d in @("client", "server", "tests", "examples", "assets", "docs", ".claude", ".venv", "__pycache__")) {
        $p = Join-Path $OdinHome $d
        if ((Test-Path $p) -and -not ((Get-Item $p).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Get-ChildItem $p -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            if (-not (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue)) { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
            elseif ($d -eq ".venv") { Remove-Item -Recurse -Force $p -ErrorAction SilentlyContinue }
        }
    }
    foreach ($f in @("odin.cmd", "odin.ps1", "odin-update.ps1", ".update-cache.json")) {
        Remove-Item -Force (Join-Path $OdinHome $f) -ErrorAction SilentlyContinue
    }
    # PATH lama menunjuk %USERPROFILE%\.odin langsung — cabut.
    $up = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($up) {
        $new = ($up -split ";" | Where-Object { $_ -and ($_.TrimEnd('\') -ne $OdinHome.TrimEnd('\')) }) -join ";"
        if ($new -ne $up) { [Environment]::SetEnvironmentVariable("Path", $new, "User") }
    }
    Write-Ok "Migrasi selesai — state di $OdinHome dipertahankan"
}

# ── 4. Dependensi (venv terisolasi) ─────────────────────────────────────────
Write-Step 4 6 "Dependensi"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
if ($env:ODIN_SKIP_DEPS -eq "1") {
    Write-Warn "ODIN_SKIP_DEPS=1 — dependensi dilewati (mode uji)"
    $VenvPy = $Python
} else {
    if (-not (Test-Path $VenvPy)) {
        Write-Info "Membuat venv $VenvDir"
        & $Python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { Write-Fatal "Gagal membuat venv." }
    }
    Write-Info "Menginstall dependensi CLI (paramiko, pyyaml)"
    & $VenvPy -m pip install --quiet --upgrade pip 2>$null
    & $VenvPy -m pip install --quiet -r (Join-Path $AppDir "requirements-cli.txt")
    if ($LASTEXITCODE -ne 0) { Write-Fatal "Gagal install dependensi: $VenvPy -m pip install -r $AppDir\requirements-cli.txt" }
    Write-Ok "Dependensi CLI terpasang di venv"
}

# ── 5. Wrapper + PATH ───────────────────────────────────────────────────────
Write-Step 5 6 "Perintah & PATH"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$Cli = Join-Path $AppDir "client\odin_cli.py"
@"
@echo off
rem ODIN CLI wrapper — dibuat install.ps1. Jangan edit; jalankan ulang installer.
set "ODIN_HOME=$OdinHome"
"$VenvPy" "$Cli" %*
"@ | Set-Content -Path (Join-Path $BinDir "odin.cmd") -Encoding ASCII
@"
`$env:ODIN_HOME = "$OdinHome"
& "$VenvPy" "$Cli" @args
"@ | Set-Content -Path (Join-Path $BinDir "odin.ps1") -Encoding UTF8
Write-Ok "Perintah: $BinDir\odin.cmd"

# Junction kompatibilitas: entry lama menunjuk ~\.odin\client\... dan ~\.odin\server\...
foreach ($d in @("client", "server")) {
    $link = Join-Path $OdinHome $d
    $target = Join-Path $AppDir $d
    if (Test-Path $link) {
        if ((Get-Item $link).Attributes -band [IO.FileAttributes]::ReparsePoint) { Remove-Item $link -Force }
    }
    if (-not (Test-Path $link)) {
        try { New-Item -ItemType Junction -Path $link -Target $target | Out-Null } catch { }
    }
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($userPath -split ";") -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable("Path", (@($userPath, $BinDir) -join ";").Trim(";"), "User")
    Write-Ok "PATH user + $BinDir — buka terminal baru agar 'odin' dikenali"
} else {
    Write-Ok "$BinDir sudah ada di PATH"
}
$env:Path = "$BinDir;$env:Path"

$slashSrc = Join-Path $AppDir ".claude\commands\odin"
$slashDst = Join-Path $env:USERPROFILE ".claude\commands\odin"
if (Test-Path $slashSrc) {
    New-Item -ItemType Directory -Force -Path $slashDst | Out-Null
    Copy-Item (Join-Path $slashSrc "*.md") $slashDst -Force
    Write-Ok "Slash command /odin:* → $slashDst"
}

# ── 6. Verifikasi ───────────────────────────────────────────────────────────
Write-Step 6 6 "Verifikasi"
$verOut = & (Join-Path $BinDir "odin.cmd") --version 2>&1
if ($LASTEXITCODE -ne 0) { Write-Fatal "Wrapper gagal dijalankan: $verOut" }
Write-Ok "$verOut"
if ($env:ODIN_SKIP_DEPS -ne "1") {
    & $VenvPy -c "import paramiko, yaml" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Fatal "Modul paramiko/pyyaml tidak bisa di-import dari venv." }
    Write-Ok "Modul paramiko & pyyaml siap"
}

$ver = (Select-String -Path (Join-Path $AppDir "server\odin_agent.py") -Pattern '__version__\s*=\s*"(.+)"' | ForEach-Object { $_.Matches.Groups[1].Value })
Write-Host ""
Write-Host "✓ ODIN v$ver terpasang   $OdinHome" -ForegroundColor Green
Write-Host ""

$hasServers = (Test-Path (Join-Path $OdinHome "servers\*.yaml"))
if ($hasServers) {
    Write-Host "  Server sudah terdaftar. Perintah berguna:"
    Write-Host "    odin doctor              cek laptop + server" -ForegroundColor Cyan
    Write-Host "    odin update <alias>      perbarui agent di server" -ForegroundColor Cyan
    Write-Host "    odin server harden <alias>   sudoers & kunci aman (server lama)" -ForegroundColor Cyan
} else {
    Write-Host "  Langkah berikutnya — satu perintah, wizard memandu sisanya:"
    Write-Host ""
    Write-Host "    odin setup" -ForegroundColor Cyan
    Write-Host ""
    if (-not $NoSetup -and -not $Yes) {
        if (Ask-Yn "Jalankan 'odin setup' sekarang?" $true) {
            & (Join-Path $BinDir "odin.cmd") setup
        }
    }
}
Write-Host ""
