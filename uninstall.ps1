# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Uninstaller — Windows (PowerShell)
#
#   irm https://raw.githubusercontent.com/Syamsuddin/ODIN/main/uninstall.ps1 | iex
#
# Env: ODIN_PURGE=1 (hapus juga kunci & registry), ODIN_YES=1 (tanpa konfirmasi)
# Normalnya cukup `odin uninstall [--purge]` — skrip ini pembungkus yang juga
# membersihkan tata letak lama (ODIN <= v2.2).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$ErrorActionPreference = "Stop"
$OdinHome = if ($env:ODIN_HOME) { $env:ODIN_HOME } else { Join-Path $env:USERPROFILE ".odin" }
$Purge = $env:ODIN_PURGE -eq "1"
$Yes   = $env:ODIN_YES -eq "1"

function Write-Info { param($m) Write-Host "▸ " -ForegroundColor Blue -NoNewline; Write-Host $m }
function Write-Ok   { param($m) Write-Host "✓ " -ForegroundColor Green -NoNewline; Write-Host $m }
function Write-Warn { param($m) Write-Host "⚠ " -ForegroundColor Yellow -NoNewline; Write-Host $m }

# ── Jalur normal: CLI v2.3+ punya `odin uninstall` ──────────────────────────
$wrapper = Join-Path $OdinHome "bin\odin.cmd"
if (Test-Path $wrapper) {
    $args = @()
    if ($Purge) { $args += "--purge" }
    if ($Yes)   { $args += "--yes" }
    & $wrapper uninstall @args
    exit $LASTEXITCODE
}

# ── Jalur lama: kode langsung di ~/.odin (ODIN <= v2.2) ─────────────────────
if (-not (Test-Path $OdinHome)) { Write-Warn "ODIN tidak ditemukan di $OdinHome."; exit 0 }
Write-Host ""
Write-Host "  ODIN Uninstaller (tata letak lama)" -ForegroundColor Cyan
Write-Host "    • kode ODIN di $OdinHome (state $(if ($Purge) {'IKUT DIHAPUS'} else {'dipertahankan'}))"
Write-Host "    • entry MCP odin, hook guard, allow-list di config Claude Code"
Write-Host ""
if (-not $Yes) {
    $ans = Read-Host "  Lanjutkan? [y/N]"
    if ($ans -notmatch '^[Yy]') { Write-Info "Dibatalkan."; exit 0 }
}

$py = $null
foreach ($c in @("python3", "python", "py")) { if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break } }
if ($py) {
    & $py -c @"
import json
from pathlib import Path
home = Path.home()
cj = home / '.claude.json'
if cj.exists():
    try:
        d = json.loads(cj.read_text())
        if 'odin' in (d.get('mcpServers') or {}):
            d['mcpServers'].pop('odin'); cj.write_text(json.dumps(d, indent=2) + '\n')
    except Exception: pass
sp = home / '.claude' / 'settings.json'
if sp.exists():
    try:
        s = json.loads(sp.read_text())
        (s.get('mcpServers') or {}).pop('odin', None)
        for ev, lst in list((s.get('hooks') or {}).items()):
            s['hooks'][ev] = [h for h in lst if not str((h or {}).get('matcher', '')).startswith('mcp__odin__')]
        allow = (s.get('permissions') or {}).get('allow')
        if isinstance(allow, list):
            s['permissions']['allow'] = [a for a in allow if not str(a).startswith('mcp__odin__')]
        sp.write_text(json.dumps(s, indent=2) + '\n')
    except Exception: pass
"@
    Write-Ok "Config Claude Code dibersihkan"
}
Remove-Item -Recurse -Force (Join-Path $env:USERPROFILE ".claude\commands\odin") -ErrorAction SilentlyContinue

$up = [Environment]::GetEnvironmentVariable("Path", "User")
if ($up) {
    $new = ($up -split ";" | Where-Object { $_ -and -not $_.StartsWith($OdinHome) }) -join ";"
    if ($new -ne $up) { [Environment]::SetEnvironmentVariable("Path", $new, "User"); Write-Ok "PATH user dibersihkan" }
}

if ($Purge) {
    Remove-Item -Recurse -Force $OdinHome
    Remove-Item -Force (Join-Path $env:USERPROFILE ".odin_mode") -ErrorAction SilentlyContinue
    Write-Ok "$OdinHome dihapus seluruhnya (termasuk kunci SSH)"
} elseif (Test-Path (Join-Path $OdinHome ".git")) {
    foreach ($f in (git -C $OdinHome ls-files)) {
        Remove-Item -Force (Join-Path $OdinHome $f) -ErrorAction SilentlyContinue
    }
    foreach ($d in @(".git", ".venv", "client", "server", "tests", "odin.cmd", "odin.ps1", "odin-update.ps1")) {
        Remove-Item -Recurse -Force (Join-Path $OdinHome $d) -ErrorAction SilentlyContinue
    }
    Write-Ok "Kode dihapus; state di $OdinHome dipertahankan"
} else {
    Write-Warn "$OdinHome bukan checkout git — state dibiarkan."
}
Write-Host ""
Write-Ok "ODIN di-uninstall dari laptop ini. Server tidak disentuh."
