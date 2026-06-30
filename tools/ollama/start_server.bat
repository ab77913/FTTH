@echo off
setlocal
cd /d "%~dp0"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Requesting Administrator permission so Windows Firewall can allow office laptops...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
  exit /b
)

echo Starting Ollama office chat server...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_office_server.ps1"

echo.
echo Launcher stopped.
pause
