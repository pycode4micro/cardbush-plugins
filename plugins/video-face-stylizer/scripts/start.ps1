$ErrorActionPreference = 'Stop'
# MCP hosts may inherit PowerShell 7 module paths while launching Windows PowerShell.
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding
$pluginRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'runtime_fingerprint.ps1')
. (Join-Path $PSScriptRoot 'hidden_process.ps1')
$runtimePython = Join-Path $pluginRoot '.venv\Scripts\python.exe'
$marker = Join-Path $pluginRoot '.runtime-ready.json'
$expected = Get-RuntimeFingerprint $pluginRoot
$ready = $false
if ((Test-Path -LiteralPath $runtimePython) -and (Test-Path -LiteralPath $marker)) {
    try {
        $saved = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        $ready = $saved.runtimeHash -eq $expected -and $saved.root -eq $pluginRoot
    } catch { $ready = $false }
}
if (-not $ready) {
    [Console]::Error.WriteLine('Preparing video-face-stylizer dependencies. First launch requires network access.')
    & (Join-Path $PSScriptRoot 'setup.ps1') -NoPackage
}
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$runtimeExit = Invoke-HiddenProcess -Program $runtimePython -Arguments @('-m', 'video_face_stylizer') -ForwardInput
exit $runtimeExit
