$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$root=(Get-Location).Path
$runner=Join-Path $root 'automation\horse_ai_autorun.ps1'
if (!(Test-Path $runner)) { throw "runner missing: $runner" }
$task='HorseRacingAI-Auto'
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""

# Primary weekly run: Sunday 20:30.
$weeklyTrigger=New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 8:30PM
# Recovery trigger: if Windows was off/asleep or Task Scheduler missed the weekly launch,
# start the same runner at the next user logon. The runner is idempotent at the output level.
$logonTrigger=New-ScheduledTaskTrigger -AtLogOn
$triggers=@($weeklyTrigger,$logonTrigger)

$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers -Settings $settings -Description 'HorseRacingAI unattended evaluation and GitHub status upload; Sunday 20:30 plus logon recovery' -Force | Out-Null
Write-Host "INSTALLED: $task / Sunday 20:30 / StartWhenAvailable / WakeToRun / AtLogOn recovery"
Write-Host "Runner: $runner"
