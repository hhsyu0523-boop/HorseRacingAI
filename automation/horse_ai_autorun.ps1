$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$root = (Resolve-Path $root).Path
$git = (Get-Command git.exe -ErrorAction Stop).Source
$py64 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
$productionDb = Join-Path $root 'database\horse_racing.db'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$autoRoot = Join-Path $env:LOCALAPPDATA ("HorseRacingAI_AutomationWorktree_" + $stamp)
$rootLogDir = Join-Path $root 'outputs\automation'
New-Item -ItemType Directory -Force -Path $rootLogDir | Out-Null
$rootStatus = Join-Path $rootLogDir 'LATEST_LOCAL_STATUS.txt'
$rootLog = Join-Path $rootLogDir ("bootstrap_$stamp.log")
$worktreeReady = $false

function Root-Log([string]$text) {
  $text | Tee-Object -FilePath $rootLog -Append
}

# Windows PowerShell can turn a native program's stderr into ErrorRecord objects.
# Git writes normal progress (for example "From https://...") to stderr, so with
# ErrorActionPreference=Stop a successful git command could abort the runner.
# Native commands are therefore executed with Continue and judged only by exit code.
function Invoke-Native([string]$exe, [string[]]$argv, [string]$cwd=$root, [string[]]$logFiles=@()) {
  Push-Location $cwd
  try {
    $saved=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    $out=@(& $exe @argv 2>&1)
    $code=$LASTEXITCODE
    $ErrorActionPreference=$saved
    foreach ($line in $out) {
      $s=$line.ToString()
      foreach ($f in $logFiles) { $s | Add-Content -Encoding UTF8 $f }
    }
    return [pscustomobject]@{ ExitCode=$code; Output=$out }
  } finally {
    $ErrorActionPreference=$saved
    Pop-Location
  }
}

function Invoke-Git([string[]]$argv, [string]$cwd=$root) {
  Root-Log "[$(Get-Date -Format o)] git $($argv -join ' ')"
  $r=Invoke-Native $git $argv $cwd @($rootLog)
  if ($r.ExitCode -ne 0) { throw "git $($argv -join ' ') failed exit=$($r.ExitCode)" }
  return $r
}

try {
  "RUNNING $(Get-Date -Format o) step=bootstrap" | Set-Content -Encoding UTF8 $rootStatus

  # Never pull/rebase/stash/reset the user's research worktree.
  Invoke-Git @('fetch','origin','main') | Out-Null
  Invoke-Git @('worktree','prune') | Out-Null
  Invoke-Git @('worktree','add','--force','--detach',$autoRoot,'origin/main') | Out-Null
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
    $r=Invoke-Native $exe $argv $autoRoot @($log,$rootLog)
    if ($r.ExitCode -ne 0) { throw "$name failed exit=$($r.ExitCode)" }
    Log "[$(Get-Date -Format o)] OK $name"
  }
  function Publish([string]$message) {
    # Automation status/logs always exist and must always be published.
    $stage=@('add','-f','outputs/automation/*.log','outputs/automation/LATEST_STATUS.txt')

    # Baseline artifacts are optional. Do not pass unmatched pathspecs to git add,
    # because git returns exit 128 when a wildcard matches no files.
    $baselineDir=Join-Path $autoRoot 'outputs\baseline'
    if (Test-Path $baselineDir) {
      if (Get-ChildItem -Path $baselineDir -Filter '*.json' -File -ErrorAction SilentlyContinue | Select-Object -First 1) {
        $stage += 'outputs/baseline/*.json'
      }
      if (Get-ChildItem -Path $baselineDir -Filter '*.txt' -File -ErrorAction SilentlyContinue | Select-Object -First 1) {
        $stage += 'outputs/baseline/*.txt'
      }
    }

    $r=Invoke-Native $git $stage $autoRoot @($log,$rootLog)
    if ($r.ExitCode -ne 0) { throw "git add failed exit=$($r.ExitCode)" }
    $r=Invoke-Native $git @('diff','--cached','--quiet') $autoRoot @($log,$rootLog)
    if ($r.ExitCode -eq 0) { return }
    if ($r.ExitCode -ne 1) { throw "git diff failed exit=$($r.ExitCode)" }
    $r=Invoke-Native $git @('-c','user.name=HorseRacingAI Automation','-c','user.email=actions@local','commit','-m',$message) $autoRoot @($log,$rootLog)
    if ($r.ExitCode -ne 0) { throw 'git commit failed' }
    $r=Invoke-Native $git @('push','origin','HEAD:main') $autoRoot @($log,$rootLog)
    if ($r.ExitCode -ne 0) {
      $r=Invoke-Native $git @('pull','--rebase','origin','main') $autoRoot @($log,$rootLog)
      if ($r.ExitCode -ne 0) { throw 'automation worktree rebase failed' }
      $r=Invoke-Native $git @('push','origin','HEAD:main') $autoRoot @($log,$rootLog)
      if ($r.ExitCode -ne 0) { throw 'git push failed after retry' }
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
      Root-Log 'Attempting remote failure publish'
      $r=Invoke-Native $git @('add','-f','outputs/automation/*.log','outputs/automation/LATEST_STATUS.txt') $autoRoot @($rootLog)
      if ($r.ExitCode -eq 0) {
        $r=Invoke-Native $git @('-c','user.name=HorseRacingAI Automation','-c','user.email=actions@local','commit','-m',"HorseRacingAI automation failure $stamp") $autoRoot @($rootLog)
        if ($r.ExitCode -eq 0) { Invoke-Native $git @('push','origin','HEAD:main') $autoRoot @($rootLog) | Out-Null }
      }
    } catch { Root-Log "FAILED_TO_PUBLISH $($_.Exception.Message)" }
  }
  exit 1
} finally {
  try {
    Set-Location $root
    if ($worktreeReady) { Invoke-Native $git @('worktree','remove','--force',$autoRoot) $root @($rootLog) | Out-Null }
    Invoke-Native $git @('worktree','prune') $root @($rootLog) | Out-Null
  } catch {}
}
