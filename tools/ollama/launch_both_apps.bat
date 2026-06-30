@echo off
setlocal
cd /d "%~dp0"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Requesting Administrator permission to start both office applications...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
  exit /b
)

echo Starting FTTH and Ollama Chat together...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_both_apps.ps1"

echo.
echo Combined launcher stopped.
pause
