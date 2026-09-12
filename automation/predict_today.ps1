param(
  [string]$Date = (Get-Date -Format 'yyyyMMdd')
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$root = (Resolve-Path $root).Path

$py64 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
if (!(Test-Path $py64)) { $py64 = (Get-Command python.exe -ErrorAction Stop).Source }
$py32 = Join-Path $root '.runtime_python312_x86\python.exe'
if (!(Test-Path $py32)) { throw "32-bit JV-Link Python not found: $py32" }

$outDir = Join-Path $root 'outputs\predictions'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$outFile = Join-Path $outDir ("prediction_$Date.txt")
$statusFile = Join-Path $outDir 'LATEST_PREDICTION_STATUS.txt'

function Run-Python([string]$Python, [string[]]$Args, [switch]$AllowEmpty) {
  Push-Location $root
  try {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $lines = @(& $Python @Args 2>&1)
    $code = $LASTEXITCODE
    $ErrorActionPreference = $saved
    if ($code -ne 0) { throw "$Python $($Args -join ' ') failed exit=$code`n$($lines -join [Environment]::NewLine)" }
    if (!$AllowEmpty -and !$lines) { throw "$Python $($Args -join ' ') returned no output" }
    return ,$lines
  } finally {
    $ErrorActionPreference = $saved
    Pop-Location
  }
}

try {
  "RUNNING $(Get-Date -Format o) date=$Date step=jvlink-test" | Set-Content -Encoding UTF8 $statusFile
  $null = Run-Python $py32 @('main.py','test-connection')

  "RUNNING $(Get-Date -Format o) date=$Date step=current-week-fetch" | Set-Content -Encoding UTF8 $statusFile
  $fetchLines = Run-Python $py32 @('-m','scripts.fetch_current_day','--date',$Date)
  $raceKeys = @()
  foreach ($line in $fetchLines) {
    $m = [regex]::Match($line.ToString(), '\[(\d{12})\]')
    if ($m.Success) { $raceKeys += $m.Groups[1].Value }
  }
  $raceKeys = @($raceKeys | Sort-Object -Unique)
  if ($raceKeys.Count -eq 0) { throw "no current-week race keys found for $Date`n$($fetchLines -join [Environment]::NewLine)" }

  $header = @(
    'HorseRacingAI DAILY PREDICTION'
    "date=$Date"
    "generated_at=$(Get-Date -Format o)"
    "races=$($raceKeys.Count)"
    ''
  )
  $header | Set-Content -Encoding UTF8 $outFile
  foreach ($line in $fetchLines) { $line.ToString() | Add-Content -Encoding UTF8 $outFile }
  '' | Add-Content -Encoding UTF8 $outFile

  $ok = 0
  $failed = 0
  foreach ($raceKey in $raceKeys) {
    "RUNNING $(Get-Date -Format o) date=$Date step=predict race=$raceKey" | Set-Content -Encoding UTF8 $statusFile
    try {
      '========================' | Add-Content -Encoding UTF8 $outFile
      "RACE $raceKey" | Add-Content -Encoding UTF8 $outFile
      '========================' | Add-Content -Encoding UTF8 $outFile
      $pred = Run-Python $py64 @('main.py','predict-race','--race',$raceKey)
      foreach ($line in $pred) { $line.ToString() | Add-Content -Encoding UTF8 $outFile }
      '' | Add-Content -Encoding UTF8 $outFile
      $ok++
    } catch {
      "FAILED_RACE $raceKey error=$($_.Exception.Message)" | Add-Content -Encoding UTF8 $outFile
      '' | Add-Content -Encoding UTF8 $outFile
      $failed++
    }
  }

  "SUCCESS $(Get-Date -Format o) date=$Date races=$($raceKeys.Count) predicted=$ok failed=$failed file=$outFile" | Set-Content -Encoding UTF8 $statusFile

  $git = Get-Command git.exe -ErrorAction SilentlyContinue
  if ($git) {
    Push-Location $root
    try {
      & $git.Source add -f -- "outputs/predictions/prediction_$Date.txt" 'outputs/predictions/LATEST_PREDICTION_STATUS.txt'
      if ($LASTEXITCODE -eq 0) {
        & $git.Source diff --cached --quiet
        if ($LASTEXITCODE -eq 1) {
          & $git.Source -c user.name='HorseRacingAI Automation' -c user.email='actions@local' commit -m "HorseRacingAI daily prediction $Date"
          if ($LASTEXITCODE -eq 0) { & $git.Source push origin HEAD:main | Out-Null }
        }
      }
    } finally { Pop-Location }
  }

  Write-Output (Get-Content -Raw -Encoding UTF8 $statusFile)
  exit 0
} catch {
  $msg = "FAILED $(Get-Date -Format o) date=$Date step=daily_prediction error=$($_.Exception.Message)"
  $msg | Set-Content -Encoding UTF8 $statusFile
  Write-Error $msg
  exit 1
}
