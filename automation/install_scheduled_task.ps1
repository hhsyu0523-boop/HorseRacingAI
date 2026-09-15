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
# retry at the next user logon. The runner gate prevents duplicate healthy runs.
$logonTrigger=New-ScheduledTaskTrigger -AtLogOn
# Daily safety-net: if the Sunday run was missed and there was no logon event,
# give the runner one deterministic recovery opportunity at 07:05. The gate only
# runs the pipeline when the required Sunday SUCCESS is still missing.
$dailyRecoveryTrigger=New-ScheduledTaskTrigger -Daily -At 7:05AM
$triggers=@($weeklyTrigger,$logonTrigger,$dailyRecoveryTrigger)

$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers -Settings $settings -Description 'HorseRacingAI unattended evaluation and GitHub status upload; Sunday 20:30 plus logon and daily 07:05 recovery' -Force | Out-Null
Write-Host "INSTALLED: $task / Sunday 20:30 / AtLogOn recovery / daily 07:05 safety-net / StartWhenAvailable / WakeToRun"
Write-Host "Runner: $runner"
