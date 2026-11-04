# Register/update Windows scheduled task: collect best IP every 30 minutes.
# Usage (no admin needed): powershell -ExecutionPolicy Bypass -File install_task.ps1

$ErrorActionPreference = 'Stop'

$TaskName = 'CFIP-BestIP'
$Root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Bat      = Join-Path $Root 'run.bat'

if (-not (Test-Path $Bat)) { throw "run.bat not found: $Bat" }

$OldPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
schtasks /Query /TN $TaskName *> $null
$exists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $OldPref
if ($exists) {
    schtasks /Delete /TN $TaskName /F | Out-Null
    Write-Host "Removed old task $TaskName"
}

$StartTime = (Get-Date).AddMinutes(1).ToString('HH:mm')
schtasks /Create /TN $TaskName /TR "`"$Bat`"" /SC MINUTE /MO 30 /ST $StartTime /F /RL LIMITED | Out-Null

schtasks /Query /TN $TaskName /FO LIST
Write-Host ""
Write-Host "Registered: $TaskName (every 30 minutes, starting $StartTime)" -ForegroundColor Green
Write-Host "Run now : schtasks /Run /TN $TaskName"
Write-Host "Remove  : schtasks /Delete /TN $TaskName /F"