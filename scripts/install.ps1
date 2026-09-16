[CmdletBinding()]
param(
    [string]$KeyFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($env:OS -ne 'Windows_NT') {
    throw 'This installer must be run in Windows PowerShell or PowerShell on Windows.'
}

# Windows PowerShell 5.1 renders a progress bar for every Invoke-WebRequest read, which turns a
# small download into a multi-second one, and it does not enable TLS 1.2 by default on every host.
$ProgressPreference = 'SilentlyContinue'
if (([Net.ServicePointManager]::SecurityProtocol -band [Net.SecurityProtocolType]::Tls12) -eq 0) {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
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

function Get-RemoteText {
    param([string]$Uri)
    # Windows PowerShell 5.1 hands back Invoke-WebRequest's .Content as a Byte[] whenever the
    # server does not advertise a text Content-Type, and this CDN sends application/octet-stream.
    # PowerShell 7 always hands back a String. Passing the Byte[] straight to a regex does not
    # fail loudly: PowerShell stringifies it to "35 33 47 ..." and every match silently misses.
    # Normalise here so no caller has to remember the difference.
    $Response = Invoke-WebRequest -UseBasicParsing -Uri $Uri
    $Content = $Response.Content
    if ($Content -is [byte[]]) {
        $Content = [System.Text.Encoding]::UTF8.GetString($Content)
    }
    if ($Content -isnot [string]) {
        throw ("Unexpected response body from {0}: {1}" -f $Uri, $Content.GetType().FullName)
    }
    if ($Content.Length -lt 1024) {
        throw ("Download from {0} returned only {1} characters; expected the full setup script. A proxy or captive portal may have replaced the response." -f $Uri, $Content.Length)
    }
    return $Content
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
$SetupContent = Get-RemoteText -Uri $SetupUrl
$CatalogMatch = [regex]::Match(
    $SetupContent,
    "(?ms)<<'CODEX_MODELS_JSON'\s*\r?\n(?<json>.*?)\r?\nCODEX_MODELS_JSON\s*$"
)
if (-not $CatalogMatch.Success) {
    # Say which of the two causes it was: the block moved, or the whole script is not what we think.
    $Detail = if ($SetupContent -match 'CODEX_MODELS_JSON') {
        'the CODEX_MODELS_JSON marker is present but its block no longer matches the expected heredoc shape'
    }
    else {
        'the CODEX_MODELS_JSON marker is absent, so the upstream setup script changed'
    }
    throw ("The official DeepSeek model catalog could not be parsed from {0} ({1} characters downloaded; {2})." -f $SetupUrl, $SetupContent.Length, $Detail)
}
$CatalogJson = $CatalogMatch.Groups['json'].Value
$Catalog = $CatalogJson | ConvertFrom-Json
# Set-StrictMode turns a renamed field into an opaque "property not found" error, so probe first.
$CatalogModels = @()
if ($Catalog.PSObject.Properties['models']) {
    $CatalogModels = @($Catalog.models)
}
if ($CatalogModels.Count -lt 1) {
    throw 'The official DeepSeek model catalog contains no models.'
}

Write-Utf8NoBom -Path $CatalogPath -Value ($CatalogJson.Trim() + [Environment]::NewLine)
Copy-Item -LiteralPath (Join-Path $ProjectDir 'config\deepseek.config.toml') -Destination $ProfilePath -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'codex-router.ps1') -Destination (Join-Path $InstallDir 'codex-router.ps1') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'codex.cmd') -Destination (Join-Path $BinDir 'codex.cmd') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'codex-router.cmd') -Destination (Join-Path $BinDir 'codex-router.cmd') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'deep.cmd') -Destination (Join-Path $BinDir 'deep.cmd') -Force
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
Write-Host 'Force DeepSeek for one run with: deep --yolo   (or: deep codex --yolo)'
Write-Host 'Open a new PowerShell window, then run: codex-router doctor'
