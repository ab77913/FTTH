@echo off
setlocal EnableExtensions EnableDelayedExpansion
title FTTH Localhost Full Pipeline - SSH DB Tunnel
color 0A

REM ============================================================================
REM  FTTH localhost launcher for the full pipeline
REM
REM  Runs on this laptop:
REM    - Python virtual environment
REM    - FastAPI app at http://localhost:8000
REM    - Celery pipeline worker if Redis is already available on localhost:6379
REM    - FastAPI in-process fallback if Redis/Celery are not available
REM
REM  Connects to PostgreSQL through SSH tunnel:
REM    127.0.0.1:15432 on this laptop -> 127.0.0.1:5432 on remote server
REM
REM  Docker: not required
REM  nginx : not used
REM ============================================================================

echo ============================================================
echo   FTTH Localhost Full Pipeline
echo   FastAPI + SSH PostgreSQL tunnel + optional Celery worker
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
set "APP_URL=http://localhost:%APP_PORT%"

REM SSH server that can access PostgreSQL.
REM Default is direct localhost DB. Set USE_SSH_TUNNEL=1 if you need the
REM remote DB tunnel instead of local PostgreSQL.
set "USE_SSH_TUNNEL=0"
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
set "DB_HOST=127.0.0.1"
set "DB_PORT=5432"
set "DB_NAME=ftth"
set "DB_USER=ftth"
set "DB_PASSWORD=ftth"

REM App connects to local PostgreSQL by default.
set "DATABASE_URL=postgresql+psycopg2://%DB_USER%:%DB_PASSWORD%@%DB_HOST%:%DB_PORT%/%DB_NAME%"
set "POSTGIS_ENABLED=false"

REM Redis/Celery are optional. If Redis is not running, FastAPI runs the full
REM pipeline in-process when you click Process.
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
set "FTTH_ENABLE_PADDLE_OCR=1"
set "FTTH_ENABLE_PADDLEOCR_SCAN=1"
set "INSTALL_OCR=1"

REM ============================================================================
REM  LOAD .env, THEN FORCE SELECTED LOCALHOST DATABASE_URL
REM ============================================================================

cd /d "%PROJECT_DIR%"
if exist "%PROJECT_DIR%\.env" (
    echo [0/10] Loading .env...
    set "ENV_DATABASE_URL="
    for /f "usebackq tokens=1,* delims==" %%A in ("%PROJECT_DIR%\.env") do (
        set "ENV_KEY=%%A"
        set "ENV_VAL=%%B"
        if not "!ENV_KEY!"=="" (
            if not "!ENV_KEY:~0,1!"=="#" (
                set "!ENV_KEY!=!ENV_VAL!"
                if /I "!ENV_KEY!"=="DATABASE_URL" set "ENV_DATABASE_URL=!ENV_VAL!"
            )
        )
    )
    if "!USE_SSH_TUNNEL!"=="1" (
        set "DATABASE_URL=postgresql+psycopg2://!DB_USER!:!DB_PASSWORD!@!LOCAL_DB_HOST!:!LOCAL_DB_PORT!/!DB_NAME!"
        echo        .env loaded. DATABASE_URL forced to SSH tunnel.
    ) else if not "!ENV_DATABASE_URL!"=="" (
        set "DATABASE_URL=!ENV_DATABASE_URL!"
        echo        .env loaded. DATABASE_URL preserved from .env.
    ) else (
        set "DATABASE_URL=postgresql+psycopg2://!DB_USER!:!DB_PASSWORD!@!DB_HOST!:!DB_PORT!/!DB_NAME!"
        echo        .env loaded. DATABASE_URL forced to local PostgreSQL.
    )
    set "POSTGIS_ENABLED=false"
) else (
    echo [0/10] No .env found. Using values from this file.
)
echo.

REM ============================================================================
REM  CHECK TOOLS
REM ============================================================================

echo [1/10] Checking required tools...
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

echo [2/10] Preparing Python virtual environment...
if not exist "%PYTHON%" (
    echo        Creating venv: %VENV_DIR%
    py -3.12 -m venv "%VENV_DIR%" >nul 2>&1
    if errorlevel 1 (
        py -3.11 -m venv "%VENV_DIR%" >nul 2>&1
    )
    if errorlevel 1 (
        python -m venv "%VENV_DIR%"
    )
)
if not exist "%PYTHON%" (
    echo.
    echo   ERROR: Could not create virtual environment.
    echo   Install Python 3.12 or 3.11 and ensure py.exe is on PATH.
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
        echo        Installing Agent 5 PaddleOCR dependencies...
        "%PYTHON%" -m pip install -r requirements-ocr.txt
        if errorlevel 1 (
            echo        WARNING: PaddleOCR install failed. Agent 5 will use Azure OCR fallback.
            set "FTTH_ENABLE_PADDLE_OCR=0"
        ) else (
            set "FTTH_ENABLE_PADDLE_OCR=1"
            set "FTTH_ENABLE_PADDLEOCR_SCAN=1"
        )
    )
)
"%PYTHON%" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('paddleocr') and importlib.util.find_spec('paddle') else 1)" >nul 2>&1
if errorlevel 1 (
    echo        PaddleOCR runtime not detected; Agent 5 will use Azure Vision OCR fallback.
    set "FTTH_ENABLE_PADDLE_OCR=0"
) else (
    echo        PaddleOCR runtime detected - Agent 5 scan column will use real Paddle reads.
    set "FTTH_ENABLE_PADDLE_OCR=1"
    set "FTTH_ENABLE_PADDLEOCR_SCAN=1"
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

echo [3/10] Stopping old local FastAPI, Celery, and SSH tunnel ports...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$project = (Resolve-Path '%PROJECT_DIR%').Path; " ^
  "Get-NetTCPConnection -LocalPort %APP_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; " ^
  "Get-NetTCPConnection -LocalPort %LOCAL_DB_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; " ^
  "Get-CimInstance Win32_Process | Where-Object { (($_.CommandLine -like '*api_server.py*') -or ($_.CommandLine -like '*celery_worker.py*') -or ($_.CommandLine -like '*data_ingestion.worker.celery_app*')) -and $_.CommandLine -like ('*' + $project + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo        Done.
echo.

REM ============================================================================
REM  START SSH TUNNEL
REM ============================================================================

if not "%USE_SSH_TUNNEL%"=="1" goto SKIP_SSH_TUNNEL

echo [4/10] Opening SSH tunnel...
echo        SSH     : %SSH_USER%@%SSH_HOST%:%SSH_PORT%
echo        Forward : %LOCAL_DB_HOST%:%LOCAL_DB_PORT%  -^>  %REMOTE_DB_HOST%:%REMOTE_DB_PORT%
echo.
echo        IMPORTANT: If asked for password, enter it in the new SSH tunnel window.
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
goto DB_VERIFY

:SKIP_SSH_TUNNEL
echo [4/10] SSH tunnel disabled.
echo        Using local PostgreSQL at %DB_HOST%:%DB_PORT%.
echo        To use the remote DB tunnel, set USE_SSH_TUNNEL=1 in this file.
echo.

REM ============================================================================
REM  VERIFY POSTGRESQL
REM ============================================================================

:DB_VERIFY
echo [5/10] Verifying PostgreSQL connection...
"%PYTHON%" -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True); c=e.connect(); c.execute(text('select 1')); c.close(); print('       PostgreSQL connection: OK')"
if errorlevel 1 (
    if not "%USE_SSH_TUNNEL%"=="1" goto LOCAL_DB_RETRY
    echo.
    echo   ERROR: PostgreSQL connection failed.
    echo.
    if "%USE_SSH_TUNNEL%"=="1" (
        echo   SSH tunnel settings:
        echo     SSH_HOST=%SSH_HOST%
        echo     REMOTE_DB_HOST=%REMOTE_DB_HOST%
        echo     REMOTE_DB_PORT=%REMOTE_DB_PORT%
        echo     LOCAL_DB_PORT=%LOCAL_DB_PORT%
    ) else (
        echo   Local DB settings:
        echo     DB_HOST=%DB_HOST%
        echo     DB_PORT=%DB_PORT%
    )
    echo     DB_NAME=%DB_NAME%
    echo     DB_USER=%DB_USER%
    echo.
    pause
    exit /b 1
)
echo.
goto DB_READY

:LOCAL_DB_RETRY
echo.
echo   WARNING: Local PostgreSQL connection failed with the current DATABASE_URL.
echo   Most common cause: the local PostgreSQL password for user "%DB_USER%" is different.
echo.
echo   Enter local PostgreSQL credentials to retry.
echo   Press Enter at a prompt to keep the value shown in brackets.
echo.
set "DB_USER_IN="
set "DB_NAME_IN="
set "DB_PASSWORD_IN="
set /p "DB_USER_IN=DB user [%DB_USER%]: "
if not "%DB_USER_IN%"=="" set "DB_USER=%DB_USER_IN%"
set /p "DB_NAME_IN=DB name [%DB_NAME%]: "
if not "%DB_NAME_IN%"=="" set "DB_NAME=%DB_NAME_IN%"
set /p "DB_PASSWORD_IN=DB password for %DB_USER%: "
if not "%DB_PASSWORD_IN%"=="" set "DB_PASSWORD=%DB_PASSWORD_IN%"
set "DATABASE_URL=postgresql+psycopg2://%DB_USER%:%DB_PASSWORD%@%DB_HOST%:%DB_PORT%/%DB_NAME%"
echo.
echo        Retrying local PostgreSQL at %DB_HOST%:%DB_PORT%/%DB_NAME% as %DB_USER%...
"%PYTHON%" -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True); c=e.connect(); c.execute(text('select 1')); c.close(); print('       PostgreSQL connection: OK')"
if errorlevel 1 (
    echo.
    echo   ERROR: PostgreSQL connection still failed.
    echo.
    echo   Fix one of these and run again:
    echo     1. Update DATABASE_URL in .env with the correct local DB password.
    echo     2. Reset the local PostgreSQL ftth user password to ftth.
    echo     3. Set USE_SSH_TUNNEL=1 if you must use the remote DB tunnel.
    echo.
    pause
    exit /b 1
)
echo.

:DB_READY

REM ============================================================================
REM  INITIALIZE DB SCHEMA
REM ============================================================================

echo [6/10] Initializing database schema...
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
REM  REDIS / CELERY CHECK
REM ============================================================================

echo [7/10] Checking Redis for Celery worker...
set "START_CELERY=0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "if (Test-NetConnection 127.0.0.1 -Port 6379 -InformationLevel Quiet) { Write-Host '       Redis detected on 127.0.0.1:6379. Celery worker will be started.'; exit 0 } else { Write-Host '       Redis not detected. FastAPI fallback will run the full pipeline.'; exit 1 }"
if not errorlevel 1 set "START_CELERY=1"
echo.

REM ============================================================================
REM  START FASTAPI
REM ============================================================================

echo [8/10] Starting FastAPI at %APP_URL% ...
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"
start "FTTH FastAPI Localhost" /MIN cmd /k ""%PYTHON%" api_server.py 1>logs\api_server.log 2>logs\api_server_err.log"

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
        goto START_WORKER
    )
    goto WAIT_API
)
echo        FastAPI is ready.
echo.

REM ============================================================================
REM  START CELERY WORKER IF REDIS EXISTS
REM ============================================================================

:START_WORKER
echo [9/10] Starting pipeline worker when available...
if "%START_CELERY%"=="1" (
    start "FTTH Celery Full Pipeline Worker" /MIN cmd /k ""%PYTHON%" celery_worker.py 1>logs\celery_worker.log 2>logs\celery_worker_err.log"
    timeout /t 4 /nobreak >nul
    "%PYTHON%" -c "from data_ingestion.worker.celery_app import celery_app; r=celery_app.control.ping(timeout=2.0, limit=1); print('       Celery worker ping:', 'OK' if r else 'no response'); raise SystemExit(0 if r else 1)"
    if errorlevel 1 (
        echo        WARNING: Celery did not answer ping. FastAPI fallback can still run the full pipeline.
    )
) else (
    echo        Skipped Celery worker because Redis is not available.
    echo        Full pipeline will run inside FastAPI when you click Process.
)
echo.

REM ============================================================================
REM  DONE
REM ============================================================================

echo [10/10] Localhost full-pipeline app ready.
echo ============================================================
echo   App URL            : %APP_URL%
echo   API URL            : http://localhost:%APP_PORT%/docs
if "%USE_SSH_TUNNEL%"=="1" (
    echo   DB via SSH tunnel  : %LOCAL_DB_HOST%:%LOCAL_DB_PORT% -^> %REMOTE_DB_HOST%:%REMOTE_DB_PORT%
    echo   SSH target         : %SSH_USER%@%SSH_HOST%:%SSH_PORT%
) else (
    echo   Database           : %DB_HOST%:%DB_PORT%/%DB_NAME%
)
echo   Docker             : not required
echo   nginx              : not used
echo.
echo   Pipeline:
echo     A0 reverse geocode ^> A1 Smarty ^> A2 geocode ^> A3 parcel
echo     ^> A4 building ^> A5 Street View/OCR ^> A6 FTTH final for all rows
echo.
echo   Runtime:
if "%START_CELERY%"=="1" (
    echo     Celery worker    : started if ping succeeded
) else (
    echo     Celery worker    : skipped
)
echo     Fallback mode    : FastAPI runs full pipeline if Celery is absent
echo.
echo   Login:
echo     ftth_team / Meridian@2026
echo     admin     / Meridian@2026
echo.
echo   Logs:
echo     API stdout       : logs\api_server.log
echo     API stderr       : logs\api_server_err.log
echo     Celery stdout    : logs\celery_worker.log
echo     Celery stderr    : logs\celery_worker_err.log
echo.
echo   Keep the SSH tunnel window open.
echo ============================================================
echo.
start %APP_URL%

echo Press any key in this window to stop local FastAPI, Celery, and tunnel, then exit.
pause >nul

echo.
echo Stopping local FastAPI, Celery, and SSH tunnel...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$project = (Resolve-Path '%PROJECT_DIR%').Path; " ^
  "Get-NetTCPConnection -LocalPort %APP_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; " ^
  "Get-NetTCPConnection -LocalPort %LOCAL_DB_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; " ^
  "Get-CimInstance Win32_Process | Where-Object { (($_.CommandLine -like '*api_server.py*') -or ($_.CommandLine -like '*celery_worker.py*') -or ($_.CommandLine -like '*data_ingestion.worker.celery_app*')) -and $_.CommandLine -like ('*' + $project + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Done.
timeout /t 2 /nobreak >nul
endlocal
