[CmdletBinding()]
param(
    [string]$TargetRoot = "C:\MCP Local\Local_Mini_MCP"
)

$ErrorActionPreference = "Stop"
$V2 = Join-Path $TargetRoot "v2"
$Server = Join-Path $V2 "mini_local_mcp_v2.py"
$Bridge = Join-Path $V2 "local_bridge_v2.py"
$Config = Join-Path $V2 "bridge_config.json"

foreach ($p in @($Server, $Bridge, $Config)) {
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
        throw "Missing required file: $p"
    }
}

$cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Server).Hash.ToLowerInvariant()

if ($cfg.expected_server_sha256 -ne $actual) {
    throw "Server hash mismatch: config=$($cfg.expected_server_sha256) actual=$actual"
}

$python = $cfg.python
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Configured Python unavailable: $python"
}

& $python -m py_compile $Server $Bridge
if ($LASTEXITCODE -ne 0) {
    throw "py_compile failed"
}

$probeScript = @"
import importlib.util, json
p = r'''$Server'''
s = importlib.util.spec_from_file_location('mini_v2', p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
print(json.dumps(m.health()))
"@

$probe = & $python -c $probeScript
if ($LASTEXITCODE -ne 0) {
    throw "health import probe failed"
}

$receipt = [ordered]@{
    verifiedAt = (Get-Date).ToUniversalTime().ToString("o")
    serverSha256 = $actual
    bridgeSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Bridge).Hash.ToLowerInvariant()
    configSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Config).Hash.ToLowerInvariant()
    pyCompile = "PASS"
    healthProbe = ($probe -join [Environment]::NewLine)
    writeEnabled = [bool]$cfg.write_enabled
    execEnabled = [bool]$cfg.exec_enabled
}
$receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $V2 "VERIFY_RECEIPT.json") -Encoding UTF8
$receipt | ConvertTo-Json -Depth 10
