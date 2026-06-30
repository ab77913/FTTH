@echo off
setlocal
cd /d "%~dp0"

echo Installing FTTH sidebar chat button...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_ftth_chat_button.ps1"

echo.
pause
