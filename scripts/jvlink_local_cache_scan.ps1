$ErrorActionPreference = 'Continue'

Write-Host '=== HorseRacingAI JV-Link local cache scan ==='
Write-Host ('Time: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Host 'READ-ONLY: no files, registry keys, or databases are modified.'
Write-Host ''

$roots = @(
    'C:\ProgramData\JRA-VAN',
    'C:\Program Files (x86)\JRA-VAN',
    'C:\Program Files\JRA-VAN'
) | Where-Object { Test-Path $_ }

if (-not $roots) {
    Write-Host 'No JRA-VAN root folders were found.'
    exit 2
}

foreach ($root in $roots) {
    Write-Host "## ROOT: $root"
    try {
        $files = Get-ChildItem -LiteralPath $root -Recurse -Force -File -ErrorAction SilentlyContinue
        $jvd = @($files | Where-Object { $_.Extension -ieq '.jvd' })
        $zero = @($jvd | Where-Object { $_.Length -eq 0 })
        $tiny = @($jvd | Where-Object { $_.Length -gt 0 -and $_.Length -lt 128 })

        Write-Host ("all_files=" + $files.Count)
        Write-Host ("jvd_files=" + $jvd.Count)
        Write-Host ("zero_byte_jvd=" + $zero.Count)
        Write-Host ("tiny_jvd_lt128=" + $tiny.Count)

        if ($jvd.Count -gt 0) {
            $oldest = $jvd | Sort-Object LastWriteTime | Select-Object -First 1
            $newest = $jvd | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            Write-Host ("oldest_jvd=" + $oldest.FullName + " | " + $oldest.Length + " bytes | " + $oldest.LastWriteTime)
            Write-Host ("newest_jvd=" + $newest.FullName + " | " + $newest.Length + " bytes | " + $newest.LastWriteTime)
        }

        if ($zero.Count -gt 0) {
            Write-Host '-- ZERO-BYTE JVD FILES --'
            $zero | Sort-Object FullName | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize
        }

        if ($tiny.Count -gt 0) {
            Write-Host '-- TINY JVD FILES (<128 bytes) --'
            $tiny | Sort-Object FullName | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize
        }

        Write-Host '-- Most recently modified JVD files (top 20) --'
        $jvd | Sort-Object LastWriteTime -Descending | Select-Object -First 20 FullName,Length,LastWriteTime | Format-Table -AutoSize
    } catch {
        Write-Host ("ERROR scanning root: " + $_.Exception.Message)
    }
    Write-Host ''
}

Write-Host '=== END CACHE SCAN ==='
