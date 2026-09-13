@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0enable_autorun_once.ps1"
if errorlevel 1 (
  echo.
  echo HorseRacingAI autorun repair FAILED.
  pause
  exit /b 1
)
echo.
echo HorseRacingAI autorun repair completed.
pause
