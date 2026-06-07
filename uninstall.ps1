# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Uninstaller — Windows (PowerShell)
# Usage: irm https://raw.githubusercontent.com/Syamsuddin/ODIN/main/uninstall.ps1 | iex
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$ErrorActionPreference = "Stop"

$InstallDir      = if ($env:ODIN_INSTALL_DIR) { $env:ODIN_INSTALL_DIR } else { "$env:USERPROFILE\.odin" }
$ClaudeJson      = "$env:USERPROFILE\.claude.json"
$GlobalSettings  = "$env:USERPROFILE\.claude\settings.json"
$OdinModeFile    = "$env:USERPROFILE\.odin_mode"

function Write-Info { param($m) Write-Host "  ▸ " -ForegroundColor Blue -NoNewline; Write-Host $m }
function Write-Ok   { param($m) Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host $m }
function Write-Warn { param($m) Write-Host "  ⚠ " -ForegroundColor Yellow -NoNewline; Write-Host $m }

# Cari Python
$Python = $null
foreach ($candidate in @("python3", "python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $Python = $candidate; break }
}

Write-Host ""
Write-Host "  ODIN Uninstaller" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $InstallDir)) {
    Write-Warn "ODIN tidak ditemukan di $InstallDir — tidak ada yang perlu dihapus."
    exit 0
}

Write-Host "  Ini akan menghapus:" -ForegroundColor Yellow
Write-Host "    • " -NoNewline; Write-Host $InstallDir -ForegroundColor Cyan -NoNewline; Write-Host " (source, venv, seluruh isi)"
if (Test-Path $OdinModeFile) {
    Write-Host "    • " -NoNewline; Write-Host $OdinModeFile -ForegroundColor Cyan
}
Write-Host "    • Entry " -NoNewline; Write-Host "mcpServers.odin" -ForegroundColor Cyan -NoNewline; Write-Host " dari ~/.claude.json"
Write-Host "    • Hook " -NoNewline; Write-Host "mcp__odin__" -ForegroundColor Cyan -NoNewline; Write-Host " dari settings.json"
Write-Host ""
$confirm = Read-Host "  Lanjutkan? [y/N]"
if ($confirm -notmatch '^[Yy]$') {
    Write-Info "Dibatalkan."
    exit 0
}

# ── Hapus direktori ODIN ───────────────────────────────────────────────────
Write-Info "Menghapus $InstallDir..."
Remove-Item -Recurse -Force $InstallDir
Write-Ok "Direktori ODIN dihapus"

# ── Hapus dari PATH user ──────────────────────────────────────────────────
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -like "*$InstallDir*") {
    $newPath = ($userPath -split ";" | Where-Object { $_ -ne $InstallDir }) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Ok "Dihapus dari PATH user"
}

# ── Hapus ~/.odin_mode ─────────────────────────────────────────────────────
if (Test-Path $OdinModeFile) {
    Remove-Item -Force $OdinModeFile
    Write-Ok "$OdinModeFile dihapus"
}

# ── Bersihkan ~/.claude.json (hapus mcpServers.odin) ───────────────────────
if ($Python -and (Test-Path $ClaudeJson)) {
    Write-Info "Membersihkan mcpServers.odin dari ~/.claude.json..."
    $pyResult = & $Python -c @"
import json, sys
try:
    with open(r'$ClaudeJson') as f:
        data = json.load(f)
    mcp = data.get('mcpServers', {})
    removed = []
    if 'odin' in mcp:
        del mcp['odin']
        removed.append('odin')
    if removed:
        with open(r'$ClaudeJson', 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        print(','.join(removed))
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
"@ 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "mcpServers.odin dihapus dari ~/.claude.json"
    } else {
        Write-Info "mcpServers.odin tidak ditemukan — skip"
    }
} elseif (-not (Test-Path $ClaudeJson)) {
    Write-Info "~/.claude.json tidak ada — skip"
}

# ── Bersihkan settings.json (hapus hook & permissions odin) ────────────────
function Clean-Settings {
    param($SettingsPath)
    if (-not (Test-Path $SettingsPath)) { return $false }
    if (-not $Python) { return $false }

    & $Python -c @"
import json, sys
try:
    with open(r'$SettingsPath') as f:
        data = json.load(f)
    changed = False
    perms = data.get('permissions', {})
    allow = perms.get('allow', [])
    new_allow = [p for p in allow if not p.startswith('mcp__odin__')]
    if len(new_allow) != len(allow):
        perms['allow'] = new_allow
        changed = True
    hooks = data.get('hooks', {})
    pre = hooks.get('PreToolUse', [])
    new_pre = [h for h in pre if not (isinstance(h, dict) and 'mcp__odin__' in h.get('matcher', ''))]
    if len(new_pre) != len(pre):
        hooks['PreToolUse'] = new_pre
        changed = True
    if changed:
        with open(r'$SettingsPath', 'w') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        sys.exit(0)
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
"@ 2>$null
    return ($LASTEXITCODE -eq 0)
}

Write-Info "Membersihkan hook & permissions odin dari settings.json..."
$cleaned = $false

if (Clean-Settings $GlobalSettings) {
    Write-Ok "Global settings (~/.claude/settings.json) dibersihkan"
    $cleaned = $true
}

$projectsDir = "$env:USERPROFILE\.claude\projects"
if (Test-Path $projectsDir) {
    Get-ChildItem -Path $projectsDir -Recurse -Filter "settings.json" | ForEach-Object {
        if (Clean-Settings $_.FullName) {
            Write-Ok "Project settings ($($_.FullName)) dibersihkan"
            $cleaned = $true
        }
    }
}

if (-not $cleaned) {
    Write-Info "Tidak ada hook/permissions odin ditemukan — skip"
}

# ── Tawarkan cleanup server ────────────────────────────────────────────────
Write-Host ""
$doServer = Read-Host "  Hapus ODIN dari server juga? [y/N]"
if ($doServer -match '^[Yy]$') {
    Write-Host ""
    $serverHost = Read-Host "  SSH user@host server (contoh: root@192.168.1.100)"
    if ($serverHost) {
        Write-Info "Menghubungi $serverHost..."
        $sshOk = $true
        try {
            ssh -o ConnectTimeout=10 $serverHost "rm -rf /home/odin/odin_agent.py /home/odin/run.sh /home/odin/.venv /home/odin/memory" 2>$null
        } catch {
            $sshOk = $false
        }
        if ($LASTEXITCODE -eq 0 -and $sshOk) {
            Write-Ok "File ODIN di server dihapus"
            Write-Warn "User odin masih ada — hapus manual: sudo userdel -r odin"
        } else {
            Write-Warn "Gagal menghapus — hapus manual di server:"
            Write-Host "    rm -rf /home/odin/odin_agent.py /home/odin/run.sh /home/odin/.venv /home/odin/memory" -ForegroundColor Cyan
        }
    }
}

Write-Host ""
Write-Host "  ✓ ODIN berhasil di-uninstall." -ForegroundColor Green
Write-Host ""
