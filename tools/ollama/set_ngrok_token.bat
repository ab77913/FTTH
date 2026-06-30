@echo off
setlocal
cd /d "%~dp0"

set /p "NGROK_TOKEN=Paste your ngrok authtoken: "
if "%NGROK_TOKEN%"=="" (
  echo No token entered.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ngrok = Get-Command ngrok -ErrorAction SilentlyContinue; $ngrokPath = if ($ngrok) { $ngrok.Source } else { $null }; if (-not $ngrokPath) { $found = Get-ChildItem \"$env:LOCALAPPDATA\Microsoft\WinGet\Packages\" -Recurse -Filter ngrok.exe -ErrorAction SilentlyContinue | Select-Object -First 1; if ($found) { $ngrokPath = $found.FullName } }; if (-not $ngrokPath) { winget install --id Ngrok.Ngrok --exact --accept-source-agreements --accept-package-agreements; $found = Get-ChildItem \"$env:LOCALAPPDATA\Microsoft\WinGet\Packages\" -Recurse -Filter ngrok.exe -ErrorAction SilentlyContinue | Select-Object -First 1; if ($found) { $ngrokPath = $found.FullName } }; if (-not $ngrokPath) { throw 'ngrok.exe was not found' }; & $ngrokPath config add-authtoken $env:NGROK_TOKEN"

echo.
echo Done. Now run start_server.bat to launch the public URL.
pause
