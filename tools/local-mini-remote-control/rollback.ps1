param(
  [Parameter(Mandatory=$true)][string]$BackupDir,
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP',
  [switch]$NoTunnelRestart
)

$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'
$ManifestPath = Join-Path $BackupDir 'backup_manifest.json'
$Allowed = @(
  'mini_local_mcp.py',
  'remote_bridge\local_bridge.py',
  'remote_bridge\bridge_config.json'
)

if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
  throw "Backup manifest not found: $ManifestPath"
}

$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ([string]$manifest.target_root -ne $TargetRoot) {
  throw "Backup target mismatch: $($manifest.target_root)"
}

$bridgePath = Join-Path $RemoteBridge 'local_bridge.py'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine.Contains($bridgePath) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

foreach ($record in @($manifest.files)) {
  $rel = [string]$record.relative_path
  if ($rel -notin $Allowed) { throw "Unexpected backup entry: $rel" }

  $dst = Join-Path $TargetRoot $rel
  $src = Join-Path $BackupDir $rel

  if ([bool]$record.existed) {
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
      throw "Backup file missing: $src"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dst -Force
    Write-Host "RESTORED=$dst"
  } else {
    if (Test-Path -LiteralPath $dst -PathType Leaf) {
      Remove-Item -LiteralPath $dst -Force
      Write-Host "REMOVED_NEW_FILE=$dst"
    }
  }
}

$configPath = Join-Path $RemoteBridge 'bridge_config.json'
if ((Test-Path -LiteralPath $configPath -PathType Leaf) -and
    (Test-Path -LiteralPath $bridgePath -PathType Leaf)) {
  $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
  $python = [string]$config.python
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Restored Python runtime not found: $python"
  }

  $token = [Environment]::GetEnvironmentVariable('LOCAL_MINI_BRIDGE_TOKEN', 'User')
  if ([string]::IsNullOrWhiteSpace($token)) {
    $token = $env:LOCAL_MINI_BRIDGE_TOKEN
  }
  if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'LOCAL_MINI_BRIDGE_TOKEN unavailable; restored files but cannot restart bridge.'
  }
  $env:LOCAL_MINI_BRIDGE_TOKEN = $token
  Remove-Item Env:LOCAL_MINI_REMOTE_WRITE -ErrorAction SilentlyContinue
  Remove-Item Env:LOCAL_MINI_REMOTE_EXEC -ErrorAction SilentlyContinue

  $quotedBridgePath = '"{0}"' -f $bridgePath
  Start-Process -FilePath $python -ArgumentList @($quotedBridgePath) -WorkingDirectory $RemoteBridge -WindowStyle Hidden

  $headers = @{ Authorization = "Bearer $token" }
  $health = $null
  for ($attempt = 1; $attempt -le 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
      $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -Headers $headers -TimeoutSec 3
      if ($health.ok -eq $true) { break }
    } catch {}
  }
  if ($null -eq $health -or $health.ok -ne $true) {
    throw 'Restored bridge did not become healthy.'
  }

  if (-not $NoTunnelRestart) {
    & (Join-Path $PackageRoot 'refresh_connector.ps1') -TargetRoot $TargetRoot
  }
}

Write-Host 'ROLLBACK_RESULT=PASS'
