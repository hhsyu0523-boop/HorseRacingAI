$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$root=(Get-Location).Path
$runner=Join-Path $root 'automation\horse_ai_autorun.ps1'
if (!(Test-Path $runner)) { throw "runner missing: $runner" }
$task='HorseRacingAI-Auto'
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
# Weekend evaluation after racing; task also runs at logon if a scheduled run was missed.
$trigger=New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 8:30PM
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -Description 'HorseRacingAI unattended evaluation and GitHub status upload' -Force | Out-Null
Write-Host "INSTALLED: $task / Sunday 20:30 / StartWhenAvailable"
Write-Host "Runner: $runner"
