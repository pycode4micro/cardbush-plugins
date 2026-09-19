# Windows entrypoint. Inherit MCP stdio handles; never launch a visible console.
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
# Do not depend solely on a GUI host's stale/sanitized PATH. No global PATH edits.
$uv = $null
if ($env:QIANCHUAN_UV_PATH) {
    if (Test-Path -LiteralPath $env:QIANCHUAN_UV_PATH -PathType Leaf) {
        $uv = (Resolve-Path -LiteralPath $env:QIANCHUAN_UV_PATH).Path
    } else {
        [Console]::Error.WriteLine('QIANCHUAN_RUNTIME_MISSING: QIANCHUAN_UV_PATH is not a file.')
        exit 127
    }
}
if (-not $uv) {
    $bundled = Join-Path $PSScriptRoot '../bin/uv.exe'
    if (Test-Path -LiteralPath $bundled -PathType Leaf) { $uv = (Resolve-Path -LiteralPath $bundled).Path }
}
if (-not $uv) {
    $found = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $uv = $found.Source }
}
if (-not $uv) {
    $paths = @([Environment]::GetEnvironmentVariable('Path','User'), [Environment]::GetEnvironmentVariable('Path','Machine'))
    foreach ($entry in (($paths -join ';') -split ';')) {
        if (-not $entry.Trim()) { continue }
        $candidate = Join-Path ([Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"'))) 'uv.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $uv = (Resolve-Path -LiteralPath $candidate).Path; break }
    }
}
if (-not $uv) {
    foreach ($relative in @('.local/bin/uv.exe', '.cargo/bin/uv.exe')) {
        $candidate = Join-Path $env:USERPROFILE $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $uv = (Resolve-Path -LiteralPath $candidate).Path; break }
    }
}
if (-not $uv) {
    [Console]::Error.WriteLine('QIANCHUAN_RUNTIME_MISSING: install uv or set QIANCHUAN_UV_PATH to its absolute path. Python alone is not sufficient. Do not retry until configured.')
    exit 127
}
$script = Join-Path $PSScriptRoot 'launch.py'
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $uv
$start.Arguments = 'run --frozen --script "' + $script + '"'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
# Windows PowerShell 5.1 lacks ProcessStartInfo.StandardInputEncoding; it uses
# Console.InputEncoding set above. Keep the byte pumps free of StreamWriter BOM.
$start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
$start.EnvironmentVariables['PYTHONUTF8'] = '1'
$process = [System.Diagnostics.Process]::Start($start)
Add-Type -TypeDefinition @'
using System.IO;
using System.Threading.Tasks;
public static class QianchuanPipe {
    public static Task Pump(Stream source, Stream target) {
        return Task.Run(() => {
            byte[] buffer = new byte[4096];
            int count;
            while ((count = source.Read(buffer, 0, buffer.Length)) > 0) {
                target.Write(buffer, 0, count);
                target.Flush();
            }
        });
    }
}
'@
$inputTask = [QianchuanPipe]::Pump([Console]::OpenStandardInput(), $process.StandardInput.BaseStream)
$outputTask = [QianchuanPipe]::Pump($process.StandardOutput.BaseStream, [Console]::OpenStandardOutput())
$errorTask = [QianchuanPipe]::Pump($process.StandardError.BaseStream, [Console]::OpenStandardError())
# Closing the parent MCP pipe must reach the child as EOF.
while (-not $process.WaitForExit(100)) {
    if ($inputTask.IsCompleted) { $process.StandardInput.Close() }
}
$outputTask.GetAwaiter().GetResult()
$errorTask.GetAwaiter().GetResult()
exit $process.ExitCode
