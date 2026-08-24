param(
    [string]$Workspace = (Get-Location).Path,
    [string]$StateRoot = "",
    [int]$IntervalSeconds = 300,
    [switch]$Once
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command python -ErrorAction Stop

$Arguments = @(
    (Join-Path $RepoRoot "birdeye_watch.py"),
    "--workspace", $Workspace,
    "--interval", [string][Math]::Max(1, $IntervalSeconds)
)

if ($StateRoot) {
    $Arguments += @("--state-root", $StateRoot)
}

if ($Once) {
    $Arguments += "--once"
}

& $Python.Source @Arguments
exit $LASTEXITCODE
