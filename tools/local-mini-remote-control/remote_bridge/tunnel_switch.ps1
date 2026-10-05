param(
    [Parameter(Position = 0)]
    [ValidateSet("open", "close", "status")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"

$root = "C:\MCP Local\Local_Mini_MCP"
$remote = Join-Path $root "remote_bridge"
$exe = "$env:LOCALAPPDATA\tunnel-client\bin\tunnel-client.exe"
$configPath = Join-Path $remote "bridge_config.json"
$bridgeScript = Join-Path $remote "start_bridge.ps1"

function Test-Port([int]$Port) {
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Stop-Tunnel {
    Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Action -eq "close") {
    Stop-Tunnel
    Write-Host "TUNNEL_CLOSED=PASS"
    exit 0
}

if ($Action -eq "status") {
    $ready = $false
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:8080/readyz" -UseBasicParsing -TimeoutSec 3
        $ready = ($r.StatusCode -eq 200)
    } catch {}
    Write-Host "BRIDGE_LISTENING=$(Test-Port 8765)"
    Write-Host "TUNNEL_READY=$ready"
    exit 0
}

$env:CONTROL_PLANE_TUNNEL_ID = [Environment]::GetEnvironmentVariable("CONTROL_PLANE_TUNNEL_ID", "User")
$env:CONTROL_PLANE_API_KEY = [Environment]::GetEnvironmentVariable("CONTROL_PLANE_API_KEY", "User")
$token = [Environment]::GetEnvironmentVariable("LOCAL_MINI_BRIDGE_TOKEN", "User")

if (-not $env:CONTROL_PLANE_TUNNEL_ID) { throw "CONTROL_PLANE_TUNNEL_ID missing" }
if (-not $env:CONTROL_PLANE_API_KEY) { throw "CONTROL_PLANE_API_KEY missing" }
if (-not $token) { throw "LOCAL_MINI_BRIDGE_TOKEN missing" }

$env:LOCAL_MINI_BRIDGE_TOKEN = $token
$env:MCP_EXTRA_HEADERS = "Authorization: Bearer $token"
$env:MCP_DISCOVERY_EXTRA_HEADERS = $env:MCP_EXTRA_HEADERS

if (-not (Test-Path -LiteralPath $configPath)) { throw "bridge config missing: $configPath" }
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json

$expectedHash = ([string]$config.server_sha256).ToLowerInvariant()
$actualHash = (Get-FileHash -LiteralPath ([string]$config.server) -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
    throw "SERVER_HASH_MISMATCH expected=$expectedHash actual=$actualHash"
}

# Never inject stale read-only overrides.
Remove-Item Env:LOCAL_MINI_REMOTE_WRITE -ErrorAction SilentlyContinue
Remove-Item Env:LOCAL_MINI_REMOTE_EXEC -ErrorAction SilentlyContinue

if (-not (Test-Port 8765)) {
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        ('"{0}"' -f $bridgeScript)
    )
    Start-Sleep -Seconds 5
}

$headers = @{ Authorization = "Bearer $token" }
$health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -Headers $headers -TimeoutSec 5
if ($health.ok -ne $true) { throw "Bridge health returned ok=false" }
if ($health.write_enabled -ne $true) { throw "Bridge health reports write_enabled=false" }
if ($health.exec_enabled -ne $true) { throw "Bridge health reports exec_enabled=false" }

Stop-Tunnel
Start-Sleep -Seconds 1

& (Join-Path $remote "connect_tunnel.ps1")
if ($LASTEXITCODE -ne 0) { throw "connect_tunnel.ps1 failed" }

Write-Host "TUNNEL_OPEN=PASS"
