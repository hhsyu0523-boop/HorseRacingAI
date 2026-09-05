$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$root = (Get-Location).Path
$logDir = Join-Path $root 'outputs\automation'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$log = Join-Path $logDir ("autorun_$stamp.log")
$status = Join-Path $logDir 'LATEST_STATUS.txt'
$py64 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
$py32 = Join-Path $root '.runtime_python312_x86\python.exe'
$git = (Get-Command git.exe -ErrorAction Stop).Source

function Run-Step([string]$name, [string]$exe, [string[]]$argv) {
  "[$(Get-Date -Format o)] START $name" | Tee-Object -FilePath $log -Append
  & $exe @argv 2>&1 | Tee-Object -FilePath $log -Append
  if ($LASTEXITCODE -ne 0) { throw "$name failed exit=$LASTEXITCODE" }
  "[$(Get-Date -Format o)] OK $name" | Tee-Object -FilePath $log -Append
}

function Publish-AutomationState([string]$message) {
  & $git add -f 'outputs/automation/*.log' 'outputs/automation/LATEST_STATUS.txt' 2>&1 | Tee-Object -FilePath $log -Append
  & $git diff --cached --quiet
  if ($LASTEXITCODE -ne 0) {
    & $git -c user.name='HorseRacingAI Automation' -c user.email='actions@local' commit -m $message 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
    & $git push origin HEAD:main 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw 'git push failed' }
  }
}

try {
  "RUNNING $(Get-Date -Format o) step=bootstrap" | Set-Content -Encoding UTF8 $status

  # Synchronize the branch itself before any automated commit. The previous implementation
  # only fetched and checked out files from origin/main, leaving local main behind and causing
  # non-fast-forward push failures that also prevented FAILED logs from reaching GitHub.
  Run-Step 'git_fetch' $git @('fetch','origin','main')
  Run-Step 'git_sync_main' $git @('pull','--rebase','--autostash','origin','main')
  Run-Step 'git_checkout_automation' $git @('checkout','origin/main','--','automation','scripts')

  # Publish a RUNNING checkpoint before the expensive evaluation so remote monitoring can
  # distinguish "task never started" from "task started but is still/was stuck evaluating".
  "RUNNING $(Get-Date -Format o) step=holdout_evaluation" | Set-Content -Encoding UTF8 $status
  Publish-AutomationState "HorseRacingAI automation running $stamp"

  if (!(Test-Path $py64)) { throw "Python311 missing: $py64" }
  if (!(Test-Path $py32)) { throw "JV-Link x86 Python missing: $py32" }

  # Current validated baseline pipeline. JV-Link refresh is deliberately separate until
  # its incremental date-range command is fixed; do not redownload five years every run.
  Run-Step 'holdout_evaluation' $py64 @('scripts\evaluate_5year_featurehistory.py')

  # Persist baseline outputs together with the detailed execution log.
  & $git add -f 'outputs/baseline/*.json' 'outputs/baseline/*.txt' 2>&1 | Tee-Object -FilePath $log -Append
  "SUCCESS $(Get-Date -Format o) step=complete" | Set-Content -Encoding UTF8 $status
  Publish-AutomationState "HorseRacingAI automation success $stamp"
  exit 0
} catch {
  $msg = "FAILED $(Get-Date -Format o) step=automation error=$($_.Exception.Message)"
  $msg | Tee-Object -FilePath $log -Append | Set-Content -Encoding UTF8 $status
  try {
    # Refresh remote state before publishing a failure, then rebase any local automation commit.
    & $git fetch origin main 2>&1 | Tee-Object -FilePath $log -Append
    & $git pull --rebase --autostash origin main 2>&1 | Tee-Object -FilePath $log -Append
    Publish-AutomationState "HorseRacingAI automation failure $stamp"
  } catch {
    "[$(Get-Date -Format o)] FAILED_TO_PUBLISH $($_.Exception.Message)" | Tee-Object -FilePath $log -Append
  }
  exit 1
}
