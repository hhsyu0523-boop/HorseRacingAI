$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$root=(Get-Location).Path
$git=(Get-Command git.exe -ErrorAction Stop).Source
Write-Host 'HorseRacingAI autorun bootstrap starting...'

# Do not checkout/rebase/reset the research worktree. It may legitimately contain
# uncommitted experiments. Refresh only the two automation files required to repair
# the scheduled task, directly from origin/main, without touching the index.
& $git fetch origin main
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }

function Restore-FromOrigin([string]$repoPath,[string]$localPath) {
  $text = & $git show "origin/main:$repoPath"
  if ($LASTEXITCODE -ne 0) { throw "git show failed: $repoPath" }
  $target = Join-Path $root $localPath
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
  [System.IO.File]::WriteAllLines($target, [string[]]$text, [System.Text.UTF8Encoding]::new($false))
  Write-Host "REFRESHED: $localPath"
}

Restore-FromOrigin 'automation/horse_ai_autorun.ps1' 'automation\horse_ai_autorun.ps1'
Restore-FromOrigin 'automation/install_scheduled_task.ps1' 'automation\install_scheduled_task.ps1'

# Install/update the scheduled task.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'automation\install_scheduled_task.ps1')
if ($LASTEXITCODE -ne 0) { throw 'scheduled task install failed' }

# Run once immediately so installation is verified now, not next Sunday.
Start-ScheduledTask -TaskName 'HorseRacingAI-Auto'
Write-Host 'STARTED: HorseRacingAI-Auto'
Write-Host 'Research worktree was not rebased, reset, stashed, or checked out.'
Write-Host 'The task now runs in its dedicated clean worktree. Check outputs\automation\LATEST_STATUS.txt for RUNNING/SUCCESS/FAILED.'
