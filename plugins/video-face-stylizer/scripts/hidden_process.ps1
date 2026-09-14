# Launch console programs without allocating a window. Forward bytes directly so
# MCP stdin/stdout never pass through PowerShell's text pipeline or formatting.
function Invoke-HiddenProcess {
    param(
        [Parameter(Mandatory=$true)][string]$Program,
        [string[]]$Arguments = @(),
        [switch]$ForwardInput,
        [switch]$OutputToError,
        [switch]$CaptureOutput
    )
    $quoted = foreach ($argument in $Arguments) {
        '"' + [regex]::Replace([regex]::Replace($argument, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }
    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $Program
    $processInfo.Arguments = $quoted -join ' '
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.RedirectStandardInput = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo
    $capture = $null
    try {
        if (-not $process.Start()) { throw "Could not start $Program" }
        $output = if ($CaptureOutput) {
            $capture = New-Object System.IO.MemoryStream
            $capture
        } elseif ($OutputToError) { [Console]::OpenStandardError() } else { [Console]::OpenStandardOutput() }
        $outputCopy = $process.StandardOutput.BaseStream.CopyToAsync($output)
        $errorCopy = $process.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
        $inputCopy = $null
        if ($ForwardInput) {
            if (-not ('VideoFace.StdioRelay' -as [type])) {
                Add-Type -TypeDefinition @'
using System.IO;
using System.Threading.Tasks;
namespace VideoFace {
    public static class StdioRelay {
        public static async Task CopyInputAsync(Stream source, Stream destination) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = await source.ReadAsync(buffer, 0, buffer.Length).ConfigureAwait(false)) != 0) {
                await destination.WriteAsync(buffer, 0, count).ConfigureAwait(false);
                // Small JSON-RPC requests must reach the child immediately;
                // FileStream otherwise buffers them until 4 KiB or stdin EOF.
                await destination.FlushAsync().ConfigureAwait(false);
            }
        }
    }
}
'@
            }
            $inputCopy = [VideoFace.StdioRelay]::CopyInputAsync([Console]::OpenStandardInput(), $process.StandardInput.BaseStream)
        } else {
            $process.StandardInput.Close()
        }
        while (-not $process.WaitForExit(50)) {
            if ($inputCopy -and $inputCopy.IsCompleted) {
                # EOF must reach FastMCP so it can close its service cleanly.
                $process.StandardInput.Close()
                $inputCopy = $null
            }
        }
        [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($outputCopy, $errorCopy))
        $exitCode = $process.ExitCode
        if ($CaptureOutput) {
            return @{ ExitCode=$exitCode; Output=[Text.Encoding]::UTF8.GetString($capture.ToArray()) }
        }
        return $exitCode
    } finally {
        if ($capture) { $capture.Dispose() }
        $process.Dispose()
    }
}
