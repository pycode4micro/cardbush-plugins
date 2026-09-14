param([string]$Python, [switch]$NoPackage)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$pluginRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'runtime_fingerprint.ps1')
. (Join-Path $PSScriptRoot 'hidden_process.ps1')
if (-not [Environment]::Is64BitProcess) { throw 'Use 64-bit Windows PowerShell for this Windows x64 plugin.' }
$uvVersion = '0.12.10'
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
$uvPath = Join-Path $pluginRoot '.tools\uv.exe'
if ($uvCommand) {
    $installedUv = Invoke-HiddenProcess -Program $uvCommand.Source -Arguments @('--version') -CaptureOutput
    if ($installedUv.ExitCode -eq 0 -and $installedUv.Output -match ('^uv ' + [regex]::Escape($uvVersion) + '(?:\s|$)')) { $uvPath = $uvCommand.Source }
}
function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    $commandExit = Invoke-HiddenProcess -Program $Program -Arguments $Arguments -OutputToError
    if ($commandExit -ne 0) { throw "Command failed ($commandExit): $Program" }
}
if (-not (Test-Path -LiteralPath $uvPath)) {
    $toolsDir = Join-Path $pluginRoot '.tools'
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $zipPath = Join-Path $toolsDir "uv-$uvVersion.zip"
    $baseUri = "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip"
    [Console]::Error.WriteLine("Installing plugin-local uv $uvVersion...")
    Invoke-WebRequest -UseBasicParsing -Uri $baseUri -OutFile $zipPath
    $checksumContent = (Invoke-WebRequest -UseBasicParsing -Uri "$baseUri.sha256").Content
    if ($checksumContent -is [byte[]]) { $checksumContent = [System.Text.Encoding]::UTF8.GetString($checksumContent) }
    $checksum = ([string]$checksumContent).Trim().Split(' ')[0]
    if ($checksum -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash -ne $checksum) {
        throw 'uv download checksum verification failed.'
    }
    Expand-Archive -LiteralPath $zipPath -DestinationPath $toolsDir -Force
    if (-not (Test-Path -LiteralPath $uvPath)) { throw 'uv archive did not contain uv.exe.' }
    Remove-Item -LiteralPath $zipPath
}
$runtimePython = Join-Path $pluginRoot '.venv\Scripts\python.exe'
$pythonRequest = if ($Python) { $Python } else { '3.12' }
# uv provides Python 3.12 when absent. No system Python/PATH changes are needed.
Invoke-Checked $uvPath @('sync', '--project', $pluginRoot, '--locked', '--no-dev', '--python', $pythonRequest)
Invoke-Checked $uvPath @('pip', 'check', '--python', $runtimePython)
Invoke-Checked $runtimePython @((Join-Path $PSScriptRoot 'verify_runtime.py'))
$marker = @{ runtimeHash=(Get-RuntimeFingerprint $pluginRoot); root=$pluginRoot }
$marker | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $pluginRoot '.runtime-ready.json') -Encoding UTF8
if (-not $NoPackage) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $readyZip = Join-Path (Split-Path -Parent $pluginRoot) "video-face-stylizer-0.2.2-local-$stamp.zip"
    Invoke-Checked $runtimePython @((Join-Path $PSScriptRoot 'package_plugin.py'), '--output', $readyZip, '--python', $runtimePython)
    [Console]::Error.WriteLine("Ready: $readyZip")
    [Console]::Error.WriteLine('Import this ZIP into CardBush. Keep the extracted folder and its .venv in place.')
}
