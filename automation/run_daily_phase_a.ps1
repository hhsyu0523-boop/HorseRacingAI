param(
  [Parameter(Mandatory=$true)][string]$DataRoot,
  [string]$Date = (Get-Date -Format 'yyyyMMdd'),
  [string]$Python64 = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
  [string]$JvPython = '',
  [string]$OutputRoot = ''
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$data = (Resolve-Path -LiteralPath $DataRoot).Path
if (!$JvPython) { $JvPython = Join-Path $data '.runtime_python312_x86\python.exe' }
if (!$OutputRoot) { $OutputRoot = Join-Path $repo '.daily_runtime' }
if (!(Test-Path -LiteralPath $Python64)) { throw '64-bit Python is missing' }
if (!(Test-Path -LiteralPath $JvPython)) { throw 'JV-Link Python is missing' }
Push-Location $repo
try {
  $branch = (& git branch --show-current).Trim()
  if ($LASTEXITCODE -ne 0 -or $branch -ne 'codex/productionize-daily-20260919') {
    throw 'This pre-approval runner is restricted to codex/productionize-daily-20260919'
  }
  & $Python64 -m scripts.daily_operation --date $Date --jv-python $JvPython `
    --models (Join-Path $data 'models') --history-db (Join-Path $data 'database\horse_racing.db') `
    --config (Join-Path $repo 'config\ensemble.json') --output $OutputRoot
  $result = $LASTEXITCODE
} finally { Pop-Location }
exit $result
