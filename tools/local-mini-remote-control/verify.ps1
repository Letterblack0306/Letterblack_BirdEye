param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP'
)

$ErrorActionPreference = 'Stop'
$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'
$Server = Join-Path $TargetRoot 'mini_local_mcp.py'
$Bridge = Join-Path $RemoteBridge 'local_bridge.py'
$ConfigPath = Join-Path $RemoteBridge 'bridge_config.json'

foreach ($p in @($Server, $Bridge, $ConfigPath)) {
  if (-not (Test-Path -LiteralPath $p)) {
    throw "MISSING: $p"
  }
}

$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$actualHash = (Get-FileHash -LiteralPath $Server -Algorithm SHA256).Hash.ToLowerInvariant()
$expectedHash = ([string]$config.server_sha256).ToLowerInvariant()

if ($actualHash -ne $expectedHash) {
  throw "HASH_MISMATCH expected=$expectedHash actual=$actualHash"
}
if (-not $config.write_enabled) {
  throw 'WRITE_NOT_ENABLED'
}
if (-not $config.exec_enabled) {
  throw 'EXEC_NOT_ENABLED'
}

$python = [string]$config.python
& $python -m py_compile $Server
if ($LASTEXITCODE -ne 0) { throw 'SERVER_PY_COMPILE_FAILED' }

& $python -m py_compile $Bridge
if ($LASTEXITCODE -ne 0) { throw 'BRIDGE_PY_COMPILE_FAILED' }

$required = @(
  'health','system_info','list_drives','list_dir','read_text','stat_path','file_hash',
  'write_text','mkdir','copy_file','move_file','delete_file','run_process'
)

$declared = @($config.remote_tools)
$missing = @($required | Where-Object { $_ -notin $declared })
if ($missing.Count -gt 0) {
  throw ("REMOTE_TOOLS_MISSING: " + ($missing -join ','))
}

Write-Host 'VERIFY_RESULT=PASS'
Write-Host "SERVER_SHA256=$actualHash"
Write-Host "WRITE_ENABLED=$($config.write_enabled)"
Write-Host "EXEC_ENABLED=$($config.exec_enabled)"
Write-Host ("REMOTE_TOOLS=" + ($declared -join ','))
