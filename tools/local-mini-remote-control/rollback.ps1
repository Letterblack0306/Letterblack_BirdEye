param(
  [Parameter(Mandatory=$true)][string]$BackupDir,
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP'
)

$ErrorActionPreference = 'Stop'
$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'

$map = @{
  'mini_local_mcp.py' = (Join-Path $TargetRoot 'mini_local_mcp.py')
  'local_bridge.py' = (Join-Path $RemoteBridge 'local_bridge.py')
  'bridge_config.json' = (Join-Path $RemoteBridge 'bridge_config.json')
}

foreach ($name in $map.Keys) {
  $src = Join-Path $BackupDir $name
  if (Test-Path -LiteralPath $src) {
    Copy-Item -LiteralPath $src -Destination $map[$name] -Force
    Write-Host "RESTORED=$($map[$name])"
  }
}

Write-Host 'ROLLBACK_RESULT=PASS'
