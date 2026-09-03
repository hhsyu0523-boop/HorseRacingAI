$ErrorActionPreference = 'Continue'

Write-Host '=== HorseRacingAI JV-Link setup-state diagnostic ==='
Write-Host ('Time: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-Host 'READ-ONLY: no registry/file/database changes are performed.'
Write-Host ''

function Show-Key {
    param([string]$Path)
    if (Test-Path $Path) {
        Write-Host "--- $Path ---"
        try {
            Get-ItemProperty -LiteralPath $Path | Format-List *
        } catch {
            Write-Host ("ERROR reading key: " + $_.Exception.Message)
        }
    }
}

Write-Host '## Candidate registry keys'
$roots = @(
    'Registry::HKEY_CURRENT_USER\Software',
    'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node',
    'Registry::HKEY_LOCAL_MACHINE\SOFTWARE'
)
foreach ($root in $roots) {
    try {
        Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue |
            Where-Object { $_.PSChildName -match 'JRA|JV|DataLab' } |
            ForEach-Object { Show-Key $_.PSPath }
    } catch {}
}

Write-Host ''
Write-Host '## ProgID / CLSID registration'
$progids = @(
    'Registry::HKEY_CLASSES_ROOT\JVDTLab.JVLink',
    'Registry::HKEY_CLASSES_ROOT\WOW6432Node\JVDTLab.JVLink'
)
foreach ($p in $progids) {
    if (Test-Path $p) {
        Write-Host "--- $p ---"
        Get-Item -LiteralPath $p | Format-List *
        if (Test-Path "$p\CLSID") {
            $clsid = (Get-ItemProperty -LiteralPath "$p\CLSID").'(default)'
            if (-not $clsid) { $clsid = (Get-Item -LiteralPath "$p\CLSID").GetValue('') }
            Write-Host ("CLSID: " + $clsid)
            if ($clsid) {
                foreach ($cp in @(
                    "Registry::HKEY_CLASSES_ROOT\CLSID\$clsid",
                    "Registry::HKEY_CLASSES_ROOT\WOW6432Node\CLSID\$clsid"
                )) {
                    if (Test-Path $cp) {
                        Write-Host "--- $cp ---"
                        Get-ItemProperty -LiteralPath $cp -ErrorAction SilentlyContinue | Format-List *
                        if (Test-Path "$cp\InprocServer32") {
                            Write-Host "--- $cp\InprocServer32 ---"
                            Get-ItemProperty -LiteralPath "$cp\InprocServer32" -ErrorAction SilentlyContinue | Format-List *
                        }
                    }
                }
            }
        }
    }
}

Write-Host ''
Write-Host '## JRA-VAN local data folders'
$folders = @(
    'C:\ProgramData\JRA-VAN',
    'C:\ProgramData\JRA-VAN\Data',
    'C:\Program Files (x86)\JRA-VAN',
    'C:\Program Files\JRA-VAN'
)
foreach ($f in $folders) {
    if (Test-Path $f) {
        Write-Host "--- $f ---"
        try {
            Get-ChildItem -LiteralPath $f -Force -ErrorAction SilentlyContinue |
                Select-Object Name,Length,LastWriteTime,Mode |
                Format-Table -AutoSize
        } catch {
            Write-Host ("ERROR reading folder: " + $_.Exception.Message)
        }
    }
}

Write-Host ''
Write-Host '## JV-Link COM initialization only (NO JVOpen)'
$python = Join-Path $PSScriptRoot '..\.runtime_python312_x86\python.exe'
if (Test-Path $python) {
    $code = @'
import json
try:
    import win32com.client
    jv = win32com.client.Dispatch('JVDTLab.JVLink')
    rc = int(jv.JVInit('UNKNOWN'))
    print(json.dumps({'dispatch': 'OK', 'jvinit': rc}, ensure_ascii=False))
    try:
        jv.JVClose()
    except Exception:
        pass
except Exception as e:
    print(json.dumps({'dispatch': 'FAILED', 'error': repr(e)}, ensure_ascii=False))
'@
    & $python -c $code
} else {
    Write-Host ("32-bit Python not found: " + $python)
}

Write-Host ''
Write-Host '=== END DIAGNOSTIC ==='
