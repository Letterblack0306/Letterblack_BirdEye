param([int]$Port = 7726)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "BirdEye Browser Relay"
$python = (Get-Command python.exe -ErrorAction Stop).Source

$action = New-ScheduledTaskAction -Execute $python -Argument ("-u `"$Root\server.py`" --host 127.0.0.1 --port $Port") -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType InteractiveToken -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 3650) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 1
$health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
if ($health.ok -ne $true) { throw "BirdEye relay did not become healthy." }

Write-Host "BirdEye Browser Relay installed as a per-user background task."
Write-Host "Task: $TaskName"
Write-Host "Endpoint: http://127.0.0.1:$Port"
Write-Host "CDP: disabled"
Write-Host "No terminal needs to remain open."
Write-Host "It will start automatically at Windows logon."
