param(
  [string]$TargetRoot = 'C:\MCP Local\Local_Mini_MCP',
  [string]$Profile = 'local-mini',
  [string]$HealthListenAddr = '127.0.0.1:8767'
)

$ErrorActionPreference = 'Stop'

$RemoteBridge = Join-Path $TargetRoot 'remote_bridge'
$ConfigPath = Join-Path $RemoteBridge 'bridge_config.json'
$TunnelExe = Join-Path $env:LOCALAPPDATA 'tunnel-client\bin\tunnel-client.exe'
$LogDir = Join-Path $RemoteBridge 'logs'
$CredFile = Join-Path $RemoteBridge 'tunnel_credentials.env'

function Read-CredentialFallback([string]$Name) {
  if (-not (Test-Path -LiteralPath $CredFile -PathType Leaf)) {
    return $null
  }

  foreach ($line in Get-Content -LiteralPath $CredFile) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.+?)\s*$') {
      if ($Matches[1] -eq $Name) {
        $value = $Matches[2].Trim('"', "'")
        if (-not [string]::IsNullOrWhiteSpace($value)) {
          return $value
        }
      }
    }
  }

  return $null
}

function Get-ProfileTunnelProcesses([string]$ProfileName) {
  $profilePattern = [regex]::Escape($ProfileName)
  return @(
    Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" -ErrorAction SilentlyContinue |
      Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match ('run.+--profile\s+["'']?' + $profilePattern + '(["'']?|\s|$)')
      }
  )
}

function Get-TemporaryLoopbackListenAddr {
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $listener.Start()
  try {
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
  } finally {
    $listener.Stop()
  }
  return "127.0.0.1:$port"
}

function Get-HealthListener([string]$ListenAddr) {
  $separator = $ListenAddr.LastIndexOf(':')
  if ($separator -lt 1) {
    throw "Invalid health listen address: $ListenAddr"
  }

  $hostName = $ListenAddr.Substring(0, $separator)
  $portText = $ListenAddr.Substring($separator + 1)
  $port = 0
  if (-not [int]::TryParse($portText, [ref]$port)) {
    throw "Invalid health listen port: $ListenAddr"
  }

  return @(
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
      Where-Object {
        $_.LocalAddress -eq $hostName -or
        ($hostName -eq '127.0.0.1' -and $_.LocalAddress -eq '0.0.0.0')
      }
  )
}

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

$runtimeKey = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY', 'User')
if ([string]::IsNullOrWhiteSpace($runtimeKey)) {
  $runtimeKey = $env:CONTROL_PLANE_API_KEY
}
if ([string]::IsNullOrWhiteSpace($runtimeKey)) {
  $runtimeKey = Read-CredentialFallback 'CONTROL_PLANE_API_KEY'
}
if ([string]::IsNullOrWhiteSpace($runtimeKey)) {
  throw 'CONTROL_PLANE_API_KEY is unavailable in User/process environment and tunnel_credentials.env.'
}

$env:LOCAL_MINI_BRIDGE_TOKEN = $token
$env:CONTROL_PLANE_API_KEY = $runtimeKey
$env:MCP_EXTRA_HEADERS = "Authorization: Bearer $token"
$env:MCP_DISCOVERY_EXTRA_HEADERS = $env:MCP_EXTRA_HEADERS

$headers = @{ Authorization = "Bearer $token" }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -Headers $headers -TimeoutSec 5
if ($health.ok -ne $true) {
  throw 'Bridge health failed before connector refresh.'
}

$profileProcesses = @(Get-ProfileTunnelProcesses $Profile)
$healthListeners = @(Get-HealthListener $HealthListenAddr)
$doctorHealthListenAddr = $HealthListenAddr

if ($healthListeners.Count -gt 0) {
  $profilePids = @($profileProcesses | ForEach-Object { [int]$_.ProcessId })
  $foreignListeners = @($healthListeners | Where-Object { [int]$_.OwningProcess -notin $profilePids })

  if ($foreignListeners.Count -gt 0) {
    $foreignPids = ($foreignListeners | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique) -join ','
    throw "Health listen address $HealthListenAddr is occupied by unrelated process PID(s): $foreignPids"
  }

  # The requested health port is already held by the same profile. Validate the
  # profile on a temporary loopback port so repeated refreshes are idempotent
  # and do not fail before the existing tunnel can be restarted.
  $doctorHealthListenAddr = Get-TemporaryLoopbackListenAddr
  Write-Host "DOCTOR_HEALTH_LISTEN_ADDR=$doctorHealthListenAddr"
}

& $TunnelExe doctor --profile $Profile --health.listen-addr $doctorHealthListenAddr --explain
if ($LASTEXITCODE -ne 0) {
  throw "tunnel-client doctor failed for profile $Profile"
}

# Stop only the tunnel process(es) for this profile after doctor has passed.
# If doctor fails, the currently working tunnel stays untouched.
@(Get-ProfileTunnelProcesses $Profile) |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

for ($attempt = 1; $attempt -le 20; $attempt++) {
  Start-Sleep -Milliseconds 100
  if (@(Get-HealthListener $HealthListenAddr).Count -eq 0) {
    break
  }
}

if (@(Get-HealthListener $HealthListenAddr).Count -gt 0) {
  throw "Health listen address $HealthListenAddr did not become free after stopping profile $Profile."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Start-Process -FilePath $TunnelExe -ArgumentList @('run','--profile',$Profile,'--health.listen-addr',$HealthListenAddr) -RedirectStandardOutput (Join-Path $LogDir 'tunnel.out.log') -RedirectStandardError (Join-Path $LogDir 'tunnel.err.log') -WindowStyle Hidden

$ready = $null
for ($attempt = 1; $attempt -le 20; $attempt++) {
  Start-Sleep -Milliseconds 500
  $baseUrl = 'http://' + $HealthListenAddr
  try {
    $ready = Invoke-WebRequest ($baseUrl + '/readyz') -UseBasicParsing -TimeoutSec 3
    if ($ready.StatusCode -eq 200) { break }
  } catch {}
}

if ($null -eq $ready -or $ready.StatusCode -ne 200) {
  throw 'Tunnel did not become ready after restart.'
}

Write-Host 'CONNECTOR_REFRESH=PASS'
Write-Host "TUNNEL_READY=$($ready.Content.Trim())"
