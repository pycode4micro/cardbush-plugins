param([switch]$Update)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$sourcePlugin = [IO.Path]::GetFullPath($PSScriptRoot)
$profileRoot = [Environment]::GetFolderPath('UserProfile')
$pluginParent = Join-Path $profileRoot 'plugins'
$targetPlugin = [IO.Path]::GetFullPath((Join-Path $pluginParent 'minimax-music'))
if (-not $targetPlugin.StartsWith(([IO.Path]::GetFullPath($pluginParent) + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe plugin destination.' }
$skillScripts = Join-Path $profileRoot '.codex/skills/.system/plugin-creator/scripts'
if (-not (Test-Path -LiteralPath (Join-Path $skillScripts 'create_basic_plugin.py'))) { throw 'Install with Codex on a computer that includes its plugin-creator system skill.' }
$pythonRuntime = Join-Path $profileRoot '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (-not (Test-Path -LiteralPath $pythonRuntime)) {
  $pythonCommand = Get-Command python3,python -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $pythonCommand) { throw 'Python 3 is needed only for the Codex marketplace registration helper.' }
  $pythonRuntime = $pythonCommand.Source
}
Get-Command node,codex -ErrorAction Stop | Out-Null
$marketplaceFile = Join-Path $profileRoot '.agents/plugins/marketplace.json'
if (Test-Path -LiteralPath $marketplaceFile) {
  & $pythonRuntime (Join-Path $skillScripts 'read_marketplace_name.py') | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Existing personal marketplace validation failed.' }
}
if (Test-Path -LiteralPath $targetPlugin) {
  if (-not $Update) { throw 'minimax-music already exists. Use -Update to intentionally update this plugin.' }
  if (-not (Test-Path -LiteralPath $marketplaceFile)) { throw 'Existing source has no personal marketplace; inspect it before updating.' }
  $market = Get-Content -LiteralPath $marketplaceFile -Raw | ConvertFrom-Json
  $entry = $market.plugins | Where-Object { $_.name -eq 'minimax-music' }
  if (-not $entry -or $entry.source.source -ne 'local' -or $entry.source.path -ne './plugins/minimax-music') { throw 'Existing marketplace entry does not match the personal local source.' }
} else {
  & $pythonRuntime (Join-Path $skillScripts 'create_basic_plugin.py') minimax-music --with-skills --with-scripts --with-assets --with-mcp --with-marketplace
  if ($LASTEXITCODE -ne 0) { throw 'Plugin scaffold failed.' }
}
if ($sourcePlugin -ne $targetPlugin) {
  Get-ChildItem -LiteralPath $sourcePlugin -Force | Where-Object { $_.Name -notin @('node_modules','.git','runtime') } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $targetPlugin -Recurse -Force
  }
}
if ($Update) {
  & $pythonRuntime (Join-Path $skillScripts 'update_plugin_cachebuster.py') $targetPlugin
  if ($LASTEXITCODE -ne 0) { throw 'Cachebuster update failed.' }
  $overlay = Get-Content -LiteralPath (Join-Path $targetPlugin '.codex-plugin/plugin.json') -Raw | ConvertFrom-Json
  $portablePath = Join-Path $targetPlugin 'plugin.json'
  $portable = Get-Content -LiteralPath $portablePath -Raw | ConvertFrom-Json
  $portable.version = $overlay.version
  [IO.File]::WriteAllText($portablePath, (($portable | ConvertTo-Json -Depth 30) + "`n"), [Text.UTF8Encoding]::new($false))
}
& $pythonRuntime -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('yaml') else 1)"
if ($LASTEXITCODE -eq 0) {
  & $pythonRuntime (Join-Path $skillScripts 'validate_plugin.py') $targetPlugin
  if ($LASTEXITCODE -ne 0) { throw 'Plugin validation failed.' }
} else {
  Write-Output 'Optional Python validator needs PyYAML; the bundled package was validated at build time. Continuing with Codex installation validation.'
}
$marketplaceName = (& $pythonRuntime (Join-Path $skillScripts 'read_marketplace_name.py')).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Marketplace name validation failed.' }
& codex plugin add ('minimax-music@' + $marketplaceName) --json
if ($LASTEXITCODE -ne 0) { throw 'Codex plugin installation failed.' }
Write-Output 'Installed. Start a new Codex task to load the plugin. Configure a MiniMax API key or an existing Music 3 server separately.'
