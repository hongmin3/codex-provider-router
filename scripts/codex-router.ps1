[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RouterArgs
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinDir = Join-Path $InstallDir 'bin'
$RealCodexPathFile = Join-Path $InstallDir 'real-codex-path.txt'
$EncryptedKeyFile = Join-Path $InstallDir 'deepseek-api-key.dpapi'
$ProfilePath = Join-Path $env:USERPROFILE '.codex\deepseek.config.toml'
$script:RoutedExitCode = 0

function Get-RealCodex {
    if (-not (Test-Path -LiteralPath $RealCodexPathFile -PathType Leaf)) {
        throw 'Original Codex path is missing. Re-run scripts\install.ps1.'
    }
    $Path = (Get-Content -LiteralPath $RealCodexPathFile -Raw).Trim()
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Original Codex CLI not found at: $Path"
    }
    return $Path
}

function Save-ProtectedKey {
    param([Security.SecureString]$SecureKey)
    $Protected = ConvertFrom-SecureString $SecureKey
    Set-Content -LiteralPath $EncryptedKeyFile -Value $Protected -Encoding ASCII
}

function Import-KeyFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "DeepSeek key file not found: $Path"
    }
    $Item = Get-Item -LiteralPath $Path
    if ($Item.Length -gt 4096) {
        throw 'Key file is unexpectedly large (maximum: 4096 bytes).'
    }
    $PlainKey = (Get-Content -LiteralPath $Path -Raw).Trim()
    if (-not $PlainKey -or $PlainKey -match '\s') {
        throw 'Key file must contain exactly one non-empty API key without spaces.'
    }
    try {
        Save-ProtectedKey (ConvertTo-SecureString $PlainKey -AsPlainText -Force)
    }
    finally {
        $PlainKey = $null
    }
    Write-Host 'DeepSeek key protected with Windows DPAPI; the plaintext file was not copied.'
}

function Set-KeyInteractively {
    $First = Read-Host 'DeepSeek API Key' -AsSecureString
    $Second = Read-Host 'Confirm DeepSeek API Key' -AsSecureString
    $FirstText = [System.Net.NetworkCredential]::new('', $First).Password
    $SecondText = [System.Net.NetworkCredential]::new('', $Second).Password
    try {
        if (-not $FirstText -or $FirstText -ne $SecondText) {
            throw 'Key is empty or does not match.'
        }
        Save-ProtectedKey $First
    }
    finally {
        $FirstText = $null
        $SecondText = $null
    }
    Write-Host 'DeepSeek key protected with Windows DPAPI.'
}

function Get-DeepSeekKey {
    if ($env:DEEPSEEK_API_KEY) {
        return $env:DEEPSEEK_API_KEY
    }
    if (-not (Test-Path -LiteralPath $EncryptedKeyFile -PathType Leaf)) {
        return $null
    }
    $Protected = (Get-Content -LiteralPath $EncryptedKeyFile -Raw).Trim()
    if (-not $Protected) {
        return $null
    }
    $SecureKey = ConvertTo-SecureString $Protected
    return [System.Net.NetworkCredential]::new('', $SecureKey).Password
}

function Remove-ProviderOverrides {
    param([string[]]$Arguments)
    $Filtered = New-Object System.Collections.Generic.List[string]
    $SkipNext = $false
    foreach ($Argument in $Arguments) {
        if ($SkipNext) {
            $SkipNext = $false
            continue
        }
        if ($Argument -in @('-p', '--profile', '-m', '--model')) {
            $SkipNext = $true
            continue
        }
        if ($Argument -match '^--(?:profile|model)=') {
            continue
        }
        $Filtered.Add($Argument)
    }
    return $Filtered.ToArray()
}

function Remove-LeadingCodexToken {
    # `deep` reads as a prefix, like `sudo`, so both `deep --yolo` and `deep codex --yolo`
    # are natural to type. Codex has no subcommand called `codex`, so dropping a leading one
    # can never swallow a real argument.
    param([string[]]$Arguments)
    if ($Arguments.Count -ge 1 -and $Arguments[0] -eq 'codex') {
        return @($Arguments | Select-Object -Skip 1)
    }
    return $Arguments
}

function Invoke-RoutedCodex {
    param([string[]]$Arguments, [bool]$ForceDeepSeek)
    $RealCodex = Get-RealCodex
    if (-not $ForceDeepSeek) {
        & $RealCodex @Arguments
        $script:RoutedExitCode = $LASTEXITCODE
        return
    }

    $Key = Get-DeepSeekKey
    if (-not $Key) {
        [Console]::Error.WriteLine('DeepSeek key is missing. Run: codex-router key set')
        $script:RoutedExitCode = 78
        return
    }
    if (-not (Test-Path -LiteralPath $ProfilePath -PathType Leaf)) {
        [Console]::Error.WriteLine('DeepSeek profile is missing. Re-run scripts\install.ps1.')
        $script:RoutedExitCode = 78
        return
    }

    $PreviousKey = $env:DEEPSEEK_API_KEY
    try {
        $env:DEEPSEEK_API_KEY = $Key
        $EffectiveArgs = @(
            '--profile', 'deepseek',
            '--model', 'deepseek-flash',
            '-c', 'model_reasoning_effort="high"'
        ) + @(Remove-ProviderOverrides $Arguments)
        Write-Host '[Codex Router] Provider: DeepSeek | Model: deepseek-flash | Reasoning: high'
        & $RealCodex @EffectiveArgs
        $script:RoutedExitCode = $LASTEXITCODE
    }
    finally {
        $Key = $null
        if ($null -eq $PreviousKey) {
            Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
        }
        else {
            $env:DEEPSEEK_API_KEY = $PreviousKey
        }
    }
}

function Show-Doctor {
    $Checks = [ordered]@{
        'Original Codex CLI' = $false
        'DeepSeek profile' = Test-Path -LiteralPath $ProfilePath -PathType Leaf
        'DeepSeek DPAPI key or environment key' = [bool](Get-DeepSeekKey)
        'codex wrapper on PATH' = $false
    }
    try { $Checks['Original Codex CLI'] = Test-Path -LiteralPath (Get-RealCodex) -PathType Leaf } catch {}
    $Command = Get-Command codex -ErrorAction SilentlyContinue
    $Checks['codex wrapper on PATH'] = [bool]($Command -and $Command.Source -eq (Join-Path $BinDir 'codex.cmd'))
    foreach ($Entry in $Checks.GetEnumerator()) {
        $Result = if ($Entry.Value) { 'PASS' } else { 'FAIL' }
        Write-Host "$Result $($Entry.Key)"
    }
    Write-Host 'API keys and tokens were not displayed.'
    return [int]($Checks.Values -contains $false)
}

if (-not $RouterArgs -or $RouterArgs.Count -eq 0) {
    [Console]::Error.WriteLine('Usage: codex-router {deep|deepseek [CODEX_ARGS]|doctor|key set|key import PATH|test deepseek|uninstall}')
    exit 2
}

$Command = $RouterArgs[0]
$Remaining = @($RouterArgs | Select-Object -Skip 1)
switch ($Command) {
    'run' {
        Invoke-RoutedCodex -Arguments $Remaining -ForceDeepSeek ($env:FORCE_DEEPSEEK -eq '1')
        exit $script:RoutedExitCode
    }
    'doctor' {
        exit (Show-Doctor)
    }
    'key' {
        if ($Remaining.Count -eq 1 -and $Remaining[0] -eq 'set') {
            Set-KeyInteractively
            exit 0
        }
        if ($Remaining.Count -eq 2 -and $Remaining[0] -eq 'import') {
            Import-KeyFile $Remaining[1]
            exit 0
        }
    }
    'test' {
        if ($Remaining.Count -eq 1 -and $Remaining[0] -eq 'deepseek') {
            $TestArgs = @('-s', 'read-only', '-a', 'never', 'exec', '--ephemeral', '--skip-git-repo-check', 'Reply exactly: DEEPSEEK_OK')
            Invoke-RoutedCodex -Arguments $TestArgs -ForceDeepSeek $true
            exit $script:RoutedExitCode
        }
    }
    'deepseek' {
        Invoke-RoutedCodex -Arguments $Remaining -ForceDeepSeek $true
        exit $script:RoutedExitCode
    }
    'deep' {
        Invoke-RoutedCodex -Arguments (Remove-LeadingCodexToken $Remaining) -ForceDeepSeek $true
        exit $script:RoutedExitCode
    }
    'uninstall' {
        & (Join-Path $InstallDir 'uninstall.ps1')
        exit $LASTEXITCODE
    }
}

[Console]::Error.WriteLine('Usage: codex-router {deep|deepseek [CODEX_ARGS]|doctor|key set|key import PATH|test deepseek|uninstall}')
exit 2
