param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP',
  [switch]$NoRestart,
  [switch]$NoTunnelRestart
)

$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupRoot = Join-Path $TargetRoot ("backups\remote-control-" + $Stamp)

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
New-Item -ItemType Directory -Force -Path $RemoteBridge | Out-Null

$replace = @(
  @{ Src = Join-Path $PackageRoot 'mini_local_mcp.py'; Dst = Join-Path $TargetRoot 'mini_local_mcp.py' },
  @{ Src = Join-Path $PackageRoot 'remote_bridge\local_bridge.py'; Dst = Join-Path $RemoteBridge 'local_bridge.py' },
  @{ Src = Join-Path $PackageRoot 'remote_bridge\start_bridge.ps1'; Dst = Join-Path $RemoteBridge 'start_bridge.ps1' },
  @{ Src = Join-Path $PackageRoot 'remote_bridge\connect_tunnel.ps1'; Dst = Join-Path $RemoteBridge 'connect_tunnel.ps1' },
  @{ Src = Join-Path $PackageRoot 'remote_bridge\tunnel_switch.ps1'; Dst = Join-Path $RemoteBridge 'tunnel_switch.ps1' }
)

foreach ($item in $replace) {
  if (Test-Path -LiteralPath $item.Dst) {
    $name = Split-Path -Leaf $item.Dst
    Copy-Item -LiteralPath $item.Dst -Destination (Join-Path $BackupRoot $name) -Force
  }
  Copy-Item -LiteralPath $item.Src -Destination $item.Dst -Force
}

$configPath = Join-Path $RemoteBridge 'bridge_config.json'
if (Test-Path -LiteralPath $configPath) {
  Copy-Item -LiteralPath $configPath -Destination (Join-Path $BackupRoot 'bridge_config.json') -Force
}

$serverHash = (Get-FileHash -LiteralPath (Join-Path $TargetRoot 'mini_local_mcp.py') -Algorithm SHA256).Hash.ToLowerInvariant()
$template = Get-Content -LiteralPath (Join-Path $PackageRoot 'remote_bridge\bridge_config.template.json') -Raw | ConvertFrom-Json
$template.server_sha256 = $serverHash
$template.server = (Join-Path $TargetRoot 'mini_local_mcp.py')
$template | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $configPath -Encoding UTF8

if (-not $NoRestart) {
  $bridgePath = Join-Path $RemoteBridge 'local_bridge.py'

  $bridgeToken = $env:LOCAL_MINI_BRIDGE_TOKEN
  if ([string]::IsNullOrWhiteSpace($bridgeToken)) {
    $bridgeToken = [Environment]::GetEnvironmentVariable('LOCAL_MINI_BRIDGE_TOKEN', 'User')
  }
  if ([string]::IsNullOrWhiteSpace($bridgeToken)) {
    throw 'LOCAL_MINI_BRIDGE_TOKEN is not set in the current process or User environment.'
  }
  $env:LOCAL_MINI_BRIDGE_TOKEN = $bridgeToken

  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($bridgePath) } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

  # bridge_config.json is authoritative. Clear stale env overrides before launch.
  Remove-Item Env:LOCAL_MINI_REMOTE_WRITE -ErrorAction SilentlyContinue
  Remove-Item Env:LOCAL_MINI_REMOTE_EXEC -ErrorAction SilentlyContinue

  Start-Sleep -Milliseconds 500
  $python = [string]$template.python
  $out = Join-Path $RemoteBridge 'logs\birdeye.out.log'
  $err = Join-Path $RemoteBridge 'logs\birdeye.err.log'
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null

  $quotedBridgePath = '"{0}"' -f $bridgePath
  Start-Process -FilePath $python -ArgumentList @($quotedBridgePath) -WorkingDirectory $RemoteBridge -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err

  $healthUrl = 'http://127.0.0.1:8765/health'
  $headers = @{ Authorization = "Bearer $bridgeToken" }
  $health = $null
  $lastError = $null
  for ($attempt = 1; $attempt -le 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
      $health = Invoke-RestMethod -Uri $healthUrl -Headers $headers -Method Get -TimeoutSec 3
      if ($health.ok -eq $true) { break }
    }
    catch {
      $lastError = $_
    }
  }

  if ($null -eq $health -or $health.ok -ne $true) {
    throw "Authenticated bridge health check failed after restart. Last error: $lastError"
  }
  if ($health.write_enabled -ne $true) {
    throw 'Authenticated bridge health check reports write_enabled=false.'
  }
  if ($health.exec_enabled -ne $true) {
    throw 'Authenticated bridge health check reports exec_enabled=false.'
  }

  Write-Host 'BRIDGE_HEALTH=PASS'
  Write-Host "BRIDGE_WRITE_ENABLED=$($health.write_enabled)"
  Write-Host "BRIDGE_EXEC_ENABLED=$($health.exec_enabled)"

  if (-not $NoTunnelRestart) {
    & (Join-Path $RemoteBridge 'tunnel_switch.ps1') close
    & (Join-Path $RemoteBridge 'tunnel_switch.ps1') open
    if ($LASTEXITCODE -ne 0) {
      throw 'Tunnel restart failed.'
    }
    Write-Host 'TUNNEL_REDISCOVERY=PASS'
  }
}

Write-Host "BACKUP_DIR=$BackupRoot"
Write-Host "SERVER_SHA256=$serverHash"

& (Join-Path $PackageRoot 'verify.ps1') -TargetRoot $TargetRoot
