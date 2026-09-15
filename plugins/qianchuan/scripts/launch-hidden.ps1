# Windows entrypoint. Inherit MCP stdio handles; never launch a visible console.
$ErrorActionPreference = 'Stop'
$uv = (Get-Command uv -CommandType Application -ErrorAction Stop).Source
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
