param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP',
  [string]$Profile = 'local-mini',
  [string]$HealthListenAddr = '127.0.0.1:8767'
)

$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Refresh = Join-Path $PackageRoot 'refresh_connector.ps1'
$LogDir = Join-Path $TargetRoot 'remote_bridge\logs'

function Get-ProfileTunnelProcess([string]$ProfileName) {
  $profilePattern = [regex]::Escape($ProfileName)
  return @(
    Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" -ErrorAction SilentlyContinue |
      Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match ('run.+--profile\s+["'']?' + $profilePattern + '(["'']?|\s|$)')
      }
  )
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Refresh owns the profile-specific doctor/start sequence. This wrapper does
# not retry in-process: Task Scheduler handles bounded restart-on-failure,
# preventing competing tunnel clients or an unbounded retry loop.
& $Refresh -TargetRoot $TargetRoot -Profile $Profile -HealthListenAddr $HealthListenAddr
if ($LASTEXITCODE -ne 0) {
  throw "connector refresh exited with code $LASTEXITCODE"
}

$processes = @(Get-ProfileTunnelProcess $Profile)
if ($processes.Count -ne 1) {
  throw "expected one tunnel-client process for profile $Profile; found $($processes.Count)"
}

Wait-Process -Id $processes[0].ProcessId
throw "tunnel-client profile $Profile exited unexpectedly"