param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP',
  [switch]$NoRestart
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
  @{ Src = Join-Path $PackageRoot 'remote_bridge\local_bridge.py'; Dst = Join-Path $RemoteBridge 'local_bridge.py' }
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
  $bridgePath = (Join-Path $RemoteBridge 'local_bridge.py')
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($bridgePath) } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

  Start-Sleep -Milliseconds 500
  $python = $template.python
  $out = Join-Path $RemoteBridge 'logs\birdeye.out.log'
  $err = Join-Path $RemoteBridge 'logs\birdeye.err.log'
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null
  Start-Process -FilePath $python -ArgumentList @($bridgePath) -WorkingDirectory $RemoteBridge -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
  Start-Sleep -Seconds 2
}

Write-Host "BACKUP_DIR=$BackupRoot"
Write-Host "SERVER_SHA256=$serverHash"

& (Join-Path $PackageRoot 'verify.ps1') -TargetRoot $TargetRoot
