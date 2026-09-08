$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$root=(Get-Location).Path
$git=(Get-Command git.exe -ErrorAction Stop).Source
$localLogDir=Join-Path $root 'outputs\automation'
New-Item -ItemType Directory -Force -Path $localLogDir | Out-Null
$bootstrapLog=Join-Path $localLogDir ('enable_autorun_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')

function Log([string]$text) {
  ('[' + (Get-Date -Format o) + '] ' + $text) | Tee-Object -FilePath $bootstrapLog -Append
}

try {
  Log 'HorseRacingAI autorun recovery starting'

  # Do not checkout/rebase/reset/stash the research worktree. It may contain
  # legitimate uncommitted experiments. Refresh only the automation files needed
  # for unattended execution, directly from origin/main, without touching the index.
  & $git fetch origin main 2>&1 | Tee-Object -FilePath $bootstrapLog -Append
  if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }

  function Restore-FromOrigin([string]$repoPath,[string]$localPath) {
    $text = & $git show "origin/main:$repoPath"
    if ($LASTEXITCODE -ne 0) { throw "git show failed: $repoPath" }
    $target = Join-Path $root $localPath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    [System.IO.File]::WriteAllLines($target, [string[]]$text, [System.Text.UTF8Encoding]::new($false))
    Log "REFRESHED: $localPath"
  }

  Restore-FromOrigin 'automation/horse_ai_autorun.ps1' 'automation\horse_ai_autorun.ps1'
  Restore-FromOrigin 'automation/install_scheduled_task.ps1' 'automation\install_scheduled_task.ps1'

  # Install/update the scheduled task from the freshly restored installer.
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'automation\install_scheduled_task.ps1') 2>&1 | Tee-Object -FilePath $bootstrapLog -Append
  if ($LASTEXITCODE -ne 0) { throw 'scheduled task install failed' }

  $task=Get-ScheduledTask -TaskName 'HorseRacingAI-Auto' -ErrorAction Stop
  Log ("TASK_REGISTERED state=" + $task.State)

  # Run once immediately so installation is verified now, not next Sunday.
  Start-ScheduledTask -TaskName 'HorseRacingAI-Auto'
  Log 'STARTED: HorseRacingAI-Auto'

  # Verify that the runner actually leaves a local status. This catches the exact
  # failure mode where Task Scheduler accepted the task but the runner never starts.
  $statusPath=Join-Path $localLogDir 'LATEST_LOCAL_STATUS.txt'
  $deadline=(Get-Date).AddSeconds(60)
  while ((Get-Date) -lt $deadline) {
    if (Test-Path $statusPath) {
      $status=(Get-Content $statusPath -Raw).Trim()
      Log ("LOCAL_STATUS: " + $status)
      Write-Host $status
      Write-Host "RECOVERY_OK: task registered and runner produced local status"
      exit 0
    }
    Start-Sleep -Seconds 2
  }

  $info=Get-ScheduledTaskInfo -TaskName 'HorseRacingAI-Auto' -ErrorAction Stop
  throw ("task started but no LATEST_LOCAL_STATUS.txt appeared within 60s; LastTaskResult=" + $info.LastTaskResult + "; LastRunTime=" + $info.LastRunTime)
}
catch {
  Log ("RECOVERY_FAILED: " + $_.Exception.Message)
  try {
    $info=Get-ScheduledTaskInfo -TaskName 'HorseRacingAI-Auto' -ErrorAction Stop
    Log ("TASK_INFO LastTaskResult=" + $info.LastTaskResult + " LastRunTime=" + $info.LastRunTime + " NextRunTime=" + $info.NextRunTime)
  } catch {}
  Write-Error $_
  exit 1
}
