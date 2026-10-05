param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP',
  [switch]$SkipLive
)

$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'
$Server = Join-Path $TargetRoot 'mini_local_mcp.py'
$Bridge = Join-Path $RemoteBridge 'local_bridge.py'
$ConfigPath = Join-Path $RemoteBridge 'bridge_config.json'
$Smoke = Join-Path $PackageRoot 'smoke_mcp.py'

foreach ($p in @($Server, $Bridge, $ConfigPath, $Smoke)) {
  if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
    throw "MISSING: $p"
  }
}

$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$actualHash = (Get-FileHash -LiteralPath $Server -Algorithm SHA256).Hash.ToLowerInvariant()
$expectedHash = ([string]$config.server_sha256).ToLowerInvariant()

if ($actualHash -ne $expectedHash) {
  throw "HASH_MISMATCH expected=$expectedHash actual=$actualHash"
}
if (-not $config.write_enabled) { throw 'WRITE_NOT_ENABLED' }
if (-not $config.exec_enabled) { throw 'EXEC_NOT_ENABLED' }

$python = [string]$config.python
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "PYTHON_NOT_FOUND: $python"
}

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

if ($SkipLive) {
  Write-Host 'VERIFY_RESULT=STATIC_PASS'
  Write-Host "SERVER_SHA256=$actualHash"
  exit 0
}

$token = $env:LOCAL_MINI_BRIDGE_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
  $token = [Environment]::GetEnvironmentVariable('LOCAL_MINI_BRIDGE_TOKEN', 'User')
}
if ([string]::IsNullOrWhiteSpace($token)) {
  throw 'LOCAL_MINI_BRIDGE_TOKEN not available for live verification.'
}
$env:LOCAL_MINI_BRIDGE_TOKEN = $token

$headers = @{ Authorization = "Bearer $token" }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -Headers $headers -TimeoutSec 5
if ($health.ok -ne $true) { throw 'BRIDGE_HEALTH_FAILED' }
if ($health.write_enabled -ne $true) { throw 'BRIDGE_WRITE_DISABLED' }
if ($health.exec_enabled -ne $true) { throw 'BRIDGE_EXEC_DISABLED' }

& $python $Smoke --url 'http://127.0.0.1:8765/mcp' --root $TargetRoot --python $python
if ($LASTEXITCODE -ne 0) { throw 'MCP_BEHAVIOR_SMOKE_FAILED' }

Write-Host 'VERIFY_RESULT=PASS'
Write-Host "SERVER_SHA256=$actualHash"
Write-Host "WRITE_ENABLED=$($health.write_enabled)"
Write-Host "EXEC_ENABLED=$($health.exec_enabled)"
Write-Host "ROOTS=$($health.roots)"
Write-Host ("REMOTE_TOOLS=" + ($declared -join ','))
