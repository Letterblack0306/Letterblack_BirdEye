param(
    [switch]$DoctorOnly,
    [string]$Profile = "local-mini"
)

$ErrorActionPreference = "Stop"

$exe = "$env:LOCALAPPDATA\tunnel-client\bin\tunnel-client.exe"
$root = "C:\MCP Local\Local_Mini_MCP"
$remote = Join-Path $root "remote_bridge"
$bridgeScript = Join-Path $remote "start_bridge.ps1"
$configPath = Join-Path $remote "bridge_config.json"
$mcpUrl = "http://127.0.0.1:8765/mcp"

if (-not (Test-Path -LiteralPath $exe)) { throw "tunnel-client not found at $exe" }
if (-not (Test-Path -LiteralPath $configPath)) { throw "bridge config not found: $configPath" }

$env:CONTROL_PLANE_TUNNEL_ID = [Environment]::GetEnvironmentVariable("CONTROL_PLANE_TUNNEL_ID", "User")
$env:CONTROL_PLANE_API_KEY = [Environment]::GetEnvironmentVariable("CONTROL_PLANE_API_KEY", "User")

$credFile = Join-Path $remote "tunnel_credentials.env"
if (Test-Path -LiteralPath $credFile) {
    foreach ($line in Get-Content -LiteralPath $credFile) {
        if ($line -match '^\s*([A-Z_]+)\s*=\s*(.+?)\s*$') {
            $name = $Matches[1]
            $val = $Matches[2].Trim('"', "'")
            if (-not $val) { continue }
            if ($name -eq "CONTROL_PLANE_TUNNEL_ID" -and -not $env:CONTROL_PLANE_TUNNEL_ID) {
                $env:CONTROL_PLANE_TUNNEL_ID = $val
            }
            if ($name -eq "CONTROL_PLANE_API_KEY" -and -not $env:CONTROL_PLANE_API_KEY) {
                $env:CONTROL_PLANE_API_KEY = $val
            }
        }
    }
}

if (-not $env:CONTROL_PLANE_TUNNEL_ID) { throw "CONTROL_PLANE_TUNNEL_ID not set" }
if (-not $env:CONTROL_PLANE_API_KEY) { throw "CONTROL_PLANE_API_KEY not set" }

$bridgeToken = [Environment]::GetEnvironmentVariable("LOCAL_MINI_BRIDGE_TOKEN", "User")
if (-not $bridgeToken) { throw "LOCAL_MINI_BRIDGE_TOKEN not set in User environment" }

$env:LOCAL_MINI_BRIDGE_TOKEN = $bridgeToken
$env:MCP_EXTRA_HEADERS = "Authorization: Bearer $bridgeToken"
$env:MCP_DISCOVERY_EXTRA_HEADERS = $env:MCP_EXTRA_HEADERS

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$serverPath = [string]$config.server
$expectedHash = ([string]$config.server_sha256).ToLowerInvariant()
$actualHash = (Get-FileHash -LiteralPath $serverPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
    throw "SERVER_HASH_MISMATCH expected=$expectedHash actual=$actualHash"
}

# Do not let stale process/user env values override bridge_config.json.
Remove-Item Env:LOCAL_MINI_REMOTE_WRITE -ErrorAction SilentlyContinue
Remove-Item Env:LOCAL_MINI_REMOTE_EXEC -ErrorAction SilentlyContinue

if (-not (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process powershell.exe -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        ('"{0}"' -f $bridgeScript)
    ) -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

$headers = @{ Authorization = "Bearer $bridgeToken" }
$health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -Headers $headers -TimeoutSec 5
if ($health.ok -ne $true) { throw "Bridge health returned ok=false" }
if ($health.write_enabled -ne $true) { throw "Bridge health reports write_enabled=false" }
if ($health.exec_enabled -ne $true) { throw "Bridge health reports exec_enabled=false" }

$initArgs = @(
    "init",
    "--sample",
    "sample_mcp_remote_no_auth",
    "--profile",
    $Profile,
    "--tunnel-id",
    $env:CONTROL_PLANE_TUNNEL_ID,
    "--mcp-server-url",
    $mcpUrl,
    "--force"
)
& $exe @initArgs
if ($LASTEXITCODE -ne 0) { throw "tunnel-client init failed" }

& $exe doctor --profile $Profile --explain
if ($LASTEXITCODE -ne 0) { throw "tunnel-client doctor failed" }

if ($DoctorOnly) {
    Write-Host "TUNNEL_DOCTOR=PASS"
    exit 0
}

Get-CimInstance Win32_Process -Filter "Name='tunnel-client.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'run.+--profile.+local-mini' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$logDir = Join-Path $remote "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Start-Process $exe -ArgumentList @("run","--profile",$Profile) -RedirectStandardOutput (Join-Path $logDir "tunnel.out.log") -RedirectStandardError (Join-Path $logDir "tunnel.err.log") -WindowStyle Hidden

Start-Sleep -Seconds 5
$ready = Invoke-WebRequest "http://127.0.0.1:8080/readyz" -UseBasicParsing -TimeoutSec 5
if ($ready.StatusCode -ne 200) { throw "Tunnel readyz failed" }

Write-Host "TUNNEL_RESTART=PASS"
Write-Host "TUNNEL_READY=$($ready.Content.Trim())"
