[CmdletBinding()]
param(
    [string]$KeyFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($env:OS -ne 'Windows_NT') {
    throw 'This installer must be run in Windows PowerShell or PowerShell on Windows.'
}

$ProjectDir = Split-Path -Parent $PSScriptRoot
$InstallDir = Join-Path $env:LOCALAPPDATA 'CodexProviderRouter'
$BinDir = Join-Path $InstallDir 'bin'
$CodexHome = Join-Path $env:USERPROFILE '.codex'
$RouterStateDir = Join-Path $CodexHome 'router'
$BackupDir = Join-Path $env:USERPROFILE ('.codex-backup\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$RealCodexPathFile = Join-Path $InstallDir 'real-codex-path.txt'
$EncryptedKeyFile = Join-Path $InstallDir 'deepseek-api-key.dpapi'
$ProfilePath = Join-Path $CodexHome 'deepseek.config.toml'
$CatalogPath = Join-Path $RouterStateDir 'deepseek-models.json'

function Write-Utf8NoBom {
    param([string]$Path, [string]$Value)
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Value, $Encoding)
}

function Find-RealCodex {
    if (Test-Path -LiteralPath $RealCodexPathFile -PathType Leaf) {
        $Saved = (Get-Content -LiteralPath $RealCodexPathFile -Raw).Trim()
        if ($Saved -and (Test-Path -LiteralPath $Saved -PathType Leaf)) {
            return $Saved
        }
    }

    $InstalledWrapper = Join-Path $BinDir 'codex.cmd'
    $Candidate = Get-Command codex -All -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandType -in @('Application', 'ExternalScript') -and
            $_.Source -and $_.Source -ne $InstalledWrapper
        } |
        Select-Object -First 1
    if (-not $Candidate) {
        throw 'Original Codex CLI was not found. Install Codex and run `codex login` first.'
    }
    return $Candidate.Source
}

function Import-DeepSeekKey {
    param([string]$Path)
    $Resolved = (Resolve-Path -LiteralPath $Path).Path
    $Item = Get-Item -LiteralPath $Resolved
    if ($Item.PSIsContainer -or $Item.Length -gt 4096) {
        throw 'Key file must be a regular text file no larger than 4096 bytes.'
    }
    $PlainKey = (Get-Content -LiteralPath $Resolved -Raw).Trim()
    if (-not $PlainKey -or $PlainKey -match '\s') {
        throw 'Key file must contain exactly one non-empty API key without spaces.'
    }
    try {
        $SecureKey = ConvertTo-SecureString $PlainKey -AsPlainText -Force
        $Protected = ConvertFrom-SecureString $SecureKey
        Write-Utf8NoBom -Path $EncryptedKeyFile -Value ($Protected + [Environment]::NewLine)
    }
    finally {
        $PlainKey = $null
    }
    return $Resolved
}

$RealCodex = Find-RealCodex

if (-not $KeyFile -and $env:DEEPSEEK_KEY_FILE) {
    $KeyFile = $env:DEEPSEEK_KEY_FILE
}
if (-not $KeyFile) {
    $DefaultKeyFile = Join-Path $ProjectDir 'deepseek-api-key.txt'
    if (Test-Path -LiteralPath $DefaultKeyFile -PathType Leaf) {
        $KeyFile = $DefaultKeyFile
    }
}
if ($KeyFile -and -not (Test-Path -LiteralPath $KeyFile -PathType Leaf)) {
    throw "DeepSeek key file not found: $KeyFile"
}

New-Item -ItemType Directory -Force -Path $InstallDir, $BinDir, $CodexHome, $RouterStateDir, $BackupDir | Out-Null

$BackupTargets = @(
    @{ Source = (Join-Path $CodexHome 'config.toml'); Name = 'codex-config.toml' },
    @{ Source = $ProfilePath; Name = 'deepseek.config.toml' },
    @{ Source = $RealCodexPathFile; Name = 'real-codex-path.txt' },
    @{ Source = $EncryptedKeyFile; Name = 'deepseek-api-key.dpapi' }
)
foreach ($Target in $BackupTargets) {
    if (Test-Path -LiteralPath $Target.Source -PathType Leaf) {
        Copy-Item -LiteralPath $Target.Source -Destination (Join-Path $BackupDir $Target.Name)
    }
}

$SetupUrl = 'https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh'
$SetupContent = (Invoke-WebRequest -UseBasicParsing -Uri $SetupUrl).Content
$CatalogMatch = [regex]::Match(
    $SetupContent,
    "(?ms)<<'CODEX_MODELS_JSON'\s*\r?\n(?<json>.*?)\r?\nCODEX_MODELS_JSON\s*$"
)
if (-not $CatalogMatch.Success) {
    throw 'The official DeepSeek model catalog could not be parsed.'
}
$CatalogJson = $CatalogMatch.Groups['json'].Value
$Catalog = $CatalogJson | ConvertFrom-Json
if (-not $Catalog.models -or $Catalog.models.Count -lt 1) {
    throw 'The official DeepSeek model catalog contains no models.'
}

Write-Utf8NoBom -Path $CatalogPath -Value ($CatalogJson.Trim() + [Environment]::NewLine)
Copy-Item -LiteralPath (Join-Path $ProjectDir 'config\deepseek.config.toml') -Destination $ProfilePath -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'codex-router.ps1') -Destination (Join-Path $InstallDir 'codex-router.ps1') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'codex.cmd') -Destination (Join-Path $BinDir 'codex.cmd') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'codex-router.cmd') -Destination (Join-Path $BinDir 'codex-router.cmd') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1') -Destination (Join-Path $InstallDir 'uninstall.ps1') -Force
Write-Utf8NoBom -Path $RealCodexPathFile -Value ($RealCodex + [Environment]::NewLine)

$ImportedKeyPath = $null
if ($KeyFile) {
    $ImportedKeyPath = Import-DeepSeekKey -Path $KeyFile
}

$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$PathParts = @($UserPath -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ne $BinDir.TrimEnd('\') })
$NewUserPath = (@($BinDir) + $PathParts) -join ';'
[Environment]::SetEnvironmentVariable('Path', $NewUserPath, 'User')
if (@($env:Path -split ';') -notcontains $BinDir) {
    $env:Path = "$BinDir;$env:Path"
}

Write-Host "Installed Codex Provider Router for Windows."
Write-Host "Original Codex: $RealCodex"
Write-Host "Backup: $BackupDir"
if ($ImportedKeyPath) {
    Write-Host "DeepSeek key imported from $ImportedKeyPath and protected with Windows DPAPI."
}
else {
    Write-Host 'DeepSeek key was not provided. Run: codex-router key set'
}
Write-Host 'Open a new PowerShell window, then run: codex-router doctor'
