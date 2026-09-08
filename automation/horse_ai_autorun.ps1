$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$root = (Resolve-Path $root).Path
$git = (Get-Command git.exe -ErrorAction Stop).Source
$py64 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
$productionDb = Join-Path $root 'database\horse_racing.db'
$autoRoot = Join-Path $env:LOCALAPPDATA 'HorseRacingAI_AutomationWorktree'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'

function Invoke-Git([string[]]$argv, [string]$cwd=$root) {
  Push-Location $cwd
  try {
    & $git @argv
    if ($LASTEXITCODE -ne 0) { throw "git $($argv -join ' ') failed exit=$LASTEXITCODE" }
  } finally { Pop-Location }
}

# IMPORTANT: Never pull/rebase/stash/reset the user's main research worktree.
# It may contain legitimate uncommitted experiments. All unattended work runs in
# a separate clean detached worktree created from origin/main.
Invoke-Git @('fetch','origin','main')
try { Invoke-Git @('worktree','remove','--force',$autoRoot) } catch {}
if (Test-Path $autoRoot) { Remove-Item -Recurse -Force $autoRoot }
Invoke-Git @('worktree','prune')
Invoke-Git @('worktree','add','--force','--detach',$autoRoot,'origin/main')

Set-Location $autoRoot
$logDir = Join-Path $autoRoot 'outputs\automation'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("autorun_$stamp.log")
$status = Join-Path $logDir 'LATEST_STATUS.txt'

function Log([string]$text) {
  $text | Tee-Object -FilePath $log -Append
}
function Run-Step([string]$name, [string]$exe, [string[]]$argv) {
  Log "[$(Get-Date -Format o)] START $name"
  & $exe @argv 2>&1 | Tee-Object -FilePath $log -Append
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
    # The automation worktree is clean and isolated, so rebasing here is safe.
    & $git pull --rebase origin main 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw 'automation worktree rebase failed' }
    & $git push origin HEAD:main 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw 'git push failed after retry' }
  }
}

try {
  "RUNNING $(Get-Date -Format o) step=bootstrap isolated_worktree=$autoRoot" | Set-Content -Encoding UTF8 $status
  if (!(Test-Path $py64)) { throw "Python311 missing: $py64" }
  if (!(Test-Path $productionDb)) { throw "production DB missing: $productionDb" }

  $env:HORSE_RACING_DB_PATH = $productionDb
  "RUNNING $(Get-Date -Format o) step=holdout_evaluation" | Set-Content -Encoding UTF8 $status
  Publish "HorseRacingAI automation running $stamp"

  Run-Step 'holdout_evaluation' $py64 @('scripts\evaluate_5year_featurehistory.py')

  "SUCCESS $(Get-Date -Format o) step=complete" | Set-Content -Encoding UTF8 $status
  Publish "HorseRacingAI automation success $stamp"
  exit 0
} catch {
  $msg = "FAILED $(Get-Date -Format o) step=automation error=$($_.Exception.Message)"
  $msg | Set-Content -Encoding UTF8 $status
  Log $msg
  try { Publish "HorseRacingAI automation failure $stamp" } catch { Log "FAILED_TO_PUBLISH $($_.Exception.Message)" }
  exit 1
}
