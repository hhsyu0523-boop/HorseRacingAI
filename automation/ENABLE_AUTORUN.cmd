@echo off
setlocal
cd /d "%~dp0.."
echo HorseRacingAI autorun recovery starting...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_autorun_once.ps1"
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo SUCCESS: autorun task was refreshed, started, and produced a local status.
) else (
  echo FAILED: recovery did not complete. See outputs\automation\enable_autorun_*.log
)
echo.
pause
exit /b %RC%
