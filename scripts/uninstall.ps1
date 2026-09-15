$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($env:OS -ne 'Windows_NT') {
    throw 'This uninstaller must be run on Windows.'
}

$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinDir = Join-Path $InstallDir 'bin'
$ProfilePath = Join-Path $env:USERPROFILE '.codex\deepseek.config.toml'

$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$PathParts = @($UserPath -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ne $BinDir.TrimEnd('\') })
[Environment]::SetEnvironmentVariable('Path', ($PathParts -join ';'), 'User')

if (Test-Path -LiteralPath $ProfilePath -PathType Leaf) {
    Remove-Item -LiteralPath $ProfilePath -Force
}

Write-Host 'Codex Provider Router PATH entry and DeepSeek profile removed.'
Write-Host 'Original Codex, ChatGPT login, config.toml, MCP settings, and backups were preserved.'
Write-Host "Router files remain at $InstallDir and can be deleted after this PowerShell process exits."

