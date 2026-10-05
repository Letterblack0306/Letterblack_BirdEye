$ErrorActionPreference = "Stop"

$root = "C:\MCP Local\Local_Mini_MCP"
$python = "C:\Users\prave\AppData\Local\Programs\Python\Python310\python.exe"
$remote = Join-Path $root "remote_bridge"
$bridge = Join-Path $remote "local_bridge.py"
$configPath = Join-Path $remote "bridge_config.json"

if (-not (Test-Path -LiteralPath $python)) { throw "python not found: $python" }
if (-not (Test-Path -LiteralPath $bridge)) { throw "bridge not found: $bridge" }
if (-not (Test-Path -LiteralPath $configPath)) { throw "bridge config not found: $configPath" }

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json

# bridge_config.json is authoritative. Remove stale environment overrides.
Remove-Item Env:LOCAL_MINI_REMOTE_WRITE -ErrorAction SilentlyContinue
Remove-Item Env:LOCAL_MINI_REMOTE_EXEC -ErrorAction SilentlyContinue
$env:MINI_MCP_ROOTS = [string]$config.roots

$userToken = [Environment]::GetEnvironmentVariable("LOCAL_MINI_BRIDGE_TOKEN", "User")
if (-not [string]::IsNullOrWhiteSpace($userToken)) {
    $env:LOCAL_MINI_BRIDGE_TOKEN = $userToken
}
if ([string]::IsNullOrWhiteSpace($env:LOCAL_MINI_BRIDGE_TOKEN)) {
    throw "LOCAL_MINI_BRIDGE_TOKEN is not set in the current process or User environment."
}

Write-Host "[bridge] config write_enabled=$($config.write_enabled)"
Write-Host "[bridge] config exec_enabled=$($config.exec_enabled)"

& $python $bridge
