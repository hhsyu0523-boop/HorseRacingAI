param([switch]$Elevated)

$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$root=(Get-Location).Path
$localLogDir=Join-Path $root 'outputs\automation'
New-Item -ItemType Directory -Force -Path $localLogDir | Out-Null
$recoveryStatus=Join-Path $localLogDir 'LATEST_RECOVERY_STATUS.txt'

# Register-ScheduledTask can require elevation, especially when replacing an
# existing task created with higher privileges. Relaunch once with UAC and make
# the result visible to the parent shell instead of returning silently.
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  if ($Elevated) { throw 'Administrator elevation was requested but is not active.' }
  Write-Host 'Administrator permission is required to repair HorseRacingAI-Auto.'
  Write-Host 'A Windows UAC confirmation will open. Choose Yes.'
  $psExe=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  $argLine='-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Elevated'
  try {
    $p=Start-Process -FilePath $psExe -Verb RunAs -ArgumentList $argLine -WorkingDirectory $root -Wait -PassThru
  } catch {
    Write-Error ('UAC elevation failed or was cancelled: ' + $_.Exception.Message)
    exit 1
  }
  Write-Host ('ELEVATED_CHILD_EXIT=' + $p.ExitCode)
  if ($p.ExitCode -ne 0) {
    if (Test-Path $recoveryStatus) { Get-Content $recoveryStatus }
    throw ('Elevated recovery failed with exit code ' + $p.ExitCode)
  }
  if (Test-Path $recoveryStatus) {
    $summary=(Get-Content $recoveryStatus -Raw).Trim()
    Write-Host $summary
    if ($summary -notmatch '^RECOVERY_OK ') { throw ('Elevated recovery did not report RECOVERY_OK: ' + $summary) }
  } else {
    throw 'Elevated recovery exited successfully but LATEST_RECOVERY_STATUS.txt was not created.'
  }
  exit 0
}

$git=(Get-Command git.exe -ErrorAction Stop).Source
$bootstrapLog=Join-Path $localLogDir ('enable_autorun_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')

function Log([string]$text) {
  ('[' + (Get-Date -Format o) + '] ' + $text) | Tee-Object -FilePath $bootstrapLog -Append
}
function Set-RecoveryStatus([string]$text) {
  [System.IO.File]::WriteAllText($recoveryStatus,$text,[System.Text.UTF8Encoding]::new($false))
  Log $text
}

try {
  Set-RecoveryStatus ('RECOVERY_RUNNING ' + (Get-Date -Format o) + ' step=bootstrap')

  $savedErrorActionPreference=$ErrorActionPreference
  $ErrorActionPreference='Continue'
  $fetchOutput=@(& $git fetch origin main 2>&1)
  $fetchExit=$LASTEXITCODE
  $ErrorActionPreference=$savedErrorActionPreference
  $fetchOutput | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $bootstrapLog -Append
  if ($fetchExit -ne 0) { throw "git fetch failed (exit=$fetchExit)" }
  Log 'GIT_FETCH_OK'

  function Restore-FromOrigin([string]$repoPath,[string]$localPath) {
    $saved=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    $text=@(& $git show "origin/main:$repoPath" 2>&1)
    $showExit=$LASTEXITCODE
    $ErrorActionPreference=$saved
    if ($showExit -ne 0) {
      $text | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $bootstrapLog -Append | Out-Null
      throw "git show failed: $repoPath (exit=$showExit)"
    }
    $target=Join-Path $root $localPath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    [System.IO.File]::WriteAllLines($target,[string[]]$text,[System.Text.UTF8Encoding]::new($false))
    Log "REFRESHED: $localPath"
  }

  Restore-FromOrigin 'automation/horse_ai_autorun.ps1' 'automation\horse_ai_autorun.ps1'
  Restore-FromOrigin 'automation/install_scheduled_task.ps1' 'automation\install_scheduled_task.ps1'

  $savedErrorActionPreference=$ErrorActionPreference
  $ErrorActionPreference='Continue'
  $installOutput=@(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'automation\install_scheduled_task.ps1') 2>&1)
  $installExit=$LASTEXITCODE
  $ErrorActionPreference=$savedErrorActionPreference
  $installOutput | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $bootstrapLog -Append
  if ($installExit -ne 0) { throw "scheduled task install failed (exit=$installExit)" }

  $task=Get-ScheduledTask -TaskName 'HorseRacingAI-Auto' -ErrorAction Stop
  Log ('TASK_REGISTERED state=' + $task.State)

  Start-ScheduledTask -TaskName 'HorseRacingAI-Auto'
  Log 'STARTED: HorseRacingAI-Auto'

  $statusPath=Join-Path $localLogDir 'LATEST_LOCAL_STATUS.txt'
  $deadline=(Get-Date).AddSeconds(60)
  while ((Get-Date) -lt $deadline) {
    if (Test-Path $statusPath) {
      $status=(Get-Content $statusPath -Raw).Trim()
      Log ('LOCAL_STATUS: ' + $status)
      Set-RecoveryStatus ('RECOVERY_OK ' + (Get-Date -Format o) + ' local_status=' + $status)
      Write-Host $status
      Write-Host 'RECOVERY_OK: task registered and runner produced local status'
      exit 0
    }
    Start-Sleep -Seconds 2
  }

  $info=Get-ScheduledTaskInfo -TaskName 'HorseRacingAI-Auto' -ErrorAction Stop
  throw ('task started but no LATEST_LOCAL_STATUS.txt appeared within 60s; LastTaskResult=' + $info.LastTaskResult + '; LastRunTime=' + $info.LastRunTime)
}
catch {
  Set-RecoveryStatus ('RECOVERY_FAILED ' + (Get-Date -Format o) + ' error=' + $_.Exception.Message)
  try {
    $info=Get-ScheduledTaskInfo -TaskName 'HorseRacingAI-Auto' -ErrorAction Stop
    Log ('TASK_INFO LastTaskResult=' + $info.LastTaskResult + ' LastRunTime=' + $info.LastRunTime + ' NextRunTime=' + $info.NextRunTime)
  } catch {}
  Write-Error $_
  exit 1
}
