$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$root = (Resolve-Path $root).Path
$git = (Get-Command git.exe -ErrorAction Stop).Source
$py64 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
$productionDb = Join-Path $root 'database\horse_racing.db'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
# Use a unique worktree per run. Reusing one fixed directory can fail before logging
# when a previous run was interrupted or the old worktree is still registered.
$autoRoot = Join-Path $env:LOCALAPPDATA ("HorseRacingAI_AutomationWorktree_" + $stamp)
$rootLogDir = Join-Path $root 'outputs\automation'
New-Item -ItemType Directory -Force -Path $rootLogDir | Out-Null
$rootStatus = Join-Path $rootLogDir 'LATEST_LOCAL_STATUS.txt'
$rootLog = Join-Path $rootLogDir ("bootstrap_$stamp.log")
$worktreeReady = $false

function Root-Log([string]$text) {
  $text | Tee-Object -FilePath $rootLog -Append
}
function Invoke-Git([string[]]$argv, [string]$cwd=$root) {
  Push-Location $cwd
  try {
    Root-Log "[$(Get-Date -Format o)] git $($argv -join ' ')"
    & $git @argv 2>&1 | Tee-Object -FilePath $rootLog -Append
    if ($LASTEXITCODE -ne 0) { throw "git $($argv -join ' ') failed exit=$LASTEXITCODE" }
  } finally { Pop-Location }
}

try {
  "RUNNING $(Get-Date -Format o) step=bootstrap" | Set-Content -Encoding UTF8 $rootStatus

  # IMPORTANT: Never pull/rebase/stash/reset the user's main research worktree.
  # It may contain legitimate uncommitted experiments. All unattended work runs in
  # a unique clean detached worktree created from origin/main.
  Invoke-Git @('fetch','origin','main')
  Invoke-Git @('worktree','prune')
  Invoke-Git @('worktree','add','--force','--detach',$autoRoot,'origin/main')
  $worktreeReady = $true

  Set-Location $autoRoot
  $logDir = Join-Path $autoRoot 'outputs\automation'
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $log = Join-Path $logDir ("autorun_$stamp.log")
  $status = Join-Path $logDir 'LATEST_STATUS.txt'

  function Log([string]$text) {
    $text | Tee-Object -FilePath $log -Append
    Root-Log $text
  }
  function Run-Step([string]$name, [string]$exe, [string[]]$argv) {
    Log "[$(Get-Date -Format o)] START $name"
    & $exe @argv 2>&1 | Tee-Object -FilePath $log -Append | Tee-Object -FilePath $rootLog -Append
    if ($LASTEXITCODE -ne 0) { throw "$name failed exit=$LASTEXITCODE" }
    Log "[$(Get-Date -Format o)] OK $name"
  }
  function Publish([string]$message) {
    & $git add -f 'outputs/automation/*.log' 'outputs/automation/LATEST_STATUS.txt' 'outputs/baseline/*.json' 'outputs/baseline/*.txt' 2>&1 | Tee-Object -FilePath $log -Append
    & $git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) { return }
    & $git -c user.name='HorseRacingAI Automation' -c user.email='actions@local' commit -m $message 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
    & $git push origin HEAD:main 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
      & $git pull --rebase origin main 2>&1 | Tee-Object -FilePath $log -Append
      if ($LASTEXITCODE -ne 0) { throw 'automation worktree rebase failed' }
      & $git push origin HEAD:main 2>&1 | Tee-Object -FilePath $log -Append
      if ($LASTEXITCODE -ne 0) { throw 'git push failed after retry' }
    }
  }

  "RUNNING $(Get-Date -Format o) step=validation isolated_worktree=$autoRoot" | Set-Content -Encoding UTF8 $status
  "RUNNING $(Get-Date -Format o) step=validation" | Set-Content -Encoding UTF8 $rootStatus
  if (!(Test-Path $py64)) { throw "Python311 missing: $py64" }
  if (!(Test-Path $productionDb)) { throw "production DB missing: $productionDb" }

  $env:HORSE_RACING_DB_PATH = $productionDb
  Publish "HorseRacingAI automation running $stamp"
  Run-Step 'holdout_evaluation' $py64 @('scripts\evaluate_5year_featurehistory.py')

  "SUCCESS $(Get-Date -Format o) step=complete" | Set-Content -Encoding UTF8 $status
  "SUCCESS $(Get-Date -Format o) step=complete" | Set-Content -Encoding UTF8 $rootStatus
  Publish "HorseRacingAI automation success $stamp"
  exit 0
} catch {
  $msg = "FAILED $(Get-Date -Format o) step=automation error=$($_.Exception.Message)"
  $msg | Set-Content -Encoding UTF8 $rootStatus
  Root-Log $msg
  if ($worktreeReady -and (Test-Path $autoRoot)) {
    try {
      Set-Location $autoRoot
      $logDir = Join-Path $autoRoot 'outputs\automation'
      New-Item -ItemType Directory -Force -Path $logDir | Out-Null
      $log = Join-Path $logDir ("autorun_$stamp.log")
      $status = Join-Path $logDir 'LATEST_STATUS.txt'
      $msg | Set-Content -Encoding UTF8 $status
      Root-Log "Attempting remote failure publish"
      & $git add -f 'outputs/automation/*.log' 'outputs/automation/LATEST_STATUS.txt'
      & $git -c user.name='HorseRacingAI Automation' -c user.email='actions@local' commit -m "HorseRacingAI automation failure $stamp"
      & $git push origin HEAD:main
    } catch { Root-Log "FAILED_TO_PUBLISH $($_.Exception.Message)" }
  }
  exit 1
} finally {
  # Cleanup only the isolated automation worktree. Never touch the user's research tree.
  try {
    Set-Location $root
    if ($worktreeReady) { & $git worktree remove --force $autoRoot 2>&1 | Out-Null }
    & $git worktree prune 2>&1 | Out-Null
  } catch {}
}
