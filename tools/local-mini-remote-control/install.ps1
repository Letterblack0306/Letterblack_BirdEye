param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP',
  [string]$Roots = '',
  [string]$PythonExe = '',
  [switch]$AllowAllRoots,
  [switch]$NoRestart,
  [switch]$NoTunnelRestart
)

$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupRoot = Join-Path $TargetRoot ("backups\remote-control-" + $Stamp)

function Test-PythonRuntime([string]$Candidate) {
  if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
  if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { return $false }
  & $Candidate -c "import mcp, uvicorn, starlette" *> $null
  return ($LASTEXITCODE -eq 0)
}

function Resolve-PythonRuntime {
  param([string]$Explicit, [string]$ExistingConfigPath)

  $candidates = New-Object System.Collections.Generic.List[string]
  if (-not [string]::IsNullOrWhiteSpace($Explicit)) { $candidates.Add($Explicit) }

  if (Test-Path -LiteralPath $ExistingConfigPath) {
    try {
      $existing = Get-Content -LiteralPath $ExistingConfigPath -Raw | ConvertFrom-Json
      if ($existing.python) { $candidates.Add([string]$existing.python) }
    } catch {}
  }

  foreach ($name in @('python.exe','python')) {
    try {
      $cmd = Get-Command $name -ErrorAction Stop
      if ($cmd.Source) { $candidates.Add([string]$cmd.Source) }
    } catch {}
  }

  try {
    $py = Get-Command py.exe -ErrorAction Stop
    foreach ($selector in @('-3.13','-3.12','-3.11','-3.10','-3')) {
      try {
        $resolved = (& $py.Source $selector -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
        if ($resolved) { $candidates.Add([string]$resolved) }
      } catch {}
    }
  } catch {}

  foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (Test-PythonRuntime $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  throw 'No Python runtime with mcp, uvicorn, and starlette is available. Pass -PythonExe explicitly.'
}

if ([string]::IsNullOrWhiteSpace($Roots)) {
  $Roots = $TargetRoot
}
if ($Roots -eq '*' -and -not $AllowAllRoots) {
  throw "Roots='*' requires explicit -AllowAllRoots. Default scope is the target runtime only."
}

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
New-Item -ItemType Directory -Force -Path $RemoteBridge | Out-Null

$configPath = Join-Path $RemoteBridge 'bridge_config.json'
$resolvedPython = Resolve-PythonRuntime -Explicit $PythonExe -ExistingConfigPath $configPath

$deploy = @(
  @{ Rel = 'mini_local_mcp.py'; Src = Join-Path $PackageRoot 'mini_local_mcp.py'; Dst = Join-Path $TargetRoot 'mini_local_mcp.py' },
  @{ Rel = 'remote_bridge\local_bridge.py'; Src = Join-Path $PackageRoot 'remote_bridge\local_bridge.py'; Dst = Join-Path $RemoteBridge 'local_bridge.py' }
)

$records = @()
foreach ($item in $deploy) {
  $existed = Test-Path -LiteralPath $item.Dst -PathType Leaf
  if ($existed) {
    $backupPath = Join-Path $BackupRoot $item.Rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupPath) | Out-Null
    Copy-Item -LiteralPath $item.Dst -Destination $backupPath -Force
  }
  $records += [pscustomobject]@{ relative_path = $item.Rel; existed = [bool]$existed }
  Copy-Item -LiteralPath $item.Src -Destination $item.Dst -Force
}

$configRel = 'remote_bridge\bridge_config.json'
$configExisted = Test-Path -LiteralPath $configPath -PathType Leaf
if ($configExisted) {
  $backupConfig = Join-Path $BackupRoot $configRel
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupConfig) | Out-Null
  Copy-Item -LiteralPath $configPath -Destination $backupConfig -Force
}
$records += [pscustomobject]@{ relative_path = $configRel; existed = [bool]$configExisted }

[pscustomobject]@{
  schema_version = 1
  target_root = $TargetRoot
  files = $records
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $BackupRoot 'backup_manifest.json') -Encoding UTF8

$serverPath = Join-Path $TargetRoot 'mini_local_mcp.py'
$serverHash = (Get-FileHash -LiteralPath $serverPath -Algorithm SHA256).Hash.ToLowerInvariant()
$template = Get-Content -LiteralPath (Join-Path $PackageRoot 'remote_bridge\bridge_config.template.json') -Raw | ConvertFrom-Json
$template.server_sha256 = $serverHash
$template.server = $serverPath
$template.python = $resolvedPython
$template.roots = $Roots
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
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

  Remove-Item Env:LOCAL_MINI_REMOTE_WRITE -ErrorAction SilentlyContinue
  Remove-Item Env:LOCAL_MINI_REMOTE_EXEC -ErrorAction SilentlyContinue

  $out = Join-Path $RemoteBridge 'logs\birdeye.out.log'
  $err = Join-Path $RemoteBridge 'logs\birdeye.err.log'
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null

  $quotedBridgePath = '"{0}"' -f $bridgePath
  Start-Process -FilePath $resolvedPython -ArgumentList @($quotedBridgePath) -WorkingDirectory $RemoteBridge -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err

  $headers = @{ Authorization = "Bearer $bridgeToken" }
  $health = $null
  $lastError = $null
  for ($attempt = 1; $attempt -le 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
      $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -Headers $headers -Method Get -TimeoutSec 3
      if ($health.ok -eq $true) { break }
    } catch { $lastError = $_ }
  }

  if ($null -eq $health -or $health.ok -ne $true) {
    throw "Authenticated bridge health check failed after restart. Last error: $lastError"
  }
  if ($health.write_enabled -ne $true) { throw 'Authenticated bridge health check reports write_enabled=false.' }
  if ($health.exec_enabled -ne $true) { throw 'Authenticated bridge health check reports exec_enabled=false.' }

  Write-Host 'BRIDGE_HEALTH=PASS'
  Write-Host "BRIDGE_WRITE_ENABLED=$($health.write_enabled)"
  Write-Host "BRIDGE_EXEC_ENABLED=$($health.exec_enabled)"
}

& (Join-Path $PackageRoot 'verify.ps1') -TargetRoot $TargetRoot -SkipLive:$NoRestart

if (-not $NoRestart -and -not $NoTunnelRestart) {
  & (Join-Path $PackageRoot 'refresh_connector.ps1') -TargetRoot $TargetRoot
}

Write-Host "BACKUP_DIR=$BackupRoot"
Write-Host "SERVER_SHA256=$serverHash"
Write-Host "PYTHON=$resolvedPython"
Write-Host "ROOTS=$Roots"
