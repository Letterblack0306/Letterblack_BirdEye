param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP'
)

$ErrorActionPreference = 'Stop'
$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'
$ConfigPath = Join-Path $RemoteBridge 'bridge_config.json'
$BridgePath = Join-Path $RemoteBridge 'local_bridge.py'

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
  throw "bridge config not found: $ConfigPath"
}
if (-not (Test-Path -LiteralPath $BridgePath -PathType Leaf)) {
  throw "bridge implementation not found: $BridgePath"
}

$token = [Environment]::GetEnvironmentVariable('LOCAL_MINI_BRIDGE_TOKEN', 'User')
if ([string]::IsNullOrWhiteSpace($token)) {
  throw 'LOCAL_MINI_BRIDGE_TOKEN is not available in the user environment.'
}

$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$python = [string]$config.python
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "configured bridge Python is not available: $python"
}

# Task Scheduler stops the PowerShell maintainer but does not reliably stop its
# Python child. Reconcile only this exact bridge executable before each launch
# so a restart cannot leave an orphan listening on 8765 and make the new task
# exit with an address-in-use failure.
@(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine.Contains($BridgePath) }) |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }

$env:LOCAL_MINI_BRIDGE_TOKEN = $token
Set-Location $RemoteBridge
& $python $BridgePath
if ($LASTEXITCODE -ne 0) {
  throw "local bridge exited with code $LASTEXITCODE"
}
throw 'local bridge exited unexpectedly'