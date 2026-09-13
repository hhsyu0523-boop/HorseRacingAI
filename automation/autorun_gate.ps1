param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
Set-Location $Root
$git=(Get-Command git.exe -ErrorAction Stop).Source
$status=''
try {
  $status = (& $git show 'origin/main:outputs/automation/LATEST_STATUS.txt' 2>$null | Out-String).Trim()
} catch { $status='' }

$now=Get-Date
$today=[int]$now.DayOfWeek # Sunday=0
$mostRecentSunday=$now.Date.AddDays(-$today)
$weeklyTime=[TimeSpan]::Parse('20:30:00')

if ($today -eq 0) {
  if ($now.TimeOfDay -lt $weeklyTime) {
    Write-Output "SKIP before Sunday 20:30; now=$($now.ToString('o'))"
    exit 2
  }
  Write-Output "RUN Sunday weekly window; now=$($now.ToString('o'))"
  exit 0
}

if ([string]::IsNullOrWhiteSpace($status)) {
  Write-Output 'RUN recovery: no remote status exists'
  exit 0
}

$parts=$status -split '\s+'
$kind=$parts[0]
$stamp=$null
if ($parts.Count -ge 2) { [DateTimeOffset]::TryParse($parts[1],[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind,[ref]$stamp) | Out-Null }

if ($kind -eq 'SUCCESS' -and $stamp -ne $null -and $stamp.LocalDateTime.Date -ge $mostRecentSunday.Date) {
  Write-Output "SKIP: required Sunday SUCCESS already exists: $status"
  exit 2
}

Write-Output "RUN recovery: required Sunday SUCCESS missing; status=$status"
exit 0
