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

function Run-Step([string]$name, [string]$exe, [string[]]$argv) {
  "[$(Get-Date -Format o)] START $name" | Tee-Object -FilePath $log -Append
  & $exe @argv 2>&1 | Tee-Object -FilePath $log -Append
  if ($LASTEXITCODE -ne 0) { throw "$name failed exit=$LASTEXITCODE" }
  "[$(Get-Date -Format o)] OK $name" | Tee-Object -FilePath $log -Append
}

try {
  if (!(Test-Path $py64)) { throw "Python311 missing: $py64" }
  if (!(Test-Path $py32)) { throw "JV-Link x86 Python missing: $py32" }
  "RUNNING $(Get-Date -Format o)" | Set-Content -Encoding UTF8 $status

  # Keep repository scripts current before executing. Local DB/runtime files are untouched.
  Run-Step 'git_fetch' 'git.exe' @('fetch','origin','main')
  Run-Step 'git_checkout_automation' 'git.exe' @('checkout','origin/main','--','automation','scripts')

  # Current validated baseline pipeline. JV-Link refresh is deliberately separate until
  # its incremental date-range command is fixed; do not redownload five years every run.
  Run-Step 'holdout_evaluation' $py64 @('scripts\evaluate_5year_featurehistory.py')

  # Persist small text/json outputs and logs so ChatGPT can inspect progress remotely.
  & git.exe add -f 'outputs/baseline/*.json' 'outputs/baseline/*.txt' 'outputs/automation/*.log' 'outputs/automation/LATEST_STATUS.txt' 2>&1 | Tee-Object -FilePath $log -Append
  & git.exe diff --cached --quiet
  if ($LASTEXITCODE -ne 0) {
    & git.exe -c user.name='HorseRacingAI Automation' -c user.email='actions@local' commit -m "HorseRacingAI automated evaluation $stamp" 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
    & git.exe push origin HEAD:main 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw 'git push failed' }
  }
  "SUCCESS $(Get-Date -Format o)" | Set-Content -Encoding UTF8 $status
  & git.exe add -f 'outputs/automation/LATEST_STATUS.txt'
  & git.exe diff --cached --quiet
  if ($LASTEXITCODE -ne 0) {
    & git.exe -c user.name='HorseRacingAI Automation' -c user.email='actions@local' commit -m "HorseRacingAI automation success $stamp" | Out-Null
    & git.exe push origin HEAD:main | Out-Null
  }
  exit 0
} catch {
  $msg = "FAILED $(Get-Date -Format o) $($_.Exception.Message)"
  $msg | Tee-Object -FilePath $log -Append | Set-Content -Encoding UTF8 $status
  try {
    & git.exe add -f 'outputs/automation/*.log' 'outputs/automation/LATEST_STATUS.txt'
    & git.exe -c user.name='HorseRacingAI Automation' -c user.email='actions@local' commit -m "HorseRacingAI automation failure $stamp" | Out-Null
    & git.exe push origin HEAD:main | Out-Null
  } catch {}
  exit 1
}
