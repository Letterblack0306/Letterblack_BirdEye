param(
  [int]$Port = 7726
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Extension = Join-Path $Root "extension"

Write-Host "Starting BirdEye browser relay on 127.0.0.1:$Port"
Write-Host "Chrome remote debugging/CDP is NOT used."

Start-Process python.exe -ArgumentList @(
  "$Root\server.py",
  "--host", "127.0.0.1",
  "--port", "$Port"
)

Start-Sleep -Milliseconds 700

$health = Invoke-RestMethod "http://127.0.0.1:$Port/health"
if (-not $health.ok) {
  throw "Relay health check failed."
}

Write-Host ""
Write-Host "Relay is running."
Write-Host "Load this folder as an unpacked Chrome extension:"
Write-Host "  $Extension"
Write-Host ""
Write-Host "Chrome: chrome://extensions"
Write-Host "Enable Developer mode -> Load unpacked -> select the extension folder."
Write-Host ""
Write-Host "No Chrome debug port is required."
