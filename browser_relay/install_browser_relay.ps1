param(
  [int]$Port = 7726,
  [int]$StartupTimeoutSeconds = 10
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Extension = Join-Path $Root "extension"
$Log = Join-Path $Root "relay-startup.log"

Write-Host "Starting BirdEye browser relay on 127.0.0.1:$Port"
Write-Host "Chrome remote debugging/CDP is NOT used."

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) {
  $python = Get-Command py.exe -ErrorAction SilentlyContinue
}
if (-not $python) {
  throw "Python was not found on PATH. Run 'python --version' first."
}

if ($python.Name -eq "py.exe") {
  $arguments = @("-3", "$Root\server.py", "--host", "127.0.0.1", "--port", "$Port")
} else {
  $arguments = @("-u", "$Root\server.py", "--host", "127.0.0.1", "--port", "$Port")
}

if (Test-Path $Log) {
  Remove-Item $Log -Force
}

$process = Start-Process -FilePath $python.Source -ArgumentList $arguments -WorkingDirectory $Root -RedirectStandardOutput $Log -RedirectStandardError $Log -PassThru

$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
$healthy = $false

while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 250
  try {
    $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 1
    if ($health.ok -eq $true) {
      $healthy = $true
      break
    }
  } catch {
    if ($process.HasExited) {
      break
    }
  }
}

if (-not $healthy) {
  $logText = if (Test-Path $Log) { Get-Content $Log -Raw } else { "" }
  $details = "Startup log:" + [Environment]::NewLine + $logText
  if ($process.HasExited) {
    throw "Relay process exited with code $($process.ExitCode)." + [Environment]::NewLine + $details
  }
  throw "Relay did not become healthy within $StartupTimeoutSeconds seconds." + [Environment]::NewLine + $details
}

Write-Host ""
Write-Host "Relay is running (PID $($process.Id))."
Write-Host "Load this folder as an unpacked Chrome extension:"
Write-Host "  $Extension"
Write-Host ""
Write-Host "Chrome: chrome://extensions"
Write-Host "Enable Developer mode -> Load unpacked -> select the extension folder."
Write-Host ""
Write-Host "No Chrome debug port is required."
Write-Host "Startup log: $Log"
