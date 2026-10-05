param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP',
  [string]$Profile = 'local-mini'
)

$ErrorActionPreference = 'Stop'

$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'
$ConfigPath = Join-Path $RemoteBridge 'bridge_config.json'
$TunnelExe = Join-Path $env:LOCALAPPDATA 'tunnel-client\bin\tunnel-client.exe'
$LogDir = Join-Path $RemoteBridge 'logs'

if (-not (Test-Path -LiteralPath $TunnelExe)) {
  throw "tunnel-client not found: $TunnelExe"
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "bridge config not found: $ConfigPath"
}

$token = [Environment]::GetEnvironmentVariable('LOCAL_MINI_BRIDGE_TOKEN', 'User')
if ([string]::IsNullOrWhiteSpace($token)) {
  $token = $env:LOCAL_MINI_BRIDGE_TOKEN
}
if ([string]::IsNullOrWhiteSpace($token)) {
  throw 'LOCAL_MINI_BRIDGE_TOKEN is not available.'
}

$env:LOCAL_MINI_BRIDGE_TOKEN = $token
$env:CONTROL_PLANE_API_KEY = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($env:CONTROL_PLANE_API_KEY)) {
  throw 'CONTROL_PLANE_API_KEY is not available in the User environment.'
}
$env:MCP_EXTRA_HEADERS = "Authorization: Bearer $token"
$env:MCP_DISCOVERY_EXTRA_HEADERS = $env:MCP_EXTRA_HEADERS

$headers = @{ Authorization = "Bearer $token" }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -Headers $headers -TimeoutSec 5
if ($health.ok -ne $true) {
  throw 'Bridge health failed before connector refresh.'
}

& $TunnelExe doctor --profile $Profile --explain
if ($LASTEXITCODE -ne 0) {
  throw "tunnel-client doctor failed for profile $Profile"
}

Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match ('run.+--profile.+' + [regex]::Escape($Profile)) } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Start-Process -FilePath $TunnelExe -ArgumentList @('run','--profile',$Profile) -RedirectStandardOutput (Join-Path $LogDir 'tunnel.out.log') -RedirectStandardError (Join-Path $LogDir 'tunnel.err.log') -WindowStyle Hidden

$ready = $null
for ($attempt = 1; $attempt -le 20; $attempt++) {
  Start-Sleep -Milliseconds 500
  try {
    $ready = Invoke-WebRequest 'http://127.0.0.1:8080/readyz' -UseBasicParsing -TimeoutSec 3
    if ($ready.StatusCode -eq 200) { break }
  } catch {}
}

if ($null -eq $ready -or $ready.StatusCode -ne 200) {
  throw 'Tunnel did not become ready after restart.'
}

Write-Host 'CONNECTOR_REFRESH=PASS'
Write-Host "TUNNEL_READY=$($ready.Content.Trim())"
