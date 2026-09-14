function Get-RuntimeFingerprint {
    param([string]$Root)
    $parts = @('uv.lock', 'pyproject.toml', 'src/video_face_stylizer/engine/model_sources.json', 'scripts/verify_runtime.py')
    $value = ($parts | ForEach-Object { $_ + ':' + [IO.File]::ReadAllText((Join-Path $Root $_)) }) -join "`n"
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($value))).Replace('-', '') }
    finally { $hash.Dispose() }
}
