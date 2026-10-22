$ErrorActionPreference = 'Stop'
$existing = @(Get-CimInstance Win32_Process -Filter "name='ssh.exe'" | Where-Object { $_.CommandLine -like '*127.0.0.1:18766:127.0.0.1:8766*' -and $_.CommandLine -like '*ubuntu@10.77.0.1*' })
if ($existing.Count -gt 0) { Write-Output "EXISTING_TUNNEL=$($existing[0].ProcessId)"; exit 0 }
$vpn = Get-Service 'WireGuardTunnel$birdeye-oracle' -ErrorAction SilentlyContinue
if (-not $vpn -or $vpn.Status -ne 'Running') { throw 'WireGuard is not running' }
if (-not (Test-NetConnection 10.77.0.1 -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue)) { throw 'Oracle VPN SSH unreachable' }
if (-not (Test-NetConnection 127.0.0.1 -Port 8766 -InformationLevel Quiet -WarningAction SilentlyContinue)) { throw 'BirdEye MCP unavailable' }
$ssh = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$key = Join-Path $env:USERPROFILE '.ssh\id_ed25519'
$args = @('-N','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=20','-o','ServerAliveCountMax=3','-i',$key,'-R','127.0.0.1:18766:127.0.0.1:8766','ubuntu@10.77.0.1')
$p = Start-Process -FilePath $ssh -ArgumentList $args -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2
if ($p.HasExited) { throw "SSH reverse tunnel exited $($p.ExitCode)" }
Write-Output "STARTED_TUNNEL=$($p.Id)"