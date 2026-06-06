# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ODIN Uninstaller — Windows (PowerShell)
# Usage: irm https://raw.githubusercontent.com/Syamsuddin/ODIN/main/uninstall.ps1 | iex
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
$ErrorActionPreference = "Stop"

$InstallDir = if ($env:ODIN_INSTALL_DIR) { $env:ODIN_INSTALL_DIR } else { "$env:USERPROFILE\.odin" }

Write-Host ""
Write-Host "  ⚡ ODIN Uninstaller" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $InstallDir)) {
    Write-Host "  ⚠ " -ForegroundColor Yellow -NoNewline
    Write-Host "ODIN tidak ditemukan di $InstallDir — tidak ada yang perlu dihapus."
    exit 0
}

Write-Host "  Ini akan menghapus:" -ForegroundColor Yellow
Write-Host "    • " -NoNewline; Write-Host $InstallDir -ForegroundColor Cyan -NoNewline; Write-Host " (source, venv, seluruh isi)"
Write-Host ""
$confirm = Read-Host "  Lanjutkan? [y/N]"
if ($confirm -notmatch '^[Yy]$') {
    Write-Host "  ▸ " -ForegroundColor Blue -NoNewline; Write-Host "Dibatalkan."
    exit 0
}

Write-Host "  ▸ " -ForegroundColor Blue -NoNewline; Write-Host "Menghapus $InstallDir..."
Remove-Item -Recurse -Force $InstallDir
Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host "Direktori ODIN dihapus"

# Hapus dari PATH user
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -like "*$InstallDir*") {
    $newPath = ($userPath -split ";" | Where-Object { $_ -ne $InstallDir }) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "  ✓ " -ForegroundColor Green -NoNewline; Write-Host "Dihapus dari PATH user"
}

Write-Host ""
Write-Host "  ✓ ODIN berhasil di-uninstall." -ForegroundColor Green
Write-Host "  ⚠ Catatan:" -ForegroundColor Yellow -NoNewline
Write-Host " Hapus manual konfigurasi MCP & hooks dari:"
Write-Host "    • " -NoNewline; Write-Host "~/.claude.json" -ForegroundColor Cyan -NoNewline; Write-Host " (mcpServers.odin)"
Write-Host "    • " -NoNewline; Write-Host ".claude/settings.json" -ForegroundColor Cyan -NoNewline; Write-Host " project (hooks PreToolUse)"
Write-Host "    • File di server tetap ada — hapus manual jika diperlukan."
Write-Host ""
