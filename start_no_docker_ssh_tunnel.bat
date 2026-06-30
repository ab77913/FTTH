@echo off
setlocal EnableExtensions EnableDelayedExpansion
title FTTH Local Pipeline - No Docker + SSH DB Tunnel
color 0A

REM ============================================================================
REM  FTTH local launcher WITHOUT Docker, using PostgreSQL through SSH tunnel
REM
REM  Use this when local PostgreSQL is failing or not installed.
REM
REM  What it runs locally on this laptop:
REM    - Python virtual environment
REM    - FastAPI app at http://127.0.0.1:8000
REM    - Pipeline fallback inside FastAPI if Celery/Redis are not available
REM
REM  What it connects to remotely:
REM    - PostgreSQL through SSH tunnel:
REM      127.0.0.1:15432 on this laptop -> 127.0.0.1:5432 on remote server
REM
REM  Docker: not used
REM  nginx : not used
REM ============================================================================

echo ============================================================
echo   FTTH Local Pipeline
echo   No Docker + PostgreSQL through SSH tunnel
echo ============================================================
echo.

REM ============================================================================
REM  SETTINGS
REM ============================================================================

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

set "VENV_DIR=%PROJECT_DIR%\.venv311"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"

set "APP_HOST=127.0.0.1"
set "APP_PORT=8000"

REM SSH server that can access PostgreSQL.
set "SSH_USER=Admin"
set "SSH_HOST=172.19.64.7"
set "SSH_PORT=22"

REM Leave blank for password login.
REM For key login, example:
REM set "SSH_KEY=C:\Users\Admin\.ssh\id_rsa"
set "SSH_KEY="

REM Remote PostgreSQL as seen from the SSH server.
set "REMOTE_DB_HOST=127.0.0.1"
set "REMOTE_DB_PORT=5432"

REM Local forwarded PostgreSQL port on this laptop.
set "LOCAL_DB_HOST=127.0.0.1"
set "LOCAL_DB_PORT=15432"

REM PostgreSQL credentials on the remote DB.
set "DB_NAME=ftth"
set "DB_USER=ftth"
set "DB_PASSWORD=ftth"

REM App connects only to local forwarded tunnel port.
set "DATABASE_URL=postgresql+psycopg2://%DB_USER%:%DB_PASSWORD%@%LOCAL_DB_HOST%:%LOCAL_DB_PORT%/%DB_NAME%"
set "POSTGIS_ENABLED=false"

REM No-Docker mode: Redis/Celery are optional. If not running, FastAPI fallback runs the pipeline.
set "REDIS_URL=redis://127.0.0.1:6379/0"
set "CELERY_BROKER_URL=redis://127.0.0.1:6379/1"
set "CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/2"
set "RABBITMQ_URL=amqp://ftth:ftth@127.0.0.1:5672/ftth"

set "PYTHONPATH=%PROJECT_DIR%"
set "LOG_LEVEL=INFO"
set "DEFAULT_CUSTOMER_ID=demo_customer"
set "DISPATCH_BATCH_SIZE=100"

REM Confidence gates.
set "AGENT0_CONFIDENCE_THRESHOLD=90"
set "AGENT1_CONFIDENCE_THRESHOLD=90"
set "AGENT2_CONFIDENCE_THRESHOLD=90"
set "AGENT3_CONFIDENCE_THRESHOLD=90"
set "AGENT5_CONFIDENCE_THRESHOLD=90"
set "AGENT6_CONFIDENCE_THRESHOLD=90"

REM Provider defaults. .env can override these.
set "USE_MOCK_PROVIDERS=false"
set "FTTH_AGENT1_PROVIDER_MODE=smarty_only"
set "FTTH_SSL_VERIFY=0"
set "FTTH_SKIP_NOMINATIM=1"
set "FTTH_ENABLE_PADDLE_OCR=0"
set "INSTALL_OCR=0"

REM ============================================================================
REM  LOAD .env, THEN FORCE SSH-TUNNEL DATABASE_URL
REM ============================================================================

cd /d "%PROJECT_DIR%"
if exist "%PROJECT_DIR%\.env" (
    echo [0/9] Loading .env...
    for /f "usebackq tokens=1,* delims==" %%A in ("%PROJECT_DIR%\.env") do (
        set "ENV_KEY=%%A"
        set "ENV_VAL=%%B"
        if not "!ENV_KEY!"=="" (
            if not "!ENV_KEY:~0,1!"=="#" (
                set "!ENV_KEY!=!ENV_VAL!"
            )
        )
    )
    set "DATABASE_URL=postgresql+psycopg2://%DB_USER%:%DB_PASSWORD%@%LOCAL_DB_HOST%:%LOCAL_DB_PORT%/%DB_NAME%"
    set "POSTGIS_ENABLED=false"
    echo        .env loaded. DATABASE_URL forced to SSH tunnel.
) else (
    echo [0/9] No .env found. Using values from this file.
)
echo.

REM ============================================================================
REM  CHECK TOOLS
REM ============================================================================

echo [1/9] Checking required tools...
where ssh >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: OpenSSH Client is not installed or not on PATH.
    echo   Install it from Windows Settings ^> Apps ^> Optional Features ^> OpenSSH Client.
    echo.
    pause
    exit /b 1
)

where py >nul 2>&1
if errorlevel 1 (
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   ERROR: Python was not found.
        echo   Install Python 3.11 and enable "Add python.exe to PATH".
        echo.
        pause
        exit /b 1
    )
)
echo        Tools found.
echo.

REM ============================================================================
REM  CREATE VENV + INSTALL DEPENDENCIES
REM ============================================================================

echo [2/9] Preparing Python virtual environment...
if not exist "%PYTHON%" (
    echo        Creating venv: %VENV_DIR%
    py -3.11 -m venv "%VENV_DIR%" >nul 2>&1
    if errorlevel 1 (
        python -m venv "%VENV_DIR%"
    )
)
if not exist "%PYTHON%" (
    echo.
    echo   ERROR: Could not create virtual environment.
    echo.
    pause
    exit /b 1
)

echo        Installing/updating dependencies...
"%PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto DEP_FAIL
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto DEP_FAIL

if "%INSTALL_OCR%"=="1" (
    if exist requirements-ocr.txt (
        echo        Installing optional PaddleOCR dependencies...
        "%PYTHON%" -m pip install -r requirements-ocr.txt
        if errorlevel 1 (
            echo        WARNING: PaddleOCR install failed. Continuing without PaddleOCR.
            set "FTTH_ENABLE_PADDLE_OCR=0"
        ) else (
            set "FTTH_ENABLE_PADDLE_OCR=1"
        )
    )
)
echo        Python environment ready.
echo.
goto DEPS_READY

:DEP_FAIL
echo.
echo   ERROR: Dependency installation failed. Check pip output above.
echo.
pause
exit /b 1

:DEPS_READY

REM ============================================================================
REM  STOP OLD LOCAL PROCESSES
REM ============================================================================

echo [3/9] Stopping old local FastAPI and SSH tunnel ports...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$project = (Resolve-Path '%PROJECT_DIR%').Path; " ^
  "Get-NetTCPConnection -LocalPort %APP_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; " ^
  "Get-NetTCPConnection -LocalPort %LOCAL_DB_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; " ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*api_server.py*' -and $_.CommandLine -like ('*' + $project + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo        Done.
echo.

REM ============================================================================
REM  START SSH TUNNEL
REM ============================================================================

echo [4/9] Opening SSH tunnel...
echo        SSH     : %SSH_USER%@%SSH_HOST%:%SSH_PORT%
echo        Forward : %LOCAL_DB_HOST%:%LOCAL_DB_PORT%  -^>  %REMOTE_DB_HOST%:%REMOTE_DB_PORT%
echo.
echo        IMPORTANT: If asked for password, enter password in the new SSH tunnel window.
echo        Keep the SSH tunnel window open while testing.
echo.

set "SSH_KEY_OPT="
if not "%SSH_KEY%"=="" (
    set SSH_KEY_OPT=-i "%SSH_KEY%"
)

start "FTTH PostgreSQL SSH Tunnel" cmd /k "ssh -N -L %LOCAL_DB_HOST%:%LOCAL_DB_PORT%:%REMOTE_DB_HOST%:%REMOTE_DB_PORT% -p %SSH_PORT% -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 %SSH_KEY_OPT% %SSH_USER%@%SSH_HOST%"

echo        Waiting for local tunnel port %LOCAL_DB_PORT%...
set /a TUNNEL_WAIT=0
:WAIT_TUNNEL
timeout /t 2 /nobreak >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "if (Test-NetConnection %LOCAL_DB_HOST% -Port %LOCAL_DB_PORT% -InformationLevel Quiet) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    set /a TUNNEL_WAIT+=1
    if %TUNNEL_WAIT% GEQ 30 (
        echo.
        echo   ERROR: SSH tunnel did not open after 60 seconds.
        echo   Check the separate SSH tunnel window for password/key/network errors.
        echo.
        pause
        exit /b 1
    )
    goto WAIT_TUNNEL
)
echo        SSH tunnel is ready.
echo.

REM ============================================================================
REM  VERIFY POSTGRESQL THROUGH TUNNEL
REM ============================================================================

echo [5/9] Verifying PostgreSQL through SSH tunnel...
"%PYTHON%" -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True); c=e.connect(); c.execute(text('select 1')); c.close(); print('       PostgreSQL tunnel connection: OK')"
if errorlevel 1 (
    echo.
    echo   ERROR: PostgreSQL connection through SSH tunnel failed.
    echo.
    echo   Check:
    echo     SSH_HOST=%SSH_HOST%
    echo     REMOTE_DB_HOST=%REMOTE_DB_HOST%
    echo     REMOTE_DB_PORT=%REMOTE_DB_PORT%
    echo     LOCAL_DB_PORT=%LOCAL_DB_PORT%
    echo     DB_NAME=%DB_NAME%
    echo     DB_USER=%DB_USER%
    echo.
    pause
    exit /b 1
)
echo.

REM ============================================================================
REM  INITIALIZE DB SCHEMA
REM ============================================================================

echo [6/9] Initializing database schema through tunnel...
"%PYTHON%" -c "from data_ingestion.database.db import init_db; init_db(); print('       DB schema ready')"
if errorlevel 1 (
    echo.
    echo   ERROR: DB initialization failed.
    echo   If this says PostGIS is missing, keep POSTGIS_ENABLED=false.
    echo.
    pause
    exit /b 1
)
echo.

REM ============================================================================
REM  OPTIONAL REDIS CHECK
REM ============================================================================

echo [7/9] Checking optional Redis/Celery support...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "if (Test-NetConnection 127.0.0.1 -Port 6379 -InformationLevel Quiet) { Write-Host '       Redis detected on 127.0.0.1:6379. Celery can be used if a worker is running.' } else { Write-Host '       Redis not detected. FastAPI fallback mode will run the pipeline.' }"
echo.

REM ============================================================================
REM  START FASTAPI
REM ============================================================================

echo [8/9] Starting FastAPI locally, no Docker, no nginx...
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"
start "FTTH FastAPI SSH Tunnel" /MIN cmd /k ""%PYTHON%" api_server.py 1>logs\api_server.log 2>logs\api_server_err.log"

echo        Waiting for API at http://%APP_HOST%:%APP_PORT% ...
set /a API_WAIT=0
:WAIT_API
timeout /t 2 /nobreak >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Invoke-WebRequest 'http://%APP_HOST%:%APP_PORT%/' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    set /a API_WAIT+=1
    if %API_WAIT% GEQ 30 (
        echo.
        echo   WARNING: API did not respond after 60 seconds.
        echo   Check:
        echo     logs\api_server.log
        echo     logs\api_server_err.log
        echo.
        goto DONE
    )
    goto WAIT_API
)
echo        FastAPI is ready.
echo.

:DONE
echo [9/9] Local app ready.
echo ============================================================
echo   App URL            : http://%APP_HOST%:%APP_PORT%
echo   DB via SSH tunnel  : %LOCAL_DB_HOST%:%LOCAL_DB_PORT% -^> %REMOTE_DB_HOST%:%REMOTE_DB_PORT%
echo   SSH target         : %SSH_USER%@%SSH_HOST%:%SSH_PORT%
echo   Docker             : not used
echo   nginx              : not used
echo   Pipeline           : FastAPI fallback if Celery/Redis absent
echo.
echo   Logs:
echo     API stdout       : logs\api_server.log
echo     API stderr       : logs\api_server_err.log
echo.
echo   Keep the SSH tunnel window open.
echo ============================================================
echo.
start http://%APP_HOST%:%APP_PORT%

echo Press any key in this window to stop local FastAPI and tunnel, then exit.
pause >nul

echo.
echo Stopping local FastAPI and SSH tunnel...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$project = (Resolve-Path '%PROJECT_DIR%').Path; " ^
  "Get-NetTCPConnection -LocalPort %APP_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; " ^
  "Get-NetTCPConnection -LocalPort %LOCAL_DB_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; " ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*api_server.py*' -and $_.CommandLine -like ('*' + $project + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Done.
timeout /t 2 /nobreak >nul
endlocal
