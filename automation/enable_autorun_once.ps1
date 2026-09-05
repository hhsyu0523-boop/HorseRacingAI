$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$root=(Get-Location).Path
Write-Host 'HorseRacingAI autorun bootstrap starting...'

# Refresh only automation/scripts from main; leave DB/runtime untouched.
& git.exe fetch origin main
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
& git.exe checkout origin/main -- automation scripts
if ($LASTEXITCODE -ne 0) { throw 'git checkout automation/scripts failed' }

# Install/update the scheduled task.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'automation\install_scheduled_task.ps1')
if ($LASTEXITCODE -ne 0) { throw 'scheduled task install failed' }

# Run once immediately so installation is verified now, not next Sunday.
Start-ScheduledTask -TaskName 'HorseRacingAI-Auto'
Write-Host 'STARTED: HorseRacingAI-Auto'
Write-Host 'The task now runs in background. Check outputs\automation\LATEST_STATUS.txt for RUNNING/SUCCESS/FAILED.'
