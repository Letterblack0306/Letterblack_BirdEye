[CmdletBinding(SupportsShouldProcess=$true)]
param(
    [string]$TargetRoot = "C:\MCP Local\Local_Mini_MCP",
    [switch]$EnableWrite,
    [switch]$EnableExec
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VersionRoot = Join-Path $TargetRoot "v2"
$ConfigPath = Join-Path $VersionRoot "bridge_config.json"
$ServerTarget = Join-Path $VersionRoot "mini_local_mcp_v2.py"
$BridgeTarget = Join-Path $VersionRoot "local_bridge_v2.py"
$BackupRoot = Join-Path $TargetRoot ("backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))

function Copy-IfExists([string]$Path, [string]$Dest) {
    if (Test-Path -LiteralPath $Path) {
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        Copy-Item -LiteralPath $Path -Destination $Dest -Force
    }
}

if (-not (Test-Path -LiteralPath $TargetRoot)) {
    throw "Target root does not exist: $TargetRoot"
}

if ($PSCmdlet.ShouldProcess($TargetRoot, "Install BirdEye local-mini v2 side-by-side")) {
    New-Item -ItemType Directory -Force -Path $VersionRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

    Copy-IfExists (Join-Path $TargetRoot "mini_local_mcp.py") $BackupRoot
    Copy-IfExists (Join-Path $TargetRoot "remote_bridge\bridge_config.json") $BackupRoot
    Copy-IfExists (Join-Path $TargetRoot "remote_bridge\local_bridge.py") $BackupRoot

    Copy-Item -LiteralPath (Join-Path $PackageRoot "mini_local_mcp_v2.py") -Destination $ServerTarget -Force
    Copy-Item -LiteralPath (Join-Path $PackageRoot "local_bridge_v2.py") -Destination $BridgeTarget -Force

    $cfg = Get-Content -LiteralPath (Join-Path $PackageRoot "bridge_config.example.json") -Raw | ConvertFrom-Json
    $cfg.server = $ServerTarget
    $cfg.expected_server_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ServerTarget).Hash.ToLowerInvariant()
    $cfg.write_enabled = [bool]$EnableWrite
    $cfg.exec_enabled = [bool]$EnableExec
    $cfg | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8

    $receipt = [ordered]@{
        installedAt = (Get-Date).ToUniversalTime().ToString("o")
        targetRoot = $TargetRoot
        versionRoot = $VersionRoot
        backupRoot = $BackupRoot
        serverSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ServerTarget).Hash.ToLowerInvariant()
        bridgeSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $BridgeTarget).Hash.ToLowerInvariant()
        configSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ConfigPath).Hash.ToLowerInvariant()
        writeEnabled = [bool]$EnableWrite
        execEnabled = [bool]$EnableExec
        switchedExistingRuntime = $false
    }
    $receipt | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $VersionRoot "INSTALL_RECEIPT.json") -Encoding UTF8

    Write-Host "INSTALLED side-by-side: $VersionRoot"
    Write-Host "Existing runtime was NOT replaced or stopped."
    Write-Host "Next: run verify-local.ps1, then deliberately update the service or launcher to v2."
}
